"""Persisting assessments (the audit trail) and re-scoring them from the stored record.

run_assessment(): investigate + persist Assessment / AssessmentEvidence + update the claim
reproduce()     : re-score a stored assessment from its recorded evidence + configuration snapshot
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from okc.assessment.investigate import Outcome, get_gazetteer, investigate
from okc.claims.analysis import ClaimAnalysis, analyze
from okc.credibility.engine import CredibilityEngine
from okc.credibility.models import DocInfo, EvidenceItem, RelationEdge
from okc.db.models import Assessment, AssessmentEvidence, Claim, Section
from okc.evidence.schemas import EvidenceContext, Judgement
from okc.search.embeddings import get_embedder
from okc.settings import config_hash, load_config

log = logging.getLogger(__name__)
_DATE_FIELDS = ("doc_date", "effective_date", "superseded_date")
_CONTEXT_KEY = "_context"  # provenance kept inside each evidence row's signals


def create_assessment(db: Session, claim: Claim, as_of: date | None) -> Assessment:
    a = Assessment(id=str(uuid.uuid4()), claim_id=claim.id, status="queued", as_of=as_of)
    db.add(a)
    db.flush()
    return a


def _doc_snapshot(doc: DocInfo) -> dict[str, Any]:
    return {**asdict(doc), **{f: (str(getattr(doc, f)) if getattr(doc, f) else None) for f in _DATE_FIELDS}}


def _evidence_row(rank: int, it: EvidenceItem) -> AssessmentEvidence:
    c, j = it.ctx, it.judgement
    context = {"doc_type": c.doc_type, "doc_kind": c.doc_kind, "status": c.status, "flags": c.flags,
               "doc_date": c.doc_date, "doc_people": c.doc_people, "source_path": c.source_path, "heading": c.heading,
               "section_role": c.section_role, "doc_title": c.doc_title, "start_line": c.start_line,
               "end_line": c.end_line}
    return AssessmentEvidence(
        rank=rank, section_id=c.section_id, document_id=c.document_id,
        section_content_hash=it.retrieval.get("content_hash", ""), section_text=c.section_text,
        span_start=c.span_start, span_end=c.span_end, span_text=c.span, label=j.label, scope=j.scope,
        strength=j.strength, rationale=j.rationale,
        signals={**j.signals, "modality": j.modality, "directness": j.directness, "conditions": j.conditions,
                 "classifier": j.classifier, _CONTEXT_KEY: context},
        features={**it.features, "weight": it.weight}, contribution=it.contribution, cluster_id=it.cluster_id,
        retrieval={k: v for k, v in it.retrieval.items() if k != "duplicates"},
        duplicates=it.retrieval.get("duplicates", []))


def persist_outcome(claim: Claim, assessment: Assessment, out: Outcome) -> Assessment:
    r = out.result
    assessment.status = "completed"
    assessment.verdict = r.verdict
    assessment.score = r.score
    assessment.needs_review = r.needs_review
    assessment.result = r.model_dump(mode="json")
    assessment.explanation = out.explanation
    assessment.retrieval = {**out.retrieval_log, "claim_analysis": out.analysis.model_dump(mode="json"),
                            "edges_snapshot": [asdict(e) for e in out.edges],
                            "documents_snapshot": {k: _doc_snapshot(v) for k, v in out.docs.items()}}
    assessment.config_snapshot = out.config_snapshot
    assessment.config_hash = config_hash(out.config_snapshot)
    assessment.versions = out.versions
    assessment.completed_at = datetime.now(timezone.utc)
    assessment.evidence.clear()
    assessment.evidence.extend(_evidence_row(rank, it) for rank, it in enumerate(out.items, start=1))

    claim.analysis = out.analysis.model_dump(mode="json")
    claim.latest_assessment_id = assessment.id
    claim.latest_verdict = r.verdict
    claim.latest_score = r.score
    claim.needs_review = r.needs_review
    claim.updated_at = datetime.now(timezone.utc)
    return assessment


def run_assessment(db: Session, assessment_id: str, *, classifier_mode: str | None = None,
                   max_evidence: int | None = None, retrieval_mode: str | None = None) -> Assessment:
    a = db.get(Assessment, assessment_id)
    claim = db.get(Claim, a.claim_id)
    a.status = "running"
    db.commit()
    try:
        out = investigate(db, claim.text, classifier_mode=classifier_mode, as_of=a.as_of, max_evidence=max_evidence,
                          retrieval_mode=retrieval_mode)
        persist_outcome(claim, a, out)
    except Exception as exc:  # noqa: BLE001 - surfaced on the assessment record
        db.rollback()
        log.exception("Assessment %s failed", assessment_id)
        a = db.get(Assessment, assessment_id)
        a.status, a.error = "failed", f"{type(exc).__name__}: {exc}"
        a.completed_at = datetime.now(timezone.utc)
    db.commit()
    return a


# ------------------------------------------------------------------- reproduce

def _stored_items(a: Assessment) -> list[EvidenceItem]:
    items = []
    for ev in a.evidence:
        sig = dict(ev.signals or {})
        cx = sig.get(_CONTEXT_KEY, {})
        ctx = EvidenceContext(
            section_id=ev.section_id, document_id=ev.document_id, doc_title=cx.get("doc_title", ""),
            doc_type=cx.get("doc_type", ""), doc_kind=cx.get("doc_kind", ""), status=cx.get("status", ""),
            flags=cx.get("flags", []), doc_date=cx.get("doc_date"), doc_people=cx.get("doc_people", []),
            heading=cx.get("heading", ""), section_role=cx.get("section_role", ""), source_path=cx.get("source_path", ""),
            start_line=cx.get("start_line", 0), end_line=cx.get("end_line", 0),
            section_text=ev.section_text, span_start=ev.span_start, span_end=ev.span_end)
        j = Judgement(label=ev.label, scope=ev.scope, strength=ev.strength, directness=sig.get("directness", 0.6),
                      modality=sig.get("modality", ""), rationale=ev.rationale, signals=sig,
                      classifier=sig.get("classifier", ""))
        items.append(EvidenceItem(ctx=ctx, judgement=j))
    return items


def _stored_docs(a: Assessment) -> dict[str, DocInfo]:
    docs = {}
    for k, v in (a.retrieval or {}).get("documents_snapshot", {}).items():
        dates = {f: (date.fromisoformat(v[f]) if v.get(f) else None) for f in _DATE_FIELDS}
        docs[k] = DocInfo(**{**v, **dates})
    return docs


def reproduce(db: Session, assessment_id: str) -> dict[str, Any]:
    """Re-score a stored assessment from its recorded evidence judgements and configuration snapshot.

    Retrieval and classification are not re-run (their recorded outputs are inputs here), so this
    checks that the scoring step is deterministic given the audit record. It also reports whether
    any evidence section has changed since the assessment.
    """
    a = db.get(Assessment, assessment_id)
    if a is None or a.status != "completed":
        return {"reproducible": False, "reason": "assessment not found or not completed"}
    claim = db.get(Claim, a.claim_id)
    stored_analysis = (a.retrieval or {}).get("claim_analysis")
    analysis = ClaimAnalysis.model_validate(stored_analysis) if stored_analysis else analyze(claim.text, get_gazetteer(db))
    edges = [RelationEdge(**e) for e in (a.retrieval or {}).get("edges_snapshot", [])]
    used_embeddings = (a.config_snapshot.get("settings") or {}).get("embeddings")
    engine = CredibilityEngine(a.config_snapshot.get("credibility", load_config("credibility")),
                               embedder=get_embedder() if used_embeddings else None)
    rescored = engine.assess(analysis, _stored_items(a), _stored_docs(a), edges, as_of=a.as_of)

    hashes = {s.id: s.content_hash
              for s in db.scalars(select(Section).where(Section.id.in_([e.section_id for e in a.evidence])))}
    changed = [e.section_id for e in a.evidence if hashes.get(e.section_id) != e.section_content_hash]
    same = rescored.verdict == a.verdict and abs(rescored.score - (a.score or 0)) < 1e-6
    return {"reproducible": same, "recorded": {"verdict": a.verdict, "score": a.score},
            "recomputed": {"verdict": rescored.verdict, "score": rescored.score}, "changed_sections_since": changed,
            "note": "Scoring re-run from the recorded evidence judgements, document-metadata and relationship "
                    "snapshots and configuration snapshot. Sections whose text has changed since are listed."}


def assess_text(text: str, classifier: str | None = None) -> dict[str, Any]:
    """One-off assessment (nothing is stored); used by the CLI."""
    from okc.db.session import get_sessionmaker

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
