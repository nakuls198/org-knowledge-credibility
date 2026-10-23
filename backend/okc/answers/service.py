"""Answer a question about the organisation in plain language.

  question names a record (DEAL-027)  -> record compliance check (answers.records)
  "which deals broke the policy?"     -> scan every record of that kind
  a rule question                     -> answered from the rulebook, then checked against the records
  anything else                       -> turned into a claim and checked by the evidence pipeline,
                                         then summarised as a direct answer with its evidence

Every answer has the same shape: an outcome, a one-line headline, a direct answer, the reasons behind
it, caveats, and the few pieces of evidence that prove it (each with its source).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from okc.answers.claims import best_statement, check_claim, passage_answer
from okc.answers.evidence import base_answer, fill_sections
from okc.answers.records import check_record, rule_question, scan
from okc.answers.rules_qa import topic_of
from okc.answers.understand import Rephrased, record_refs, rephrase, scan_subject
from okc.claims.analysis import ClaimValidationError
from okc.db.models import Document


def answer(db: Session, question: str, *, classifier_mode: str | None = None, retrieval_mode: str | None = None,
           max_evidence: int | None = None, fresh: bool = False) -> dict[str, Any]:
    out = _route(db, question, classifier_mode=classifier_mode, retrieval_mode=retrieval_mode,
                 max_evidence=max_evidence, fresh=fresh)
    fill_sections(db, out.get("evidence") or [])
    return out


def _route(db: Session, question: str, *, classifier_mode: str | None, retrieval_mode: str | None,
           max_evidence: int | None, fresh: bool) -> dict[str, Any]:
    question = re.sub(r"\s+", " ", (question or "").strip())
    record_ids = {d for (d,) in db.execute(select(Document.id).where(Document.kind == "record",
                                                                     Document.is_deleted.is_(False)))}
    refs = record_refs(question, lambda x: x in record_ids)
    if refs:
        return check_record(db, question, refs[0], refs[1:])
    subject = scan_subject(question)
    if subject:
        return scan(db, question, subject)
    topic = topic_of(question)
    if topic:
        return rule_question(db, question, topic)

    rp = rephrase(question, lambda q: best_statement(db, q))
    if rp.claim is None:
        found = passage_answer(db, question)
        if found:
            return found
        if not rp.fallback:
            return base_answer(question, "none", "UNKNOWN", "No clear answer in the files", rp.note, note=rp.note)
        rp = Rephrased(rp.fallback, "Your question was turned into the statement it asks about.")
    try:
        return check_claim(db, question, rp.claim, rp=rp, classifier_mode=classifier_mode,
                           retrieval_mode=retrieval_mode, max_evidence=max_evidence, fresh=fresh)
    except ClaimValidationError as exc:
        return base_answer(question, "none", "UNKNOWN", "That can't be checked as written", str(exc), note=str(exc))
