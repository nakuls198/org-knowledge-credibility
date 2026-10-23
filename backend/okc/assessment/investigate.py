"""Assess a claim: analyse -> gather evidence -> classify -> score -> explain.

`investigate()` is pure computation (it writes nothing) and is used by the API, the CLI, the answers
layer and the evaluation. Persistence lives in `okc.assessment.store`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from okc.claims.actors import Gazetteer
from okc.claims.analysis import ClaimAnalysis, analyze
from okc.credibility.engine import CredibilityEngine
from okc.credibility.explanation import build_explanation, llm_narrative
from okc.credibility.models import CredibilityResult, DocInfo, EvidenceItem, RelationEdge
from okc.db.models import Document, Relationship, Section
from okc.evidence.classifier import EvidenceClassifier
from okc.evidence.gather import Investigator
from okc.evidence.llm import get_llm_classifier
from okc.evidence.nli import get_nli
from okc.evidence.rules import RulesClassifier
from okc.search.embeddings import get_embedder
from okc.search.service import SearchService, get_search_service
from okc.settings import get_settings, load_config

ENGINE_VERSION = "okc-assessment-1.0"

_gazetteer_lock = threading.Lock()
_gazetteer_cache: dict[str, Gazetteer] = {}


def corpus_version(db: Session) -> str:
    """Changes whenever the active documents change; used to invalidate caches and stale answers."""
    n, latest = db.execute(select(func.count(Document.id), func.max(Document.updated_at))
                           .where(Document.is_deleted.is_(False))).one()
    return f"{n}:{latest}"


def get_gazetteer(db: Session) -> Gazetteer:
    """People and roles of the organisation, read from roster tables in the corpus (cached per corpus version)."""
    key = corpus_version(db)
    with _gazetteer_lock:
        if key not in _gazetteer_cache:
            rows = db.execute(select(Section.plain_text, Document.doc_type).join(Document)
                              .where(Section.is_deleted.is_(False), Section.plain_text.contains(" | "))).all()
            _gazetteer_cache.clear()
            _gazetteer_cache[key] = Gazetteer.from_sections(rows, load_config("entities"))
        return _gazetteer_cache[key]


def build_classifier(gazetteer: Gazetteer, mode: str | None = None) -> EvidenceClassifier:
    """Rules classifier plus whichever optional NLI / LLM components are enabled and available."""
    reasoning, retrieval = load_config("reasoning"), load_config("retrieval")
    rules = RulesClassifier(reasoning, gazetteer, retrieval.get("synonyms", {}))
    return EvidenceClassifier(rules, reasoning, nli=get_nli(), llm=get_llm_classifier(), mode=mode)


@dataclass
class Outcome:
    analysis: ClaimAnalysis
    items: list[EvidenceItem]
    result: CredibilityResult
    explanation: dict[str, Any]
    retrieval_log: dict[str, Any]
    config_snapshot: dict[str, Any]
    versions: dict[str, Any]
    docs: dict[str, DocInfo]
    edges: list[RelationEdge]


def document_infos(db: Session, ids: set[str]) -> dict[str, DocInfo]:
    return {d.id: DocInfo(id=d.id, title=d.title, doc_type=d.doc_type, status=d.status, owner=d.owner,
                          flags=list(d.flags or []), doc_date=d.doc_date, effective_date=d.effective_date,
                          superseded_date=d.superseded_date)
            for d in db.scalars(select(Document).where(Document.id.in_(ids)))}


def relation_edges(db: Session, ids: set[str]) -> list[RelationEdge]:
    rows = db.scalars(select(Relationship).where(Relationship.source_document_id.in_(ids),
                                                 Relationship.target_document_id.in_(ids))).all()
    return [RelationEdge(r.source_document_id, r.target_document_id, r.rel_type, r.origin, r.evidence, r.attributes or {})
            for r in rows]


def investigate(db: Session, text: str, *, classifier_mode: str | None = None, as_of: date | None = None,
                max_evidence: int | None = None, retrieval_mode: str | None = None, search: SearchService | None = None,
                narrative: bool | None = None, classifier: EvidenceClassifier | None = None) -> Outcome:
    settings = get_settings()
    retrieval_cfg, reasoning_cfg, credibility_cfg = (load_config(n) for n in ("retrieval", "reasoning", "credibility"))
    gazetteer = get_gazetteer(db)
    analysis = analyze(text, gazetteer)

    gathered = Investigator(db, search or get_search_service(), retrieval_cfg, gazetteer.non_tool_names).gather(
        analysis, mode=retrieval_mode, max_evidence=max_evidence)
    classifier = classifier or build_classifier(gazetteer, classifier_mode)
    judgements = classifier.classify_all(analysis, gathered.contexts)
    items = [EvidenceItem(ctx=c, judgement=j, retrieval=r)
             for c, j, r in zip(gathered.contexts, judgements, gathered.retrieval)]

    ids = {c.document_id for c in gathered.contexts}
    docs, edges = document_infos(db, ids), relation_edges(db, ids)
    engine = CredibilityEngine(credibility_cfg, embedder=get_embedder() if settings.embeddings_enabled else None)
    result = engine.assess(analysis, items, docs, edges, as_of=as_of)

    explanation = build_explanation(analysis, items, result)
    use_narrative = narrative if narrative is not None else (classifier.mode.startswith("llm") and settings.llm_provider == "ollama")
    if use_narrative:
        narrated = llm_narrative(explanation, items, settings.ollama_host, settings.llm_model)
        if narrated:
            explanation["llm_narrative"] = narrated
    gathered.log["classifier_notes"] = classifier.notes

    snapshot = {"retrieval": retrieval_cfg, "reasoning": reasoning_cfg, "credibility": credibility_cfg,
                "settings": {"search_backend": settings.search_backend, "embeddings": settings.embeddings_enabled,
                             "embedding_model": settings.embedding_model if settings.embeddings_enabled else None,
                             "nli_model": settings.nli_model if settings.nli_enabled else None,
                             "llm_model": settings.llm_model if settings.llm_provider != "none" else None}}
    versions = {"engine": ENGINE_VERSION, "credibility": credibility_cfg.get("version"), "classifier": classifier.version,
                "explanation": explanation["generated_by"], "corpus": corpus_version(db)}
    return Outcome(analysis, items, result, explanation, gathered.log, snapshot, versions, docs, edges)
