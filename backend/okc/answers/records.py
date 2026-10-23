"""Answers that come from the policy rulebook: one record, a scan of all records of a kind, or a rule question."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from okc.answers import compliance as cp
from okc.answers.evidence import (
    around,
    audit_by_record,
    base_answer,
    evidence_item,
    first_section,
    mentions,
    record_line,
    rule_evidence,
    trim,
    type_label,
)
from okc.answers.rules_qa import answer_rule_question
from okc.assessment.investigate import get_gazetteer
from okc.db.models import Document
from okc.settings import load_config

_MAX_UNCLEAR_LISTED = 12
_MAX_SCAN_EVIDENCE_RECORDS = 4


def _policy_rules(rb: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """policy id -> {policy, section, rule_text} for every rule in the rulebook (for citing the rule)."""
    rules = {v["policy"]: v for v in rb["discount_new_business"]["versions"]}
    rules[rb["discount_renewal"]["policy"]] = rb["discount_renewal"]
    rules[rb["expense_approval"]["policy"]] = rb["expense_approval"]
    return {p: {"policy": p, "section": r["section"], "rule_text": ""} for p, r in rules.items()}


def _approvals(rb: dict[str, Any], audit: dict[str, list[dict[str, Any]]], rid: str) -> list[dict[str, Any]]:
    return [a for a in audit.get(rid, []) if a.get("action") == rb["approval_audit_action"]]


def check_record(db: Session, question: str, rid: str, others: list[str] | None = None) -> dict[str, Any]:
    rb = load_config("compliance")
    doc = db.get(Document, rid)
    audit = audit_by_record(db)
    finding = cp.check_record(doc.doc_type, doc.meta or {}, rb, get_gazetteer(db).lookup, _approvals(rb, audit, rid))

    section = first_section(db, rid)
    evidence = [evidence_item(doc, section.id if section else None, record_line(doc), "record", "The record")]
    rule = rule_evidence(db, finding.rule)
    if rule:
        evidence.append(rule)
    for a in audit.get(rid, [])[:3]:
        audit_doc = db.get(Document, a["_doc_id"])
        if audit_doc:
            evidence.append(evidence_item(audit_doc, None, record_line(audit_doc), "audit", "System audit log"))
    mention_notes = []
    for d, s in mentions(db, rid, {rid}):
        quote = around(s.plain_text, rid)
        evidence.append(evidence_item(d, s.id, quote, "mention", f"Mentions {rid}"))
        mention_notes.append(f"{d.id} ({type_label(d.doc_type).lower()}{', ' + cp.fmt_date(d.doc_date) if d.doc_date else ''}) "
                             f"mentions {rid}: \"{trim(quote, 220)}\"")
    caveats = list(finding.caveats)
    if others:
        caveats.append(f"Your question also mentions {', '.join(others)}; ask about each separately for its own check.")
    return base_answer(question, "record", finding.outcome, f"{rid}: {finding.headline}", finding.answer,
                       reasons=list(finding.reasons) + mention_notes[:2], caveats=caveats, evidence=evidence,
                       facts=[list(x) for x in finding.facts], checked_as=rid,
                       note=f"Checked {rid} against {finding.rule['policy']}." if finding.rule else f"Showed what {rid} records.")


def findings(db: Session, subject: str) -> list[tuple[Document, cp.Finding]]:
    """Every record of a kind ('deals' / 'expenses') checked against the rulebook."""
    rb = load_config("compliance")
    lookup, audit = get_gazetteer(db).lookup, audit_by_record(db)
    if subject == "deals":
        docs = db.scalars(select(Document).where(Document.doc_type == rb["discount_new_business"]["record_type"],
                                                 Document.is_deleted.is_(False)).order_by(Document.id)).all()
    else:
        expense = rb["expense_approval"]
        docs = [d for d in db.scalars(select(Document).where(Document.doc_type == expense["record_type"],
                                                              Document.is_deleted.is_(False)).order_by(Document.id))
                if (d.meta or {}).get("process_or_capability") == expense["process"]]
    return [(d, cp.check_record(d.doc_type, d.meta or {}, rb, lookup, _approvals(rb, audit, d.id))) for d in docs]


def _scan_policies(rb: dict[str, Any], subject: str) -> list[str]:
    if subject == "deals":
        newest = sorted(rb["discount_new_business"]["versions"], key=lambda v: str(v["effective_from"]))[-1]
        return [newest["policy"], rb["discount_renewal"]["policy"]]
    return [rb["expense_approval"]["policy"]]


def scan(db: Session, question: str, subject: str) -> dict[str, Any]:
    rb = load_config("compliance")
    what = load_config("answers")["scan_subjects"][subject]["label"]
    policies = _scan_policies(rb, subject)
    checked = [(d, f) for d, f in findings(db, subject) if f.outcome not in ("NOT_APPLICABLE", "NO_RULE")]
    by_outcome = {o: [(d, f) for d, f in checked if f.outcome == o]
                  for o in ("VIOLATION", "LIKELY_VIOLATION", "CANNOT_TELL", "COMPLIANT")}
    bad, likely, unclear, ok = (by_outcome[o] for o in ("VIOLATION", "LIKELY_VIOLATION", "CANNOT_TELL", "COMPLIANT"))
    n_bad = len(bad) + len(likely)
    if n_bad:
        headline = (f"{n_bad} of {len(checked)} {what} {'breaks' if n_bad == 1 else 'break'} a policy rule"
                    + (f" ({len(likely)} not confirmed)" if likely else ""))
        outcome = "VIOLATION"
    else:
        headline, outcome = f"No policy breaches found in {len(checked)} {what}", "COMPLIANT"
    parts = [p for p in (f"{len(bad)} broke a rule" if bad else "",
                         f"{len(likely)} probably did (no approval recorded)" if likely else "",
                         f"{len(ok)} followed the rules" if ok else "",
                         f"{len(unclear)} can't be checked because a field is missing" if unclear else "") if p]
    summary = ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0] if parts else "none apply"
    answer = f"Checked all {len(checked)} {what} against {' and '.join(policies)}: {summary}."

    rules = _policy_rules(rb)
    evidence = [e for e in (rule_evidence(db, rules[p]) for p in policies) if e]
    for d, f in (bad + likely)[:_MAX_SCAN_EVIDENCE_RECORDS]:
        section = first_section(db, d.id)
        evidence.append(evidence_item(d, section.id if section else None, record_line(d), "record", f.headline))
    caveats = []
    if unclear:
        noun = what if len(unclear) > 1 else what[:-1]
        caveats.append(f"{len(unclear)} {noun} could not be checked because the record is missing a field the rule needs "
                       f"(amount or approver): " + ", ".join(d.id for d, _ in unclear[:_MAX_UNCLEAR_LISTED])
                       + (" …" if len(unclear) > _MAX_UNCLEAR_LISTED else "") + ".")
    return base_answer(question, "scan", outcome, headline, answer,
                       reasons=[f"{d.id}: {f.answer}" for d, f in bad + likely], caveats=caveats, evidence=evidence,
                       items=[{"record": d.id, "outcome": f.outcome, "headline": f.headline, "answer": f.answer}
                              for d, f in bad + likely + unclear + ok],
                       checked_as=f"all {what}", note=f"Every {what[:-1]} was checked against the rulebook.")


def rule_question(db: Session, question: str, topic: str) -> dict[str, Any]:
    rb = load_config("compliance")
    practice = {k: [(d.id, f) for d, f in findings(db, k)] for k in ("deals", "expenses")}
    answer = answer_rule_question(question, topic, rb, get_gazetteer(db), practice)
    rules = _policy_rules(rb)
    evidence = [e for e in (rule_evidence(db, rules[p]) for p in answer.policies) if e]
    for rid in answer.practice_records[:3]:
        d = db.get(Document, rid)
        if d:
            section = first_section(db, rid)
            evidence.append(evidence_item(d, section.id if section else None, record_line(d), "practice",
                                          "What actually happened"))
    return base_answer(question, "rule", answer.outcome, answer.headline, answer.answer, reasons=answer.reasons,
                       caveats=answer.caveats, evidence=evidence, checked_as=", ".join(answer.policies),
                       note="Answered from the policy rulebook, then checked against the records.")
