"""Claim assessment orchestration + persistence (audit trail).

investigate()  : pure computation (no writes) - used by the API, CLI and evaluation
run_assessment(): investigate + persist Assessment / AssessmentEvidence + update the claim
reproduce()    : re-score a stored assessment from its recorded evidence + config snapshot
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.claims.analysis import ClaimAnalysis, analyze
from app.claims.gazetteer import Gazetteer
from app.core.config import config_hash, get_settings, load_config
from app.credibility.engine import CredibilityEngine, CredibilityResult, DocInfo, Edge, EvidenceItem
from app.database.models import Assessment, AssessmentEvidence, Claim, Document, Relationship, Section
from app.explanations.builder import build_explanation, llm_narrative
from app.reasoning.classifier import EvidenceClassifier
from app.reasoning.llm import get_llm_classifier
from app.reasoning.nli import get_nli
from app.reasoning.rules import RulesClassifier
from app.reasoning.schemas import EvidenceContext, Judgement
from app.retrieval.embeddings import get_embedder
from app.retrieval.service import SearchService, get_search_service

log = logging.getLogger(__name__)
ENGINE_VERSION = "okc-assessment-1.0"

_gz_lock = threading.Lock()
_gz_cache: dict[str, Gazetteer] = {}


def corpus_version(db: Session) -> str:
    n, latest = db.execute(select(func.count(Document.id), func.max(Document.updated_at))
                           .where(Document.is_deleted.is_(False))).one()
    return f"{n}:{latest}"


def get_gazetteer(db: Session) -> Gazetteer:
    key = corpus_version(db)
    with _gz_lock:
        if key not in _gz_cache:
            rows = db.execute(select(Section.plain_text, Document.doc_type).join(Document)
                              .where(Section.is_deleted.is_(False), Section.plain_text.contains(" | "))).all()
            _gz_cache.clear()
            _gz_cache[key] = Gazetteer.from_sections(rows, load_config("entities"))
        return _gz_cache[key]


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
    edges: list[Edge]


def _doc_infos(db: Session, ids: set[str]) -> dict[str, DocInfo]:
    out = {}
    for d in db.scalars(select(Document).where(Document.id.in_(ids))):
        out[d.id] = DocInfo(id=d.id, title=d.title, doc_type=d.doc_type, status=d.status, owner=d.owner,
                            flags=list(d.flags or []), doc_date=d.doc_date, effective_date=d.effective_date,
                            superseded_date=d.superseded_date)
    return out


def _edges(db: Session, ids: set[str]) -> list[Edge]:
    rows = db.scalars(select(Relationship).where(Relationship.source_document_id.in_(ids),
                                                 Relationship.target_document_id.in_(ids))).all()
    return [Edge(r.source_document_id, r.target_document_id, r.rel_type, r.origin, r.evidence, r.attributes or {})
            for r in rows]


def investigate(db: Session, text: str, *, classifier_mode: str | None = None, as_of: date | None = None,
                max_evidence: int | None = None, retrieval_mode: str | None = None, search: SearchService | None = None,
                narrative: bool | None = None, classifier: EvidenceClassifier | None = None) -> Outcome:
    settings = get_settings()
    cfg_ret, cfg_rsn, cfg_cred = load_config("retrieval"), load_config("reasoning"), load_config("credibility")
    cfg_rsn = {**cfg_rsn, "_synonyms": cfg_ret.get("synonyms", {})}
    gz = get_gazetteer(db)
    analysis = analyze(text, gz)
    search = search or get_search_service()
    from app.assessments.investigation import Investigator

    gathered = Investigator(db, search, cfg_ret).gather(analysis, mode=retrieval_mode, max_evidence=max_evidence)
    if classifier is None:
        classifier = EvidenceClassifier(RulesClassifier(cfg_rsn, gz), cfg_rsn, nli=get_nli(), llm=get_llm_classifier(),
                                        mode=classifier_mode)
    judgements = classifier.classify_all(analysis, gathered.contexts)
    items = [EvidenceItem(ctx=c, judgement=j, retrieval=r)
             for c, j, r in zip(gathered.contexts, judgements, gathered.retrieval)]
    ids = {c.document_id for c in gathered.contexts}
    docs, edges = _doc_infos(db, ids), _edges(db, ids)
    engine = CredibilityEngine(cfg_cred, embedder=get_embedder() if settings.embeddings_enabled else None)
    result = engine.assess(analysis, items, docs, edges, as_of=as_of)
    explanation = build_explanation(analysis, items, result)
    use_narrative = narrative if narrative is not None else (classifier.mode.startswith("llm") and settings.llm_provider == "ollama")
    if use_narrative:
        nar = llm_narrative(explanation, items, settings.ollama_host, settings.llm_model)
        if nar:
            explanation["llm_narrative"] = nar
    gathered.log["classifier_notes"] = classifier.notes
    snapshot = {"retrieval": cfg_ret, "reasoning": cfg_rsn, "credibility": cfg_cred,
                "settings": {"search_backend": settings.search_backend, "embeddings": settings.embeddings_enabled,
                             "embedding_model": settings.embedding_model if settings.embeddings_enabled else None,
                             "nli_model": settings.nli_model if settings.nli_enabled else None,
                             "llm_model": settings.llm_model if settings.llm_provider != "none" else None}}
    versions = {"engine": ENGINE_VERSION, "credibility": cfg_cred.get("version"), "classifier": classifier.version,
                "explanation": explanation["generated_by"], "corpus": corpus_version(db)}
    return Outcome(analysis, items, result, explanation, gathered.log, snapshot, versions, docs, edges)


def persist_outcome(db: Session, claim: Claim, assessment: Assessment, out: Outcome) -> Assessment:
    r = out.result
    assessment.status = "completed"
    assessment.verdict = r.verdict
    assessment.score = r.score
    assessment.needs_review = r.needs_review
    assessment.result = r.model_dump(mode="json")
    assessment.explanation = out.explanation
    assessment.retrieval = {**out.retrieval_log, "claim_analysis": out.analysis.model_dump(mode="json"),
                            "edges_snapshot": [e.__dict__ for e in out.edges],
                            "documents_snapshot": {k: {**v.__dict__, "doc_date": str(v.doc_date) if v.doc_date else None,
                                                       "effective_date": str(v.effective_date) if v.effective_date else None,
                                                       "superseded_date": str(v.superseded_date) if v.superseded_date else None}
                                                   for k, v in out.docs.items()}}
    assessment.config_snapshot = out.config_snapshot
    assessment.config_hash = config_hash(out.config_snapshot)
    assessment.versions = out.versions
    assessment.completed_at = datetime.now(timezone.utc)
    assessment.evidence.clear()
    for rank, it in enumerate(out.items, start=1):
        c, j = it.ctx, it.judgement
        assessment.evidence.append(AssessmentEvidence(
            rank=rank, section_id=c.section_id, document_id=c.document_id,
            section_content_hash=it.retrieval.get("content_hash", ""), section_text=c.section_text,
            span_start=c.span_start, span_end=c.span_end, span_text=c.span, label=j.label, scope=j.scope,
            strength=j.strength, rationale=j.rationale,
            signals={**j.signals, "modality": j.modality, "directness": j.directness, "conditions": j.conditions,
                     "classifier": j.classifier,
                     "_context": {"doc_type": c.doc_type, "doc_kind": c.doc_kind, "status": c.status, "flags": c.flags,
                                  "doc_date": c.doc_date, "doc_people": c.doc_people, "source_path": c.source_path,
                                  "heading": c.heading, "section_role": c.section_role, "doc_title": c.doc_title,
                                  "start_line": c.start_line, "end_line": c.end_line}},
            features={**it.features, "weight": it.weight}, contribution=it.contribution, cluster_id=it.cluster_id,
            retrieval={k: v for k, v in it.retrieval.items() if k != "duplicates"},
            duplicates=it.retrieval.get("duplicates", []),
        ))
    claim.analysis = out.analysis.model_dump(mode="json")
    claim.latest_assessment_id = assessment.id
    claim.latest_verdict = r.verdict
    claim.latest_score = r.score
    claim.needs_review = r.needs_review
    claim.updated_at = datetime.now(timezone.utc)
    return assessment


def create_assessment(db: Session, claim: Claim, as_of: date | None) -> Assessment:
    a = Assessment(id=str(uuid.uuid4()), claim_id=claim.id, status="queued", as_of=as_of)
    db.add(a)
    db.flush()
    return a


def run_assessment(db: Session, assessment_id: str, *, classifier_mode: str | None = None,
                   max_evidence: int | None = None, retrieval_mode: str | None = None) -> Assessment:
    a = db.get(Assessment, assessment_id)
    claim = db.get(Claim, a.claim_id)
    a.status = "running"
    db.commit()
    try:
        out = investigate(db, claim.text, classifier_mode=classifier_mode, as_of=a.as_of, max_evidence=max_evidence,
                          retrieval_mode=retrieval_mode)
        persist_outcome(db, claim, a, out)
    except Exception as exc:  # noqa: BLE001 - surfaced on the assessment record
        db.rollback()
        log.exception("Assessment %s failed", assessment_id)
        a = db.get(Assessment, assessment_id)
        a.status, a.error = "failed", f"{type(exc).__name__}: {exc}"
        a.completed_at = datetime.now(timezone.utc)
    db.commit()
    return a


def reproduce(db: Session, assessment_id: str) -> dict[str, Any]:
    """Re-score a stored assessment from its recorded evidence judgements and config snapshot.

    Retrieval and classification are not re-run (their recorded outputs are inputs here), so
    this checks that the scoring step is deterministic given the audit record. It also reports
    whether any evidence section has changed since the assessment.
    """
    a = db.get(Assessment, assessment_id)
    if a is None or a.status != "completed":
        return {"reproducible": False, "reason": "assessment not found or not completed"}
    claim = db.get(Claim, a.claim_id)
    snap = (a.retrieval or {}).get("claim_analysis")
    analysis = ClaimAnalysis.model_validate(snap) if snap else analyze(claim.text, get_gazetteer(db))
    items: list[EvidenceItem] = []
    for ev in a.evidence:
        sig = dict(ev.signals or {})
        cx = sig.get("_context", {})
        ctx = EvidenceContext(
            section_id=ev.section_id, document_id=ev.document_id, doc_title=cx.get("doc_title", ""),
            doc_type=cx.get("doc_type", ""), doc_kind=cx.get("doc_kind", ""), status=cx.get("status", ""),
            flags=cx.get("flags", []), doc_date=cx.get("doc_date"), doc_people=cx.get("doc_people", []),
            heading=cx.get("heading", ""), section_role=cx.get("section_role", ""), source_path=cx.get("source_path", ""),
            start_line=cx.get("start_line", 0), end_line=cx.get("end_line", 0),
            section_text=ev.section_text, span_start=ev.span_start, span_end=ev.span_end)
        j = Judgement(label=ev.label, scope=ev.scope, strength=ev.strength, directness=sig.get("directness", 0.6),
                      modality=sig.get("modality", ""), rationale=ev.rationale, signals=sig, classifier=sig.get("classifier", ""))
        items.append(EvidenceItem(ctx=ctx, judgement=j))
    snap_docs = (a.retrieval or {}).get("documents_snapshot", {})
    docs = {k: DocInfo(**{**v, "doc_date": date.fromisoformat(v["doc_date"]) if v.get("doc_date") else None,
                          "effective_date": date.fromisoformat(v["effective_date"]) if v.get("effective_date") else None,
                          "superseded_date": date.fromisoformat(v["superseded_date"]) if v.get("superseded_date") else None})
            for k, v in snap_docs.items()}
    edges = [Edge(**e) for e in (a.retrieval or {}).get("edges_snapshot", [])]
    used_embeddings = (a.config_snapshot.get("settings") or {}).get("embeddings")
    engine = CredibilityEngine(a.config_snapshot.get("credibility", load_config("credibility")),
                               embedder=get_embedder() if used_embeddings else None)
    res = engine.assess(analysis, items, docs, edges, as_of=a.as_of)
    changed = []
    hashes = {s.id: s.content_hash for s in db.scalars(select(Section).where(Section.id.in_([e.section_id for e in a.evidence])))}
    for ev in a.evidence:
        if hashes.get(ev.section_id) != ev.section_content_hash:
            changed.append(ev.section_id)
    same = res.verdict == a.verdict and abs(res.score - (a.score or 0)) < 1e-6
    return {"reproducible": same, "recorded": {"verdict": a.verdict, "score": a.score},
            "recomputed": {"verdict": res.verdict, "score": res.score}, "changed_sections_since": changed,
            "note": "Scoring re-run from the recorded evidence judgements, document-metadata and relationship "
                    "snapshots and configuration snapshot. Sections whose text has changed since are listed."}


def assess_text(text: str, classifier: str | None = None) -> dict[str, Any]:
    from app.database.session import get_sessionmaker

    with get_sessionmaker()() as db:
        out = investigate(db, text, classifier_mode=classifier)
        return {
            "claim": out.analysis.model_dump(),
            "result": out.result.model_dump(mode="json"),
            "explanation": out.explanation,
            "evidence": [{"rank": i + 1, "document": it.ctx.document_id, "section": it.ctx.section_id,
                          "label": it.judgement.label, "scope": it.judgement.scope, "weight": it.weight,
                          "contribution": it.contribution, "cluster": it.cluster_id, "span": it.ctx.span[:200],
                          "rationale": it.judgement.rationale} for i, it in enumerate(out.items)],
            "retrieval": out.retrieval_log,
            "versions": out.versions,
        }
