"""Answers that come from the evidence pipeline: a claim (or a question turned into one) checked against the files.

  best_statement()      the statement in the policies / procedures / meeting decisions that best answers a question
  check_claim()         create or reuse an assessment of a claim
  answer_from_assessment()  summarise an assessment as a plain answer with its evidence
  passage_answer()      for questions no statement answers: the passages that best match
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from okc.answers import compliance as cp
from okc.answers.evidence import (
    base_answer,
    evidence_item,
    message_body,
    record_line,
    section_name,
    trim,
    type_label,
)
from okc.answers.understand import Rephrased, stems
from okc.assessment.investigate import corpus_version, get_gazetteer
from okc.assessment.store import create_assessment, run_assessment
from okc.claims.analysis import analyze
from okc.claims.candidates import extract_candidates
from okc.db.models import Assessment, Claim, Document
from okc.search.embeddings import get_embedder
from okc.search.schemas import SearchFilters, SearchRequest
from okc.search.service import get_search_service

# Stems too generic to show that a question is about the same thing as a statement.
_GENERIC_STEMS = {"date", "perio", "revie", "polic", "year", "month", "time", "statu", "versi", "docum", "effec", "owner",
                  "updat", "recor", "proce", "end", "new", "respo", "accou", "assig", "step", "steps", "own", "owner"}
_NUMBERISH = re.compile(r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve|weekly|monthly|daily)\b", re.I)
_WANTS_NUMBER = re.compile(r"\s*(how (long|many|much|often|soon)|when|what (is|are) the (limit|cap|threshold|deadline|"
                           r"period|time|timeline))", re.I)
_WHO_LINE = re.compile(r"\b(responsible|accountable|approv\w*|owner|owns|sign[- ]?off|decides|assigned)\b", re.I)
_RESPONSIBLE_PAIR = re.compile(r"\s*(responsible|accountable)\s*:", re.I)
_VERBISH = re.compile(r"\b(is|are|was|were|be|been|has|have|had|will|would|should|must|can|could|did|does|do|said|says|"
                      r"told|goes|went|got|gets|made|makes|need|needs|\w{3,}ed|\w{3,}es)\b", re.I)

_OUTCOME = {
    "SUPPORTED": ("YES", "Yes, the files back this up"),
    "PARTIALLY_SUPPORTED": ("MOSTLY", "Mostly yes, with limits"),
    "CONTESTED": ("MIXED", "It depends: the files disagree"),
    "CONTRADICTED": ("NO", "No, the files say otherwise"),
    "INSUFFICIENT_EVIDENCE": ("UNKNOWN", "The files don't say"),
}
_PRACTICE_SCOPES = {"practice", "testimony", "belief"}
_SECTION_ROLE_BONUS = {"decision": 0.5, "requirement": 0.3, "procedure_step": 0.3, "responsibility": 0.2,
                       "discussion": 0.15, "action": 0.1, "open_issue": 0.1, "header": -0.5, "purpose": -0.4,
                       "background": -0.2}


# ---------------------------------------------------------------- matching a question to a statement

def _on_topic(question: str, statement: str, quote: str, strict: bool = False) -> bool:
    """True when a passage is about the same thing as the statement (shares non-generic words with both).
    strict (for disagreeing passages): at least two shared words, and the passage must read as a statement."""
    q, s, x = stems(question) - _GENERIC_STEMS, stems(statement) - _GENERIC_STEMS, stems(quote) - _GENERIC_STEMS
    if not (q & x and s & x):
        return False
    return not strict or (len(s & x) >= 2 and bool(_VERBISH.search(quote)))


def best_statement(db: Session, question: str) -> dict | None:
    """The statement in the policies / procedures / meeting decisions that best answers a question.

    Candidates come from the documents search ranks highest; they are scored by meaning (embedding
    cosine) when the embedding model is available, with word overlap as a guard, plus small bonuses for
    numbers on how-long / how-many questions and for the document title naming the topic."""
    response = get_search_service().search(SearchRequest(
        query=question[:500], mode="hybrid", size=20,
        filters=SearchFilters(doc_types=["policy", "procedure", "meeting_note"], include_records=False)))
    if not response.hits:
        return None
    rank: dict[str, int] = {}
    for i, h in enumerate(response.hits):
        rank.setdefault(h.section_id, i)
    doc_ids = list(dict.fromkeys(h.document_id for h in response.hits))[:5]
    docs = db.scalars(select(Document).where(Document.id.in_(doc_ids)).options(selectinload(Document.sections))).all()
    candidates = extract_candidates(docs, limit=500)
    if not candidates:
        return None

    question_stems = stems(question) - _GENERIC_STEMS
    wants_number = bool(_WANTS_NUMBER.match(question))
    asks_who = bool(re.match(r"\s*(who|whose|which (person|role|team))\b", question, re.I))
    embedder = get_embedder()
    similarity: list[float] = [0.0] * len(candidates)
    if embedder is not None:
        try:
            import numpy as np

            vectors = np.asarray(embedder.encode([question] + [f"{c.document_title}: {c.text}" for c in candidates]))
            similarity = list((vectors[1:] @ vectors[0]).tolist())
        except Exception:  # noqa: BLE001 - fall back to word overlap
            embedder = None

    best, best_score = None, -1.0
    for c, sim in zip(candidates, similarity):
        shared = len(question_stems & (stems(c.text) | stems(c.document_title)))  # the title carries the topic
        who_line = asks_who and bool(_WHO_LINE.search(c.text))
        topic_text = c.text + " " + c.section_heading + (" " + c.document_title if who_line else "")
        if shared < 1 or not question_stems & stems(topic_text):
            continue
        overlap = len(question_stems & stems(c.text)) / max(1, len(question_stems))
        base = sim if embedder is not None else overlap
        score = (base + 0.12 * overlap + 0.1 * (len(question_stems & stems(c.document_title)) / max(1, len(question_stems)))
                 + 0.1 / (1 + rank.get(c.section_id, 20)) + (0.03 if not c.caution else 0.0)
                 + (0.12 if wants_number and _NUMBERISH.search(c.text) else 0.0)
                 + (0.15 if asks_who and _WHO_LINE.search(c.text) else 0.0))
        threshold = (0.35 if who_line else 0.5) if embedder is not None else 0.34
        if base >= threshold and score > best_score:
            best, best_score = c.model_dump(), score
    if best and asks_who and _RESPONSIBLE_PAIR.match(best["text"]):
        # answer with the whole Responsible / Accountable pair from the same section
        pair = [c.text for c in candidates if c.section_id == best["section_id"] and _RESPONSIBLE_PAIR.match(c.text)]
        best = {**best, "text": " ".join(pair[:2])}
    return best


# ---------------------------------------------------------------- assessments -> answers

def _latest_reusable(db: Session, claim: Claim, mode: str | None) -> Assessment | None:
    """The claim's latest completed assessment, if it was made on the current corpus with the same judge."""
    if not claim.latest_assessment_id:
        return None
    a = db.get(Assessment, claim.latest_assessment_id)
    if not a or a.status != "completed" or (a.versions or {}).get("corpus") != corpus_version(db):
        return None
    used = ((a.versions or {}).get("classifier") or {}).get("mode")
    if mode and used and used != mode:
        return None
    return a


def _evidence_from_assessment(db: Session, e: Any, role: str) -> dict[str, Any]:
    cx = (e.signals or {}).get("_context", {})
    doc = db.get(Document, e.document_id)
    if doc is not None and doc.kind == "record":
        quote = record_line(doc)
    elif (doc is not None and doc.doc_type == "communication") or cx.get("doc_type") == "communication":
        quote = trim(message_body(e.span_text), 300)
    else:
        quote = trim(e.span_text, 300)
    if doc is None:
        return {"doc_id": e.document_id, "section_id": e.section_id, "title": cx.get("doc_title", e.document_id),
                "type": type_label(cx.get("doc_type"), cx.get("status")), "date": cx.get("doc_date"), "status": cx.get("status"),
                "path": cx.get("source_path"), "quote": quote, "role": role, "note": "",
                "section": section_name(cx.get("heading"), e.document_id, cx.get("doc_title"))}
    item = evidence_item(doc, e.section_id, quote, role)
    item["section"] = section_name(cx.get("heading"), doc.id, doc.title) if doc.kind != "record" else None
    return item


def _who_said(ev: dict[str, Any]) -> str:
    when = f", {cp.fmt_date(cp.to_date(ev['date']))}" if ev.get("date") else ""
    sec = f" · {ev['section']}" if ev.get("section") else ""
    return f"{ev['doc_id']}{sec} ({ev['type'].lower()}{when})"


def _why_it_counts_less(e: Any) -> str:
    features, cx = e.features or {}, (e.signals or {}).get("_context", {})
    if cx.get("status") == "superseded":
        return "it is superseded, so it no longer applies"
    if cx.get("status") == "draft":
        return "it is only a draft"
    if (e.signals or {}).get("predates_replacement"):
        return f"it predates the change recorded on {cp.fmt_date(cp.to_date(e.signals['predates_replacement']))}"
    if (features.get("temporal") or 1) < 0.6:
        return "it is out of date"
    if (features.get("authority") or 1) < 0.4:
        return "it is a less authoritative source for this kind of claim"
    return "it is outweighed by the other sources"


def _top_documents(evidence: list[Any], label: str, k: int) -> list[Any]:
    """The strongest evidence rows with this label, one per document."""
    out, seen = [], set()
    for e in evidence:
        if e.label == label and e.document_id not in seen:
            seen.add(e.document_id)
            out.append(e)
        if len(out) == k:
            break
    return out


def _direct_answer(outcome: str, claim_type: str | None, sup: list, con: list, sup_ev: list, con_ev: list) -> str:
    if outcome == "YES":
        return (f"{_who_said(sup_ev[0])} says so: \"{trim(sup_ev[0]['quote'], 200)}\"" if sup_ev
                else "The files back this up.")
    if outcome == "MOSTLY":
        weak = sup and all(e.scope not in ("formal", "decision") for e in sup)
        why = ("the support comes from accounts and records, not from a formal policy or decision" if weak
               else "only part of the claim is covered by the files")
        return f"The files support this, but {why}."
    if outcome == "MIXED":
        if claim_type == "normative" and con and con[0].scope in _PRACTICE_SCOPES:
            return (f"The written rule supports it ({sup_ev[0]['doc_id']}), but practice differs: "
                    f"{_who_said(con_ev[0])} shows \"{trim(con_ev[0]['quote'], 180)}\"" if sup_ev
                    else "Practice and the written rules disagree.")
        if sup_ev and con_ev:
            pick = next((x for e, x in zip(con, con_ev) if e.scope in _PRACTICE_SCOPES), con_ev[0])
            return f"Partly. {_who_said(sup_ev[0])} supports it, but {_who_said(pick)} says: \"{trim(pick['quote'], 200)}\""
        return "The files point both ways."
    if outcome == "NO":
        return (f"{_who_said(con_ev[0])} says otherwise: \"{trim(con_ev[0]['quote'], 200)}\"" if con_ev
                else "The files say otherwise.")
    return "None of the files directly confirms or contradicts this."


def _reasons(outcome: str, sup: list, con: list, sup_ev: list, con_ev: list, governance: list) -> list[str]:
    pro = [f"Supports it: {_who_said(x)}: \"{trim(x['quote'], 220)}\""
           + (f" (counts less: {_why_it_counts_less(e)})" if outcome == "NO" else "") for e, x in zip(sup, sup_ev)]
    contra = [f"Goes against it: {_who_said(x)}: \"{trim(x['quote'], 220)}\""
              + (f" (counts less: {_why_it_counts_less(e)})" if outcome in ("YES", "MOSTLY") else "") for e, x in zip(con, con_ev)]
    reasons = contra + pro if outcome == "NO" else pro + contra
    for x in sup_ev:
        other = next((c for c in con_ev if c["doc_id"] == x["doc_id"]), None)
        if other:
            reasons.insert(0, f"{x['doc_id']} says both: its {x.get('section') or 'one'} section supports the claim, but its "
                              f"{other.get('section') or 'another'} section says otherwise, so the document itself shows the "
                              f"written process and what happens in practice differ.")
            break
    for g in governance[:1]:
        text = g.get("explanation") or ""
        if text and "human review" not in text and not text.startswith("No recorded"):
            reasons.append(text)
    return reasons or ["No passage in the files clearly supports or contradicts the claim."]


def _caveats(a: Assessment, outcome: str, claim_type: str | None, sup: list) -> list[str]:
    r = a.result or {}
    caveats = []
    formal_support = any(e.scope in ("formal", "decision") for e in sup)
    if a.needs_review and (outcome in ("MIXED", "MOSTLY", "UNKNOWN") or not formal_support):
        caveats.append("Worth confirming with the document owner: " + (
            "the sources disagree." if outcome == "MIXED" else "the evidence is limited."))
    if (r.get("practice_adherence") is not None and claim_type == "normative" and outcome in ("YES", "MOSTLY")
            and (r.get("practice_adherence") or 1) < 0.8):
        caveats.append("The written rule is not always followed in practice; some records show it being bypassed.")
    return caveats


def answer_from_assessment(db: Session, question: str, a: Assessment, rp: Rephrased | None = None) -> dict[str, Any]:
    result = a.result or {}
    outcome, headline = _OUTCOME[a.verdict or result.get("verdict") or "INSUFFICIENT_EVIDENCE"]
    ranked = sorted(a.evidence, key=lambda e: -abs(e.contribution or 0))
    sup, con = _top_documents(ranked, "SUPPORTS", 2), _top_documents(ranked, "CONTRADICTS", 2)
    sup_ev = [_evidence_from_assessment(db, e, "supports") for e in sup]
    con_ev = [_evidence_from_assessment(db, e, "against") for e in con]
    claim_type = ((a.retrieval or {}).get("claim_analysis") or {}).get("claim_type")

    answer = _direct_answer(outcome, claim_type, sup, con, sup_ev, con_ev)
    reasons = _reasons(outcome, sup, con, sup_ev, con_ev, result.get("governance") or [])
    caveats = _caveats(a, outcome, claim_type, sup)

    evidence_override = None
    if rp and rp.source:
        outcome, headline, answer, reasons, sup_ev, con_ev = _narrow_to_statement(
            question, rp, outcome, headline, reasons, sup_ev, con_ev)
        caveats = [c for c in caveats if not (outcome == "FOUND" and "sources disagree" in c)]
        evidence_override = sup_ev + con_ev
    claim = db.get(Claim, a.claim_id)
    evidence = evidence_override if evidence_override is not None else sup_ev + con_ev
    if rp and rp.source:
        source_doc = db.get(Document, rp.source["document_id"])
        if source_doc and all(x["doc_id"] != source_doc.id for x in evidence):
            evidence.insert(0, evidence_item(source_doc, rp.source["section_id"], rp.source["text"], "context",
                                             "Where the statement comes from"))
    return base_answer(question, "claim", outcome, headline, answer, reasons=reasons[:5], caveats=caveats,
                       evidence=evidence, checked_as=claim.text if claim else None, note=rp.note if rp else None,
                       claim_id=a.claim_id, assessment_id=a.id)


def _narrow_to_statement(question: str, rp: Rephrased, outcome: str, headline: str, reasons: list[str],
                         sup_ev: list, con_ev: list):
    """The question was answered with a statement from the files: report whether the other files agree with it."""
    statement = rp.source["text"].strip()
    disagreeing = [x for x in con_ev if _on_topic(question, statement, x["quote"], strict=True)]
    sup_ev = [x for x in sup_ev if _on_topic(question, statement, x["quote"]) or x["doc_id"] == rp.source["document_id"]]
    if not disagreeing and outcome in ("MIXED", "NO"):
        outcome, con_ev = "YES", []
    else:
        con_ev = disagreeing
    kept = {x["doc_id"] for x in con_ev} | {x["doc_id"] for x in sup_ev}
    reasons = [x for x in reasons if not (x.startswith(("Goes against it", "Supports it"))
                                          and not any(f": {k} (" in x for k in kept))]
    source = f"{rp.source['document_id']} ({rp.source['section_heading']})"
    follow_up = {
        "YES": "The other files agree.",
        "MOSTLY": "The other files mostly agree.",
        "MIXED": (f"But not everyone follows it: {_who_said(con_ev[0])} says: \"{trim(con_ev[0]['quote'], 180)}\""
                  if con_ev else "But the files disagree on it."),
        "NO": (f"But other files contradict it: {_who_said(con_ev[0])} says: \"{trim(con_ev[0]['quote'], 180)}\""
               if con_ev else "But other files contradict it."),
        "UNKNOWN": "No other file confirms it.",
    }[outcome]
    headline = re.sub(r"^\d+\.\s*", "", statement).rstrip(".")
    answer = f"{source} says: \"{statement}\" {follow_up}"
    outcome = {"YES": "FOUND", "MOSTLY": "FOUND"}.get(outcome, outcome)
    return outcome, headline, answer, reasons, sup_ev, con_ev


def check_claim(db: Session, question: str, text: str, *, rp: Rephrased | None = None, classifier_mode: str | None = None,
                retrieval_mode: str | None = None, max_evidence: int | None = None, fresh: bool = False) -> dict[str, Any]:
    analysis = analyze(text, get_gazetteer(db))
    claim = db.scalars(select(Claim).where(Claim.normalized_text == analysis.normalized)).first()
    if claim is None:
        claim = Claim(id=str(uuid.uuid4()), text=text, normalized_text=analysis.normalized,
                      analysis=analysis.model_dump(mode="json"), origin="question", tags=[])
        db.add(claim)
        db.commit()
    a = None if (fresh or retrieval_mode or max_evidence) else _latest_reusable(db, claim, classifier_mode or "rules+nli")
    if a is None:
        a = create_assessment(db, claim, None)
        db.commit()
        a = run_assessment(db, a.id, classifier_mode=classifier_mode, max_evidence=max_evidence,
                           retrieval_mode=retrieval_mode)
        if a.status != "completed":
            return base_answer(question, "claim", "ERROR", "The check failed", a.error or "The check failed.",
                               checked_as=text, claim_id=claim.id, assessment_id=a.id)
    return answer_from_assessment(db, question, a, rp)


# ---------------------------------------------------------------- passages

def passage_answer(db: Session, question: str) -> dict[str, Any] | None:
    """For questions no statement answers: the passages that best answer it, with their sources."""
    response = get_search_service().search(SearchRequest(query=question[:500], mode="hybrid", size=15,
                                                         filters=SearchFilters(include_records=False)))
    question_stems = stems(question) - _GENERIC_STEMS
    dated = sorted({h.doc_date for h in response.hits if h.doc_date})
    scored = []
    for i, h in enumerate(response.hits):
        shared = len(question_stems & stems(h.text))
        if shared < 2 or h.status in ("superseded", "draft"):
            continue
        recency = (dated.index(h.doc_date) / max(1, len(dated) - 1)) if h.doc_date in dated else 0.0
        scored.append((shared / max(1, len(question_stems)) + _SECTION_ROLE_BONUS.get(h.section_role, 0.0)
                       + 0.3 * recency - 0.02 * i, h))
    hits, seen = [], set()
    for _, h in sorted(scored, key=lambda x: -x[0]):
        if h.document_id not in seen:
            seen.add(h.document_id)
            hits.append(h)
    hits = hits[:3]
    if not hits:
        return None
    evidence = []
    for h in hits:
        d = db.get(Document, h.document_id)
        if d:
            evidence.append(evidence_item(d, h.section_id, trim(h.text, 360), "context", h.heading))
    top = evidence[0]
    return base_answer(question, "passage", "FOUND", "Here is what the files say",
                       f"{_who_said(top)} ({top['note']}): \"{trim(top['quote'], 300)}\"",
                       reasons=[f"{_who_said(e)}: \"{trim(e['quote'], 220)}\"" for e in evidence[1:]],
                       caveats=["This is the closest passage in the files; no single statement answers the question directly."],
                       evidence=evidence, note="Answered with the passages that best match your question.")
