"""Human-facing guidance derived from an assessment: temporal notes, caveats, review triggers,
missing information and suggested next steps."""

from __future__ import annotations

from datetime import date
from typing import Any

from okc.claims.analysis import ClaimAnalysis
from okc.credibility.models import Cluster, DocInfo, EvidenceItem, GovernanceConflict, RelationEdge

_SUPPORTED_VERDICTS = ("SUPPORTED", "PARTIALLY_SUPPORTED")
_MAX_NEXT_STEPS = 6


def temporal_notes(items: list[EvidenceItem], docs: dict[str, DocInfo], edges: list[RelationEdge],
                   as_of: date | None, replaced_note: str | None) -> list[str]:
    notes: list[str] = [replaced_note] if replaced_note else []
    seen: set[str] = set()
    for it in items:
        d = docs[it.ctx.document_id]
        if d.id in seen or it.judgement.label not in ("SUPPORTS", "CONTRADICTS", "AMBIGUOUS"):
            continue
        seen.add(d.id)
        if d.status == "superseded":
            by = next((e.source for e in edges if e.rel_type == "supersedes" and e.target == d.id), None)
            notes.append(f"{d.id} is superseded" + (f" by {by}" if by else "") +
                         (f" (from {d.superseded_date})" if d.superseded_date else "") +
                         "; it is treated as historical, not current, evidence.")
        if "potentially_stale" in d.flags:
            notes.append(f"{d.id} (last updated {d.doc_date}) may be out of date relative to the policy it implements.")
        if d.status == "draft":
            notes.append(f"{d.id} is a draft that is not formally in force.")
        if "review_overdue" in d.flags:
            notes.append(f"{d.id} is past its review date.")
        if as_of and d.doc_date and d.doc_date > as_of:
            notes.append(f"{d.id} ({d.doc_date}) post-dates the as-of date {as_of} and was excluded.")
    if proposals_only(items):
        notes.append("Support comes only from proposals/drafts/pilots, which do not establish an implemented rule.")
    return notes


def proposal_support(items: list[EvidenceItem]) -> list[EvidenceItem]:
    return [it for it in items if it.judgement.scope == "proposal" and it.judgement.label == "SUPPORTS"]


def proposals_only(items: list[EvidenceItem]) -> bool:
    """Support exists, but only from proposals / drafts / pilots."""
    return bool(proposal_support(items)) and not any(
        it.judgement.scope in ("formal", "decision") and it.judgement.label == "SUPPORTS" for it in items)


def practice_gap(analysis: ClaimAnalysis, items: list[EvidenceItem], verdict: str,
                 practice_caveat_mass: float) -> tuple[float | None, list[str]]:
    """For rule claims: how often practice follows the rule, and a caveat when it clearly does not."""
    if analysis.claim_type != "normative":
        return None, []
    followed = sum(it.weight for it in items if it.judgement.scope == "practice" and it.judgement.label == "SUPPORTS")
    deviated = sum(it.weight for it in items if it.judgement.scope in ("practice", "testimony")
                   and it.judgement.label == "CONTRADICTS")
    adherence = round(followed / (followed + deviated), 3) if followed + deviated > 0 else None
    caveats: list[str] = []
    if deviated >= practice_caveat_mass and verdict in _SUPPORTED_VERDICTS:
        sources = sorted({it.ctx.document_id for it in items if it.judgement.scope in ("practice", "testimony")
                          and it.judgement.label == "CONTRADICTS"})
        caveats.append(f"The rule is documented, but practice deviates from it in {len(sources)} source(s) "
                       f"({', '.join(sources[:6])}).")
    return adherence, caveats


def review_reasons(verdict: str, governance: list[GovernanceConflict], interval: tuple[float, float],
                   max_interval_width: float, items: list[EvidenceItem]) -> list[str]:
    lo, hi = interval
    reasons: list[str] = []
    if verdict == "CONTESTED":
        reasons.append("Supporting and contradicting evidence are both substantial.")
    for g in governance:
        if g.resolution == "unresolved":
            reasons.append(g.explanation)
        elif g.resolution == "resolved_by_staleness":
            reasons.append(f"Resolution relies on an inferred staleness flag: {g.explanation}")
    if verdict != "INSUFFICIENT_EVIDENCE" and (hi - lo) > max_interval_width:
        reasons.append(f"Evidence-weight interval is wide ({lo:.2f}-{hi:.2f}).")
    if verdict in _SUPPORTED_VERDICTS and proposal_support(items):
        reasons.append("Support relies on a proposal or pilot rather than an approved rule.")
    if verdict in _SUPPORTED_VERDICTS and any(
            it.judgement.label == "CONTRADICTS" and it.features["authority"] >= 0.7 and it.features["temporal"] >= 0.6
            for it in items):
        reasons.append("At least one authoritative, current source contradicts the claim.")
    return reasons


def missing_information(analysis: ClaimAnalysis, items: list[EvidenceItem], docs: dict[str, DocInfo], verdict: str,
                        clusters: list[Cluster], coverage_detail: dict[str, list[str]]) -> list[str]:
    missing: list[str] = []
    if coverage_detail["missing"]:
        missing.append("No decisive evidence mentions: " + ", ".join(m.split(":", 1)[1] for m in coverage_detail["missing"][:6]))
    if sum(1 for c in clusters if c.label == "SUPPORTS") == 1 and verdict in _SUPPORTED_VERDICTS:
        missing.append("Only one independent source lineage supports the claim.")
    if analysis.claim_type == "normative" and not any(
            it.judgement.scope == "formal" and it.judgement.label != "NEUTRAL" for it in items):
        missing.append("No formal (policy / procedure) source addresses this rule.")
    if analysis.claim_type == "practice" and not any(
            it.ctx.doc_kind == "record" and it.judgement.label != "NEUTRAL" for it in items):
        missing.append("No system record (CRM / task / audit) corroborates the described practice.")
    undated = sorted({it.ctx.document_id for it in items
                      if it.judgement.label != "NEUTRAL" and docs[it.ctx.document_id].doc_date is None})
    if undated:
        missing.append(f"Undated evidence: {', '.join(undated[:5])}.")
    return missing


def next_steps(verdict: str, governance: list[GovernanceConflict], items: list[EvidenceItem], docs: dict[str, DocInfo],
               missing: list[str], caveats: list[str]) -> list[str]:
    steps: list[str] = []
    for g in governance:
        if g.resolution == "unresolved":
            owners = sorted({docs[d].owner for d in g.supporting + g.contradicting if docs.get(d) and docs[d].owner})
            steps.append(f"Ask {' / '.join(owners) or 'the document owners'} which of "
                         f"{', '.join(g.supporting + g.contradicting)} governs, and retire or update the other.")
        elif g.resolution == "resolved_by_staleness":
            stale = next((d for d in g.supporting + g.contradicting if d != g.governing_document), None)
            if stale and docs.get(stale):
                steps.append(f"Ask {docs[stale].owner or 'the owner'} to update {stale} so it matches {g.governing_document}.")
    if any("practice deviates" in c for c in caveats):
        policy_owner = next((docs[it.ctx.document_id].owner for it in items
                             if it.judgement.scope == "formal" and it.judgement.label == "SUPPORTS"
                             and docs[it.ctx.document_id].owner), None)
        steps.append(f"Review the documented deviations with {policy_owner or 'the policy owner'} and decide whether to "
                     f"enforce the rule or formalise an exception.")
    if verdict == "INSUFFICIENT_EVIDENCE":
        steps.append("Locate a governing document or system record for this claim; none in the indexed corpus "
                     "addresses it directly.")
    beliefs = [it for it in items if it.judgement.scope == "belief" and it.judgement.label == "SUPPORTS"]
    if beliefs and verdict != "SUPPORTED":
        steps.append(f"Verify second-hand statements ({', '.join(sorted({b.ctx.document_id for b in beliefs})[:4])}) "
                     f"against the governing document.")
    if any("Only one independent source" in m for m in missing):
        steps.append("Seek independent corroboration (a system record or a source that does not derive from the same origin).")
    if verdict == "CONTESTED":
        steps.append("Escalate for human review: interview the people whose accounts conflict and check system records.")
    return steps[:_MAX_NEXT_STEPS]
