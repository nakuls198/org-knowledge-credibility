"""Per-item weight features and claim coverage.

weight = authority(claim type, scope) x doc-type modifier x temporal x directness x provenance x strength
Every factor is in [0, 1] and configured in `config/credibility.yaml`.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from okc.claims.analysis import ClaimAnalysis
from okc.common.text import stem, tokenize
from okc.credibility.models import DocInfo, EvidenceItem

# Words too generic to count as an aspect of a claim that evidence must address.
_GENERIC_STEMS = {"requir", "approv", "process", "document", "polic", "current", "formal", "deal", "team", "without",
                  "within", "above", "below", "over", "under", "before", "after", "currently", "formally"}
_MAX_ASPECTS = 12
_STATUSLESS_TYPES = ("meeting_note", "interview", "communication")  # status is not a meaningful field for these


def item_features(cfg: dict[str, Any], item: EvidenceItem, analysis: ClaimAnalysis, doc: DocInfo,
                  as_of: date | None) -> dict[str, float]:
    j, ctx = item.judgement, item.ctx
    authority_table = cfg["authority"].get(analysis.claim_type, cfg["authority"]["factual"])
    modifiers = cfg.get("doc_type_modifier", {})
    authority = authority_table.get(j.scope, 0.3) * modifiers.get(doc.doc_type, modifiers.get("default", 1.0))
    return {"authority": round(authority, 4), "temporal": round(_temporal(cfg["temporal"], analysis, doc, j.scope, as_of), 4),
            "directness": round(j.directness, 4), "provenance": round(_provenance(cfg["provenance"], doc, ctx), 4),
            "strength": round(j.strength, 4)}


def _temporal(t: dict[str, float], analysis: ClaimAnalysis, doc: DocInfo, scope: str, as_of: date | None) -> float:
    factor = 1.0
    current_claim = analysis.temporal_focus != "historical"
    if doc.status == "superseded" and current_claim:
        factor *= t["superseded_current_claim"]
    if "potentially_stale" in doc.flags:
        factor *= t["potentially_stale"]
    if "review_overdue" in doc.flags:
        factor *= t["review_overdue"]
    if scope == "history" and current_claim:
        factor *= t["history_section_current_claim"]
    if doc.doc_date is None:
        factor *= t["undated"]
    if as_of and doc.doc_date and doc.doc_date > as_of:
        factor = t["after_as_of"]
    return factor


def _provenance(cfg: dict[str, Any], doc: DocInfo, ctx) -> float:
    """floor + (1 - floor) x the fraction of provenance fields present."""
    present = {
        "date": doc.doc_date is not None,
        "author": bool(doc.owner or ctx.doc_people),
        "status": doc.status not in ("unspecified", "") or doc.doc_type in _STATUSLESS_TYPES,
        "source_path": bool(ctx.source_path),
    }
    fields = cfg["fields"]
    fraction = sum(present.get(f, False) for f in fields) / max(1, len(fields))
    return cfg["floor"] + (1 - cfg["floor"]) * fraction


def coverage(analysis: ClaimAnalysis, items: list[EvidenceItem]) -> tuple[float, dict[str, list[str]]]:
    """How much of what the claim says (actors, values, key terms) the decisive evidence actually addresses."""
    decisive = [it for it in items if it.judgement.label in ("SUPPORTS", "CONTRADICTS")]
    text = " ".join(it.ctx.span for it in decisive)
    stems = {stem(t) for t in tokenize(text)}
    aspects: dict[str, bool] = {}
    for a in analysis.actors:
        key = a.person or a.role or a.function or a.mention
        aspects[f"actor:{key}"] = bool((a.person and a.person.split()[0] in text)
                                       or (a.role and a.role.lower() in text.lower())
                                       or a.mention.lower() in text.lower())
    for n in analysis.numbers:
        aspects[f"value:{n.raw}"] = bool(re.search(rf"(?<![\d.]){re.escape(f'{n.value:g}')}(?![\d])", text))
    for t in analysis.content_terms:
        if len(aspects) >= _MAX_ASPECTS:
            break
        if t in _GENERIC_STEMS or t.isdigit() or any(t in k.lower() for k in aspects):
            continue
        aspects[f"term:{t}"] = t in stems
    if not aspects:
        return 0.0, {"covered": [], "missing": []}
    covered = [k for k, v in aspects.items() if v]
    missing = [k for k, v in aspects.items() if not v]
    return round(len(covered) / len(aspects), 3), {"covered": covered, "missing": missing}
