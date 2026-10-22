"""Plain answers to questions about the organisation."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.session import get_db

router = APIRouter()


class AnswerRequest(BaseModel):
    question: str = Field(min_length=3, max_length=600, description="Any question or statement about the organisation")
    classifier_mode: Literal["rules", "rules+nli", "llm", "llm+rules"] | None = None
    retrieval_mode: Literal["bm25", "semantic", "hybrid"] | None = None
    max_evidence: int | None = Field(default=None, ge=4, le=60)
    fresh: bool = Field(default=False, description="Re-run the check even if an up-to-date result exists")


@router.post("/answer", tags=["answers"], summary="Answer a question in plain language, with the evidence behind it")
def post_answer(body: AnswerRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Record questions (\"Is DEAL-027 a violation of policy?\") are checked against the policy rulebook;
    \"which deals broke the discount policy?\" scans every record; anything else is checked as a claim.
    The response always has: outcome, headline, answer, reasons, caveats, evidence (with sources)."""
    from app.answers.service import answer

    return answer(db, body.question, classifier_mode=body.classifier_mode, retrieval_mode=body.retrieval_mode,
                  max_evidence=body.max_evidence, fresh=body.fresh)
