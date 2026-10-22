"""Answer a question about the organisation in plain language.

  question names a record (DEAL-027)  -> record compliance check (answers.compliance)
  "which deals broke the policy?"     -> scan every record of that kind
  anything else                       -> turned into a claim and checked by the evidence pipeline,
                                         then summarised as a direct answer with its evidence

Every answer has the same shape: an outcome, a one-line headline, a direct answer, the reasons
behind it, caveats, and the few pieces of evidence that prove it (each with its source).
"""

from __future__ import annotations

import re
import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.answers import compliance as cp
from app.answers.questions import Rephrased, record_refs, rephrase, scan_subject, stems
from app.assessments.service import corpus_version, create_assessment, get_gazetteer, run_assessment
from app.claims.analysis import ClaimValidationError, analyze
from app.core.config import load_config
from app.database.models import Assessment, Claim, Document, Section

ANSWER_VERSION = "answers-1.0"

_TYPE_LABEL = {
    "policy": "Policy", "procedure": "Process document", "meeting_note": "Meeting notes", "interview": "Interview",
    "communication": "Message", "communication_index": "Message index", "org_reference": "Org reference",
    "crm_record": "CRM deal record", "task_record": "Task record", "audit_event": "Audit log entry",
}


# ---------------------------------------------------------------- shared helpers

def _label(doc_type: str | None, status: str | None = None) -> str:
    base = _TYPE_LABEL.get(doc_type or "", (doc_type or "File").replace("_", " ").capitalize())
    if status in ("superseded", "draft"):
        return f"{base} ({status.upper()})"
    return base


def _trim(text: str, n: int = 260) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


_HEADER = re.compile(r"^\s*(from|to|cc|subject|timestamp|participants|thread relationship|date)\s*:", re.I)


def _around(text: str, needle: str, n: int = 360) -> str:
    """What a message actually says about needle: the first non-header line mentioning it, plus the reply."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if needle in ln and not _HEADER.match(ln):
            return _trim(" ".join(lines[i:i + 2]), n)
    body = [ln for ln in lines if not _HEADER.match(ln)]
    return _trim(" ".join(body) or text, n)


def _evidence(doc: Document, section_id: str | None, quote: str, role: str, note: str = "") -> dict[str, Any]:
    return {"doc_id": doc.id, "section_id": section_id, "title": doc.title, "type": _label(doc.doc_type, doc.status),
            "date": doc.doc_date.isoformat() if doc.doc_date else None, "status": doc.status, "path": doc.rel_path,
            "quote": quote, "role": role, "note": note}


_lookup_lock = threading.Lock()
_lookup_cache: dict[str, Any] = {}


def _people_lookup(db: Session):
    """lookup(name) -> (full name, role), resolving unique first names via the roster."""
    gz = get_gazetteer(db)

    def lookup(name: str) -> tuple[str, str | None]:
        name = (name or "").strip()
        if name in gz.people:
            return name, gz.people[name].get("role")
        full = gz.first_names.get(name.split()[0]) if name else None
        if full and (" " not in name or full.split()[-1] == name.split()[-1]):
            return full, gz.people[full].get("role")
        return name, None

    return lookup


def _audit_by_record(db: Session) -> dict[str, list[dict[str, Any]]]:
    key = corpus_version(db)
    with _lookup_lock:
        if _lookup_cache.get("key") != key:
            out: dict[str, list[dict[str, Any]]] = {}
            for d in db.scalars(select(Document).where(Document.doc_type == "audit_event", Document.is_deleted.is_(False))):
                rel = (d.meta or {}).get("related_record") or ""
                obj = (d.meta or {}).get("object") or ""
                for rid in {rel, *re.findall(r"[A-Z]{2,6}-\d{2,4}", obj)} - {""}:
                    out.setdefault(rid, []).append({**(d.meta or {}), "_doc_id": d.id})
            _lookup_cache.update(key=key, audit=out)
        return _lookup_cache["audit"]


def _mentions(db: Session, rid: str, exclude: set[str], limit: int = 4) -> list[tuple[Document, Section]]:
    """Sections of non-record documents (messages, meetings, interviews) that mention a record ID."""
    rows = db.execute(select(Document, Section).join(Section, Section.document_id == Document.id)
                      .where(Section.plain_text.contains(rid), Section.is_deleted.is_(False),
                             Document.is_deleted.is_(False), Document.kind != "record",
                             Document.kind != "container")).all()
    seen, out = set(exclude), []
    for d, s in sorted(rows, key=lambda r: (r[1].parent_section_id is None, len(r[1].plain_text)), reverse=True):
        if d.id not in seen:
            seen.add(d.id)
            out.append((d, s))
    return out[:limit]


def _rule_evidence(db: Session, rule: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rule:
        return None
    sec = db.get(Section, rule["section"])
    doc = db.get(Document, rule["policy"])
    if not doc:
        return None
    return _evidence(doc, sec.id if sec else None, _trim(sec.plain_text if sec else rule["rule_text"], 420), "rule",
                     "The rule")


def _record_line(doc: Document) -> str:
    m = {k: cp.fix_text(str(v)) for k, v in (doc.meta or {}).items()}
    if doc.doc_type == "crm_record":
        parts = [f"{m.get('discount_pct') or '?'}% discount", m.get("stage", ""),
                 f"closed {m['close_date']}" if m.get("close_date") else "",
                 f"approval: {m.get('approval_record') or 'none'}" + (f" ({m['approval_role']})" if m.get("approval_role") else "")]
        line = f"{m.get('customer_name', '')}: " + ", ".join(p for p in parts if p)
        return line + (f". Note: {m['notes']}" if m.get("notes") else "")
    if doc.doc_type == "task_record":
        return f"{m.get('project', '')}. {m.get('comments', '')}".strip()
    if doc.doc_type == "audit_event":
        return (f"{m.get('timestamp', '')} · {m.get('actor_name', '')} ({m.get('actor_role', '')}): {m.get('action', '')}"
                f" {m.get('object', '')} ({m.get('previous_state', '')} → {m.get('new_state', '')})"
                + (f". {m['notes']}" if m.get("notes") else ""))
    return _trim(" · ".join(f"{k}: {v}" for k, v in m.items() if v), 300)


_GENERIC = {"date", "perio", "revie", "polic", "year", "month", "time", "statu", "versi", "docum", "effec", "owner",
            "updat", "recor", "proce", "end", "new", "respo", "accou", "assig", "step", "steps", "own", "owner"}


_VERBISH = re.compile(r"\b(is|are|was|were|be|been|has|have|had|will|would|should|must|can|could|did|does|do|said|says|"
                      r"told|goes|went|got|gets|made|makes|need|needs|\w{3,}ed|\w{3,}es)\b", re.I)


def _on_topic(question: str, statement: str, quote: str, strict: bool = False) -> bool:
    """True when a passage is about the same thing as the statement (shares non-generic words with both).
    strict (for disagreeing passages): at least two shared words, and the passage must read as a statement."""
    q, s, x = stems(question) - _GENERIC, stems(statement) - _GENERIC, stems(quote) - _GENERIC
    if not (q & x and s & x):
        return False
    return not strict or (len(s & x) >= 2 and bool(_VERBISH.search(quote)))


def _base(question: str, kind: str, outcome: str, headline: str, answer: str, **kw: Any) -> dict[str, Any]:
    return {"question": question, "kind": kind, "outcome": outcome, "headline": headline, "answer": answer,
            "reasons": kw.get("reasons", []), "caveats": kw.get("caveats", []), "evidence": kw.get("evidence", []),
            "facts": kw.get("facts", []), "checked_as": kw.get("checked_as"), "note": kw.get("note"),
            "claim_id": kw.get("claim_id"), "assessment_id": kw.get("assessment_id"), "items": kw.get("items", []),
            "version": ANSWER_VERSION}


# ---------------------------------------------------------------- records

def check_record(db: Session, question: str, rid: str, others: list[str] | None = None) -> dict[str, Any]:
    rb = load_config("compliance")
    doc = db.get(Document, rid)
    approvals = [a for a in _audit_by_record(db).get(rid, []) if a.get("action") == "Approved discount"]
    f = cp.check_record(doc.doc_type, doc.meta or {}, rb, _people_lookup(db), approvals)

    sec = db.scalars(select(Section).where(Section.document_id == rid)).first()
    evidence = [_evidence(doc, sec.id if sec else None, _record_line(doc), "record", "The record")]
    rule_ev = _rule_evidence(db, f.rule)
    if rule_ev:
        evidence.append(rule_ev)
    for a in _audit_by_record(db).get(rid, [])[:3]:
        ad = db.get(Document, a["_doc_id"])
        if ad:
            evidence.append(_evidence(ad, None, _record_line(ad), "audit", "System audit log"))
    mention_notes = []
    for d, s in _mentions(db, rid, {rid}):
        quote = _around(s.plain_text, rid)
        evidence.append(_evidence(d, s.id, quote, "mention", f"Mentions {rid}"))
        mention_notes.append(f"{d.id} ({_label(d.doc_type).lower()}{', ' + cp.fmt_date(d.doc_date) if d.doc_date else ''}) "
                             f"mentions {rid}: \"{_trim(quote, 220)}\"")
    reasons = list(f.reasons) + mention_notes[:2]
    caveats = list(f.caveats)
    if others:
        caveats.append(f"Your question also mentions {', '.join(others)}; ask about each separately for its own check.")
    return _base(question, "record", f.outcome, f"{rid}: {f.headline}", f.answer, reasons=reasons, caveats=caveats,
                 evidence=evidence, facts=[list(x) for x in f.facts], checked_as=rid,
                 note=f"Checked {rid} against {f.rule['policy']}." if f.rule else f"Showed what {rid} records.")


def _findings(db: Session, subject: str) -> list[tuple[Document, cp.Finding]]:
    """Every record of a kind ('deals' / 'expenses') checked against the rulebook."""
    rb = load_config("compliance")
    lookup = _people_lookup(db)
    audit = _audit_by_record(db)
    if subject == "deals":
        docs = db.scalars(select(Document).where(Document.doc_type == "crm_record", Document.is_deleted.is_(False))
                          .order_by(Document.id)).all()
    else:
        docs = [d for d in db.scalars(select(Document).where(Document.doc_type == "task_record",
                                                              Document.is_deleted.is_(False)).order_by(Document.id))
                if (d.meta or {}).get("process_or_capability") == rb["expense_approval"]["process"]]
    out = []
    for d in docs:
        approvals = [a for a in audit.get(d.id, []) if a.get("action") == "Approved discount"]
        out.append((d, cp.check_record(d.doc_type, d.meta or {}, rb, lookup, approvals)))
    return out


def scan(db: Session, question: str, subject: str) -> dict[str, Any]:
    what, rules = ("closed-won deals", ["POL-01", "POL-06"]) if subject == "deals" else ("expense claims", ["POL-07"])
    results = _findings(db, subject)
    checked = [(d, f) for d, f in results if f.outcome not in ("NOT_APPLICABLE", "NO_RULE")]
    bad = [(d, f) for d, f in checked if f.outcome == "VIOLATION"]
    likely = [(d, f) for d, f in checked if f.outcome == "LIKELY_VIOLATION"]
    unclear = [(d, f) for d, f in checked if f.outcome == "CANNOT_TELL"]
    ok = [(d, f) for d, f in checked if f.outcome == "COMPLIANT"]
    n_bad = len(bad) + len(likely)
    if n_bad:
        headline = f"{n_bad} of {len(checked)} {what} {'breaks' if n_bad == 1 else 'break'} a policy rule" + (f" ({len(likely)} not confirmed)" if likely else "")
        outcome = "VIOLATION"
    else:
        headline, outcome = f"No policy breaches found in {len(checked)} {what}", "COMPLIANT"
    parts = [f"{len(bad)} broke a rule" if bad else "", f"{len(likely)} probably did (no approval recorded)" if likely else "",
             f"{len(ok)} followed the rules" if ok else "",
             f"{len(unclear)} can't be checked because a field is missing" if unclear else ""]
    parts = [x for x in parts if x]
    answer = (f"Checked all {len(checked)} {what} against {' and '.join(rules)}: "
              + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0] if parts else "none apply") + ".")
    reasons = [f"{d.id}: {f.answer}" for d, f in bad + likely]
    items = [{"record": d.id, "outcome": f.outcome, "headline": f.headline, "answer": f.answer}
             for d, f in bad + likely + unclear + ok]
    evidence = [e for e in (_rule_evidence(db, {"policy": p, "section": f"{p}:formal-requirements", "rule_text": ""})
                            for p in rules) if e]
    for d, f in (bad + likely)[:4]:
        sec = db.scalars(select(Section).where(Section.document_id == d.id)).first()
        evidence.append(_evidence(d, sec.id if sec else None, _record_line(d), "record", f.headline))
    caveats = ([f"{len(unclear)} {what if len(unclear) > 1 else what[:-1]} could not be checked because the record is "
                f"missing a field the rule needs (amount or approver): " + ", ".join(d.id for d, _ in unclear[:12])
                + (" …" if len(unclear) > 12 else "") + "."] if unclear else [])
    return _base(question, "scan", outcome, headline, answer, reasons=reasons, caveats=caveats, evidence=evidence,
                 items=items, checked_as=f"all {what}", note=f"Every {what[:-1]} was checked against the rulebook.")


def rule_question(db: Session, question: str, topic: str) -> dict[str, Any]:
    from app.answers.rules_qa import answer_rule_question

    rb = load_config("compliance")
    practice = {k: [(d.id, f) for d, f in _findings(db, k)] for k in ("deals", "expenses")}
    ra = answer_rule_question(question, topic, rb, get_gazetteer(db), practice)
    evidence = [e for e in (_rule_evidence(db, {"policy": pid, "section": f"{pid}:formal-requirements", "rule_text": ""})
                            for pid in ra.policies) if e]
    for rid in ra.practice_records[:3]:
        d = db.get(Document, rid)
        if d:
            sec = db.scalars(select(Section).where(Section.document_id == rid)).first()
            evidence.append(_evidence(d, sec.id if sec else None, _record_line(d), "practice", "What actually happened"))
    return _base(question, "rule", ra.outcome, ra.headline, ra.answer, reasons=ra.reasons, caveats=ra.caveats,
                 evidence=evidence, checked_as=", ".join(ra.policies),
                 note="Answered from the policy rulebook, then checked against the records.")


# ---------------------------------------------------------------- claims

_NUMBERISH = re.compile(r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve|weekly|monthly|daily)\b", re.I)
_WANTS_NUMBER = re.compile(r"\s*(how (long|many|much|often|soon)|when|what (is|are) the (limit|cap|threshold|deadline|"
                           r"period|time|timeline))", re.I)
_WHO_LINE = re.compile(r"\b(responsible|accountable|approv\w*|owner|owns|sign[- ]?off|decides|assigned)\b", re.I)
DEBUG_CANDIDATES: list = []


def _best_statement(db: Session, question: str) -> dict | None:
    """The statement in the policies / procedures / meeting decisions that best answers a question.

    Candidates come from the documents search ranks highest; they are scored by meaning (embedding
    cosine) when the embedding model is available, with word overlap as a guard, plus small bonuses
    for numbers on how-long / how-many questions and for the document title naming the topic."""
    from app.claims.extraction import extract_candidates
    from app.retrieval.embeddings import get_embedder
    from app.retrieval.schemas import SearchFilters, SearchRequest
    from app.retrieval.service import get_search_service

    resp = get_search_service().search(SearchRequest(query=question[:500], mode="hybrid", size=20, filters=SearchFilters(
        doc_types=["policy", "procedure", "meeting_note"], include_records=False)))
    if not resp.hits:
        return None
    rank: dict[str, int] = {}
    for i, h in enumerate(resp.hits):
        rank.setdefault(h.section_id, i)
    doc_ids = list(dict.fromkeys(h.document_id for h in resp.hits))[:5]
    docs = db.scalars(select(Document).where(Document.id.in_(doc_ids)).options(selectinload(Document.sections))).all()
    cands = [c for c in extract_candidates(docs, limit=500)]
    if not cands:
        return None
    q = stems(question) - _GENERIC
    wants_number = bool(_WANTS_NUMBER.match(question))
    asks_who = bool(re.match(r"\s*(who|whose|which (person|role|team))\b", question, re.I))
    emb = get_embedder()
    cos: list[float] = [0.0] * len(cands)
    if emb is not None:
        try:
            import numpy as np

            v = np.asarray(emb.encode([question] + [f"{c.document_title}: {c.text}" for c in cands]))
            cos = list((v[1:] @ v[0]).tolist())
        except Exception:  # noqa: BLE001 - fall back to word overlap
            emb = None
    best, best_score = None, -1.0
    DEBUG_CANDIDATES.clear()
    for c, sim in zip(cands, cos):
        shared = len(q & (stems(c.text) | stems(c.document_title)))  # the title carries the topic
        who_line = asks_who and bool(_WHO_LINE.search(c.text))
        topic_text = c.text + " " + c.section_heading + (" " + c.document_title if who_line else "")
        if shared < 1 or not q & stems(topic_text):
            continue
        overlap = len(q & stems(c.text)) / max(1, len(q))
        base = sim if emb is not None else overlap
        score = (base + 0.12 * overlap + 0.1 * (len(q & stems(c.document_title)) / max(1, len(q)))
                 + 0.1 / (1 + rank.get(c.section_id, 20)) + (0.03 if not c.caution else 0.0)
                 + (0.12 if wants_number and _NUMBERISH.search(c.text) else 0.0)
                 + (0.15 if asks_who and _WHO_LINE.search(c.text) else 0.0))
        DEBUG_CANDIDATES.append((round(score, 3), round(sim, 3), c.document_id, c.text[:80]))
        threshold = (0.35 if who_line else 0.5) if emb is not None else 0.34
        if base >= threshold and score > best_score:
            best, best_score = c.model_dump(), score
    if best and asks_who and re.match(r"\s*(responsible|accountable)\s*:", best["text"], re.I):
        # answer with the whole Responsible / Accountable pair from the same section
        pair = [c.text for c in cands if c.section_id == best["section_id"]
                and re.match(r"\s*(responsible|accountable)\s*:", c.text, re.I)]
        best = {**best, "text": " ".join(pair[:2])}
    return best


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


_OUTCOME = {
    "SUPPORTED": ("YES", "Yes, the files back this up"),
    "PARTIALLY_SUPPORTED": ("MOSTLY", "Mostly yes, with limits"),
    "CONTESTED": ("MIXED", "It depends: the files disagree"),
    "CONTRADICTED": ("NO", "No, the files say otherwise"),
    "INSUFFICIENT_EVIDENCE": ("UNKNOWN", "The files don't say"),
}


def _body(text: str) -> str:
    """A message without its header lines and title."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    body = [ln for ln in lines if not _HEADER.match(ln) and not re.match(r"^[A-Z]+-\d+\s+[—-]", ln)]
    return " ".join(body) or text


def _ev_from_assessment(db: Session, e: Any, role: str) -> dict[str, Any]:
    cx = (e.signals or {}).get("_context", {})
    doc = db.get(Document, e.document_id)
    if doc is not None and doc.kind == "record":
        quote = _record_line(doc)
    elif (doc is not None and doc.doc_type == "communication") or cx.get("doc_type") == "communication":
        quote = _trim(_body(e.span_text), 300)
    else:
        quote = _trim(e.span_text, 300)
    if doc is None:
        return {"doc_id": e.document_id, "section_id": e.section_id, "title": cx.get("doc_title", e.document_id),
                "type": _label(cx.get("doc_type"), cx.get("status")), "date": cx.get("doc_date"), "status": cx.get("status"),
                "path": cx.get("source_path"), "quote": quote, "role": role, "note": "",
                "section": _section_name(cx.get("heading"), e.document_id, cx.get("doc_title"))}
    out = _evidence(doc, e.section_id, quote, role)
    out["section"] = _section_name(cx.get("heading"), doc.id, doc.title) if doc.kind != "record" else None
    return out


def _section_name(heading: str | None, doc_id: str, title: str | None = None) -> str | None:
    """A section heading worth showing (not the document's own title / header)."""
    h = (heading or "").strip()
    if not h or h == (title or "").strip() or h.startswith(doc_id) or h.lower() in ("header", "record"):
        return None
    return h


def _fill_sections(db: Session, evidence: list[dict[str, Any]]) -> None:
    for ev in evidence:
        if "section" in ev or not ev.get("section_id"):
            ev.setdefault("section", None)
            continue
        s = db.get(Section, ev["section_id"])
        ev["section"] = _section_name(s.heading if s else None, ev["doc_id"], ev.get("title")) \
            if ev.get("role") not in ("record", "audit", "practice") else None


def _who_said(ev: dict[str, Any]) -> str:
    when = f", {cp.fmt_date(cp.to_date(ev['date']))}" if ev.get("date") else ""
    sec = f" · {ev['section']}" if ev.get("section") else ""
    return f"{ev['doc_id']}{sec} ({ev['type'].lower()}{when})"


def answer_from_assessment(db: Session, question: str, a: Assessment, rp: Rephrased | None = None) -> dict[str, Any]:
    r = a.result or {}
    verdict = a.verdict or r.get("verdict") or "INSUFFICIENT_EVIDENCE"
    outcome, headline = _OUTCOME[verdict]
    ev = sorted(a.evidence, key=lambda e: -abs(e.contribution or 0))

    def top(label: str, k: int) -> list:
        out, seen = [], set()
        for e in ev:
            if e.label == label and e.document_id not in seen:
                seen.add(e.document_id)
                out.append(e)
            if len(out) == k:
                break
        return out

    sup, con = top("SUPPORTS", 2), top("CONTRADICTS", 2)
    sup_ev = [_ev_from_assessment(db, e, "supports") for e in sup]
    con_ev = [_ev_from_assessment(db, e, "against") for e in con]

    def weaker(e: Any) -> str:
        f = e.features or {}
        cx = (e.signals or {}).get("_context", {})
        if cx.get("status") == "superseded":
            return "it is superseded, so it no longer applies"
        if cx.get("status") == "draft":
            return "it is only a draft"
        if (e.signals or {}).get("predates_replacement"):
            return f"it predates the change recorded on {cp.fmt_date(cp.to_date(e.signals['predates_replacement']))}"
        if (f.get("temporal") or 1) < 0.6:
            return "it is out of date"
        if (f.get("authority") or 1) < 0.4:
            return "it is a less authoritative source for this kind of claim"
        return "it is outweighed by the other sources"

    claim_type = ((a.retrieval or {}).get("claim_analysis") or {}).get("claim_type")
    practice_scopes = {"practice", "testimony", "belief"}
    if outcome == "YES":
        answer = (f"{_who_said(sup_ev[0])} says so: \"{_trim(sup_ev[0]['quote'], 200)}\"" if sup_ev
                  else "The files back this up.")
    elif outcome == "MOSTLY":
        weak = sup and all(e.scope not in ("formal", "decision") for e in sup)
        why = ("the support comes from accounts and records, not from a formal policy or decision" if weak
               else "only part of the claim is covered by the files")
        answer = f"The files support this, but {why}."
    elif outcome == "MIXED":
        if claim_type == "normative" and con and con[0].scope in practice_scopes:
            answer = (f"The written rule supports it ({sup_ev[0]['doc_id']}), but practice differs: "
                      f"{_who_said(con_ev[0])} shows \"{_trim(con_ev[0]['quote'], 180)}\"" if sup_ev
                      else "Practice and the written rules disagree.")
        elif sup_ev and con_ev:
            pick = next((x for e, x in zip(con, con_ev) if e.scope in practice_scopes), con_ev[0])
            answer = (f"Partly. {_who_said(sup_ev[0])} supports it, but {_who_said(pick)} says: "
                      f"\"{_trim(pick['quote'], 200)}\"")
        else:
            answer = "The files point both ways."
    elif outcome == "NO":
        answer = (f"{_who_said(con_ev[0])} says otherwise: \"{_trim(con_ev[0]['quote'], 200)}\"" if con_ev
                  else "The files say otherwise.")
    else:
        answer = "None of the files directly confirms or contradicts this."

    pro = [f"Supports it: {_who_said(x)}: \"{_trim(x['quote'], 220)}\""
           + (f" (counts less: {weaker(e)})" if outcome == "NO" else "") for e, x in zip(sup, sup_ev)]
    contra = [f"Goes against it: {_who_said(x)}: \"{_trim(x['quote'], 220)}\""
              + (f" (counts less: {weaker(e)})" if outcome in ("YES", "MOSTLY") else "") for e, x in zip(con, con_ev)]
    reasons = contra + pro if outcome == "NO" else pro + contra
    for x in sup_ev:
        y = next((c for c in con_ev if c["doc_id"] == x["doc_id"]), None)
        if y:
            reasons.insert(0, f"{x['doc_id']} says both: its {x.get('section') or 'one'} section supports the claim, but its "
                              f"{y.get('section') or 'another'} section says otherwise, so the document itself shows the "
                              f"written process and what happens in practice differ.")
            break
    for g in (r.get("governance") or [])[:1]:
        text = g.get("explanation") or ""
        if text and "human review" not in text and not text.startswith("No recorded"):
            reasons.append(text)
    if not reasons:
        reasons.append("No passage in the files clearly supports or contradicts the claim.")

    caveats = []
    formal_support = any(e.scope in ("formal", "decision") for e in sup)
    if a.needs_review and (outcome in ("MIXED", "MOSTLY", "UNKNOWN") or not formal_support):
        caveats.append("Worth confirming with the document owner: " + (
            "the sources disagree." if outcome == "MIXED" else "the evidence is limited."))
    if (r.get("practice_adherence") is not None and claim_type == "normative" and outcome in ("YES", "MOSTLY")
            and (r.get("practice_adherence") or 1) < 0.8):
        caveats.append("The written rule is not always followed in practice; some records show it being bypassed.")
    if rp and rp.source:
        stmt = rp.source["text"].strip()
        on_topic = [x for x in con_ev if _on_topic(question, stmt, x["quote"], strict=True)]
        sup_ev = [x for x in sup_ev if _on_topic(question, stmt, x["quote"]) or x["doc_id"] == rp.source["document_id"]]
        if not on_topic and outcome in ("MIXED", "NO"):
            outcome, con_ev = "YES", []
            caveats_extra = []
        else:
            con_ev, caveats_extra = on_topic, []
        kept = {x["doc_id"] for x in con_ev} | {x["doc_id"] for x in sup_ev}
        reasons = [x for x in reasons if not (x.startswith(("Goes against it", "Supports it"))
                                             and not any(f": {k} (" in x for k in kept))]
        src = f"{rp.source['document_id']} ({rp.source['section_heading']})"
        follow = {
            "YES": "The other files agree.",
            "MOSTLY": "The other files mostly agree.",
            "MIXED": (f"But not everyone follows it: {_who_said(con_ev[0])} says: \"{_trim(con_ev[0]['quote'], 180)}\""
                      if con_ev else "But the files disagree on it."),
            "NO": (f"But other files contradict it: {_who_said(con_ev[0])} says: \"{_trim(con_ev[0]['quote'], 180)}\""
                   if con_ev else "But other files contradict it."),
            "UNKNOWN": "No other file confirms it.",
        }[outcome]
        headline = re.sub(r"^\d+\.\s*", "", stmt).rstrip(".")
        answer = f"{src} says: \"{stmt}\" {follow}"
        outcome = {"YES": "FOUND", "MOSTLY": "FOUND"}.get(outcome, outcome)
        evidence_override = sup_ev + con_ev
    else:
        caveats_extra, evidence_override = [], None
    claim = db.get(Claim, a.claim_id)
    note = rp.note if rp else None
    evidence = evidence_override if evidence_override is not None else sup_ev + con_ev
    caveats = [c for c in caveats if not (outcome == "FOUND" and "sources disagree" in c)] + caveats_extra
    if rp and rp.source:
        src_doc = db.get(Document, rp.source["document_id"])
        if src_doc and all(x["doc_id"] != src_doc.id for x in evidence):
            evidence.insert(0, _evidence(src_doc, rp.source["section_id"], rp.source["text"], "context",
                                         "Where the statement comes from"))
    return _base(question, "claim", outcome, headline, answer, reasons=reasons[:5], caveats=caveats,
                 evidence=evidence, checked_as=claim.text if claim else None, note=note,
                 claim_id=a.claim_id, assessment_id=a.id)


def check_claim(db: Session, question: str, text: str, *, rp: Rephrased | None = None, classifier_mode: str | None = None,
                retrieval_mode: str | None = None, max_evidence: int | None = None, fresh: bool = False) -> dict[str, Any]:
    import uuid

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
            return _base(question, "claim", "ERROR", "The check failed", a.error or "The check failed.",
                         checked_as=text, claim_id=claim.id, assessment_id=a.id)
    return answer_from_assessment(db, question, a, rp)


def passage_answer(db: Session, question: str) -> dict[str, Any] | None:
    """For questions no statement answers: the passages that best answer it, with their sources."""
    from app.retrieval.schemas import SearchFilters, SearchRequest
    from app.retrieval.service import get_search_service

    resp = get_search_service().search(SearchRequest(query=question[:500], mode="hybrid", size=15,
                                                     filters=SearchFilters(include_records=False)))
    q = stems(question) - _GENERIC
    role_bonus = {"decision": 0.5, "requirement": 0.3, "procedure_step": 0.3, "responsibility": 0.2, "discussion": 0.15,
                  "action": 0.1, "open_issue": 0.1, "header": -0.5, "purpose": -0.4, "background": -0.2}
    dated = sorted({h.doc_date for h in resp.hits if h.doc_date})
    scored = []
    for i, h in enumerate(resp.hits):
        shared = len(q & stems(h.text))
        if shared < 2 or h.status in ("superseded", "draft"):
            continue
        recency = (dated.index(h.doc_date) / max(1, len(dated) - 1)) if h.doc_date in dated else 0.0
        scored.append((shared / max(1, len(q)) + role_bonus.get(h.section_role, 0.0) + 0.3 * recency - 0.02 * i, h))
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
            evidence.append(_evidence(d, h.section_id, _trim(h.text, 360), "context", h.heading))
    top = evidence[0]
    return _base(question, "passage", "FOUND", "Here is what the files say",
                 f"{_who_said(top)} ({top['note']}): \"{_trim(top['quote'], 300)}\"",
                 reasons=[f"{_who_said(e)}: \"{_trim(e['quote'], 220)}\"" for e in evidence[1:]],
                 caveats=["This is the closest passage in the files; no single statement answers the question directly."],
                 evidence=evidence, note="Answered with the passages that best match your question.")


# ---------------------------------------------------------------- entry point

def answer(db: Session, question: str, *, classifier_mode: str | None = None, retrieval_mode: str | None = None,
           max_evidence: int | None = None, fresh: bool = False) -> dict[str, Any]:
    out = _answer(db, question, classifier_mode=classifier_mode, retrieval_mode=retrieval_mode,
                  max_evidence=max_evidence, fresh=fresh)
    _fill_sections(db, out.get("evidence") or [])
    return out


def _answer(db: Session, question: str, *, classifier_mode: str | None = None, retrieval_mode: str | None = None,
            max_evidence: int | None = None, fresh: bool = False) -> dict[str, Any]:
    question = re.sub(r"\s+", " ", (question or "").strip())
    record_ids = {d for (d,) in db.execute(select(Document.id).where(Document.kind == "record",
                                                                     Document.is_deleted.is_(False)))}
    refs = record_refs(question, lambda x: x in record_ids)
    if refs:
        return check_record(db, question, refs[0], refs[1:])
    subject = scan_subject(question)
    if subject:
        return scan(db, question, subject)
    from app.answers.rules_qa import topic_of

    topic = topic_of(question)
    if topic:
        return rule_question(db, question, topic)
    rp = rephrase(question, lambda q: _best_statement(db, q))
    if rp.claim is None:
        found = passage_answer(db, question)
        if found:
            return found
        if not rp.fallback:
            return _base(question, "none", "UNKNOWN", "No clear answer in the files", rp.note, note=rp.note)
        rp = Rephrased(rp.fallback, "Your question was turned into the statement it asks about.")
    try:
        return check_claim(db, question, rp.claim, rp=rp, classifier_mode=classifier_mode,
                           retrieval_mode=retrieval_mode, max_evidence=max_evidence, fresh=fresh)
    except ClaimValidationError as exc:
        return _base(question, "none", "UNKNOWN", "That can't be checked as written", str(exc), note=str(exc))
