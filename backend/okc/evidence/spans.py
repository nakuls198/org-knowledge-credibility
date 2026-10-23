"""Evidence-unit selection: which sentence / list item / chat turn of a section bears on the claim.

Relevance is a transparent, weighted term-overlap score (not a probability):
  relevance = sum(w(t) for claim terms t found in the unit) / sum(w(t) for all claim terms)
with w(t) = an IDF-style weight over the candidate pool, synonyms counted at half weight,
plus small bonuses for matching actors, numbers and identifiers.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from okc.claims.analysis import ClaimAnalysis
from okc.common.text import content_terms, split_units, stem, tokenize

_MIN_UNIT_CHARS = 8
_WHOLE_SECTION_CHARS = 220


def term_weights(analysis: ClaimAnalysis, pool_texts: list[str]) -> dict[str, float]:
    n = max(1, len(pool_texts))
    doc_freq: Counter[str] = Counter()
    for t in pool_texts:
        doc_freq.update(set(content_terms(t)))
    actor_tokens = {stem(w) for a in analysis.actors for w in tokenize(a.mention)}
    # Words that only name an actor ("head", "ops") are matched by the actor signals; counting them
    # again here would make any participant list look relevant.
    return {term: (math.log(1 + n / (1 + doc_freq.get(term, 0))) + 0.2) * (0.3 if term in actor_tokens else 1.0)
            for term in analysis.content_terms}


def unit_relevance(unit: str, analysis: ClaimAnalysis, weights: dict[str, float],
                   synonyms: dict[str, list[str]]) -> tuple[float, list[str]]:
    terms = set(content_terms(unit))
    total = sum(weights.values()) or 1.0
    got, matched = 0.0, []
    synonym_stems = {stem(k): {stem(w) for alt in v for w in alt.split()} for k, v in synonyms.items()}
    for t, w in weights.items():
        if t in terms:
            got += w
            matched.append(t)
        elif synonym_stems.get(t) and synonym_stems[t] & terms:
            got += 0.5 * w
            matched.append(f"~{t}")
    score = got / total
    if analysis.identifiers and any(i in unit for i in analysis.identifiers):
        score += 0.15
    for num in analysis.numbers:
        value = str(int(num.value)) if num.value.is_integer() else str(num.value)
        if re.search(rf"(?<![\d.]){re.escape(value)}(?![\d])", unit):
            score += 0.08
            break
    low = unit.lower()
    for actor in analysis.actors:
        if actor.person and actor.person.split()[0] in unit or actor.role and actor.role.lower() in low:
            score += 0.05
            break
    return min(score, 1.0), matched


def best_units(section_text: str, analysis: ClaimAnalysis, weights: dict[str, float],
               synonyms: dict[str, list[str]], k: int, is_record: bool = False) -> list[tuple[int, int, float, list[str]]]:
    """Top-k (start, end, relevance, matched_terms) evidence units of a section."""
    if is_record or len(section_text) <= _WHOLE_SECTION_CHARS:
        units = [(0, len(section_text))]
    else:
        units = split_units(section_text)
    scored = []
    for s, e in units:
        unit = section_text[s:e]
        if len(unit.strip()) < _MIN_UNIT_CHARS:
            continue
        relevance, matched = unit_relevance(unit, analysis, weights, synonyms)
        scored.append((s, e, relevance, matched))
    scored.sort(key=lambda x: (-x[2], x[0]))
    return scored[:k]
