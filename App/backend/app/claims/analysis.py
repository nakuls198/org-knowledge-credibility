"""Claim validation, normalisation and analysis (deterministic).

Normalisation never changes meaning: whitespace and typographic quotes are
standardised, nothing is paraphrased. Analysis extracts the parts reasoning
needs: actors, numeric thresholds/conditions, dates, identifiers, quantifier,
claim type (normative rule vs. observed practice vs. factual statement).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.claims.gazetteer import Actor, Gazetteer
from app.core.text import STOPWORDS, content_terms, stem, tokenize

ClaimType = Literal["normative", "practice", "factual"]
Quantifier = Literal["universal", "existential", "unspecified"]


class NumericCondition(BaseModel):
    value: float
    unit: str  # "%", "$", "days", "business days", "weeks", "hours", "months", "years", "count"
    comparator: str  # gt | gte | lt | lte | eq | cap
    raw: str


class ClaimAnalysis(BaseModel):
    original: str
    normalized: str
    warnings: list[str] = Field(default_factory=list)
    claim_type: ClaimType
    claim_type_reason: str
    quantifier: Quantifier
    negated: bool
    content_terms: list[str]
    key_phrases: list[str]
    actors: list[Actor]
    numbers: list[NumericCondition]
    dates: list[str]
    years: list[int]
    identifiers: list[str]
    temporal_focus: Literal["current", "historical", "unspecified"]
    conditions: list[str]


class ClaimValidationError(ValueError):
    pass


_NORMATIVE = re.compile(
    r"\b(must|shall|requires?|required|requirement|needs? to|has to|have to|is (?:only )?approved by|approv\w+ (?:by|from)|"
    r"authority|accountable|responsible for|owns?|owned by|sole approver|mandatory|policy|capped|cap|documented rule|"
    r"formally|formal|tie-?breaker|assigns?|is expected|are expected|documented|standard|follows?|following a|"
    r"procedure|process|guidelines?|is the sole|governs?)\b", re.I)
_PRACTICE = re.compile(
    r"\b(in practice|usually|typically|often|sometimes|frequently|routinely|commonly|informally|actually|"
    r"tend to|tends to|habitually|in reality|as a rule of thumb|themselves|herself|himself|directly|treated as|"
    r"are handled|is handled)\b", re.I)
_EVENT = re.compile(r"\b(was|were|has been|have been|had been)\s+(?:\w+ly\s+)?\w+(?:ed|en|ne|un|ght)\b", re.I)
_MODAL = re.compile(r"\b(must|shall|requires?|required|needs? to|has to|have to|may only|is expected)\b", re.I)
_UNIVERSAL = re.compile(r"\b(all|every|each|always|never|no one|none|any|only|sole|must|requires?|required)\b", re.I)
_EXISTENTIAL = re.compile(r"\b(some|sometimes|often|occasionally|can|may|at least one|frequently|informally|several)\b", re.I)
# Negation of the claim's main predicate only. "without X" states a condition, it does not negate the claim.
_NEGATION = re.compile(r"\b(not|never|none|nobody|no one|cannot|can't|isn't|aren't|wasn't|weren't|doesn't|don't|didn't|"
                       r"hasn't|haven't|won't|no longer)\b|\b(?:is|are|was|were|has|have|had) no\b|^no\b", re.I)
_CURRENT = re.compile(r"\b(current(?:ly)?|now|today|at present|presently|still|as of)\b", re.I)
_HISTORICAL = re.compile(r"\b(previously|formerly|used to|before \d{4}|in (?:19|20)\d{2}|was|were|had)\b", re.I)

_NUM = re.compile(
    r"(?P<cmp>(?:more than|greater than|over|above|exceeding|at least|or more|at or above|under|below|less than|"
    r"up to|at most|within|capped at|cap of|no more than|of)\s+)?"
    r"(?P<cur>\$)?(?P<val>\d+(?:[.,]\d+)?)[\s-]?(?P<unit>%|percent|per cent|business days?|days?|weeks?|hours?|months?|years?)?"
    r"(?P<post>\s+(?:or more|or greater|or above|and above|or less|or under|and below))?", re.I)
_CMP = {"more than": "gt", "greater than": "gt", "over": "gt", "above": "gt", "exceeding": "gt", "at least": "gte",
        "at or above": "gte", "under": "lt", "below": "lt", "less than": "lt", "up to": "lte", "at most": "lte",
        "within": "lte", "capped at": "cap", "cap of": "cap", "no more than": "lte", "of": "eq"}
_ID = re.compile(r"\b[A-Z]{2,8}-\d{2,4}\b")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_CONDITION = re.compile(r"\b(?:if|when|unless|except|once|before|after|for|within|above|over|below|under)\b[^,.;]{3,60}", re.I)


def normalize(text: str) -> str:
    t = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


def validate(text: str) -> tuple[str, list[str]]:
    norm = normalize(text)
    warnings: list[str] = []
    words = tokenize(norm)
    if len(norm) < 10 or len(words) < 3:
        raise ClaimValidationError("A claim needs at least three words and ten characters.")
    if len(norm) > 600:
        raise ClaimValidationError("A claim must be at most 600 characters; split compound claims into separate claims.")
    if norm.endswith("?"):
        warnings.append("Phrased as a question; it is assessed as the statement it implies.")
    if len(re.findall(r"\b(and|but)\b", norm, re.I)) >= 3:
        warnings.append("Compound claim: parts may be supported to different degrees; consider splitting it.")
    return norm, warnings


def parse_numbers(text: str) -> list[NumericCondition]:
    out: list[NumericCondition] = []
    for m in _NUM.finditer(text):
        unit = (m.group("unit") or "").lower()
        if m.group("cur"):
            unit = "$"
        elif unit in ("percent", "per cent"):
            unit = "%"
        elif unit:
            unit = re.sub(r"s$", "", unit)
            unit = {"business day": "business days", "day": "days", "week": "weeks", "hour": "hours",
                    "month": "months", "year": "years"}.get(unit, unit)
        if not unit:
            continue  # bare numbers (years, ids) are not thresholds
        cmp_raw = (m.group("cmp") or "").strip().lower()
        post = (m.group("post") or "").strip().lower()
        comparator = _CMP.get(cmp_raw, "eq")
        if post in ("or more", "or greater", "or above", "and above"):
            comparator = "gte"
        elif post in ("or less", "or under", "and below"):
            comparator = "lte"
        try:
            val = float(m.group("val").replace(",", ""))
        except ValueError:
            continue
        out.append(NumericCondition(value=val, unit=unit, comparator=comparator, raw=m.group(0).strip()))
    return out


def analyze(text: str, gazetteer: Gazetteer | None = None) -> ClaimAnalysis:
    norm, warnings = validate(text)
    lowered = norm.lower()
    practice = _PRACTICE.search(norm)
    normative = _NORMATIVE.search(norm)
    event = _EVENT.search(norm)
    if event and not _MODAL.search(norm):
        ctype, reason = "factual", f"past-tense event '{event.group(0)}'"
    elif practice:
        ctype, reason = "practice", f"practice cue '{practice.group(0)}'"
    elif normative:
        ctype, reason = "normative", f"normative cue '{normative.group(0)}'"
    else:
        ctype, reason = "factual", "no normative or practice cue; treated as a factual statement"
    uni, exi = _UNIVERSAL.search(norm), _EXISTENTIAL.search(norm)
    if exi and not re.search(r"\b(every|all|always|never)\b", norm, re.I):
        quant = "existential"
    elif uni or ctype == "normative":
        quant = "universal"
    else:
        quant = "unspecified"
    terms = list(dict.fromkeys(content_terms(norm)))
    key_phrases = [w for w in tokenize(norm) if w not in STOPWORDS and len(w) > 3][:12]
    temporal = "current" if _CURRENT.search(norm) else ("historical" if _HISTORICAL.search(norm) and _YEAR.search(norm) else "unspecified")
    return ClaimAnalysis(
        original=text, normalized=norm, warnings=warnings, claim_type=ctype, claim_type_reason=reason,
        quantifier=quant, negated=bool(_NEGATION.search(lowered)), content_terms=terms, key_phrases=key_phrases,
        actors=gazetteer.find(norm) if gazetteer else [], numbers=parse_numbers(norm),
        dates=_DATE.findall(norm), years=[int(y.group(0)) for y in _YEAR.finditer(norm)],
        identifiers=sorted(set(_ID.findall(norm))), temporal_focus=temporal,
        conditions=[m.group(0).strip() for m in _CONDITION.finditer(norm)][:6],
    )


def expand_query(analysis: ClaimAnalysis, synonyms: dict[str, list[str]]) -> str:
    """Claim text + synonym expansion (retrieval only; the claim itself is never rewritten)."""
    extra: list[str] = []
    stems = set(analysis.content_terms)
    for key, alts in synonyms.items():
        if stem(key) in stems:
            extra.extend(alts)
    for a in analysis.actors:
        extra.extend(x for x in (a.person, a.role) if x)
    return analysis.normalized + (" " + " ".join(dict.fromkeys(extra)) if extra else "")
