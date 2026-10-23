"""Plain answers to questions about the organisation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from okc.answers.service import answer
from okc.api.schemas import AnswerRequest
from okc.db.session import get_db

router = APIRouter()


@router.post("/answer", tags=["answers"], summary="Answer a question in plain language, with the evidence behind it")
def post_answer(body: AnswerRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Record questions (\"Is DEAL-027 a violation of policy?\") are checked against the policy rulebook;
    \"which deals broke the discount policy?\" scans every record; anything else is checked as a claim.
    The response always has: outcome, headline, answer, reasons, caveats, evidence (with sources)."""
    return answer(db, body.question, classifier_mode=body.classifier_mode, retrieval_mode=body.retrieval_mode,
                  max_evidence=body.max_evidence, fresh=body.fresh)
