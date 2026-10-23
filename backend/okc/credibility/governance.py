"""Governance: when sources disagree, which document governs (supersession, staleness, draft status)."""

from __future__ import annotations

from okc.credibility.models import DocInfo, EvidenceItem, GovernanceConflict, RelationEdge


def find_conflicts(items: list[EvidenceItem], docs: dict[str, DocInfo], edges: list[RelationEdge]) -> list[GovernanceConflict]:
    formal = [it for it in items if it.judgement.scope in ("formal", "decision", "proposal")
              and it.judgement.label in ("SUPPORTS", "CONTRADICTS")]
    supporting = sorted({it.ctx.document_id for it in formal if it.judgement.label == "SUPPORTS"})
    contradicting = sorted({it.ctx.document_id for it in formal if it.judgement.label == "CONTRADICTS"})
    supersedes = {(e.source, e.target) for e in edges if e.rel_type == "supersedes"}
    implements = {(e.source, e.target) for e in edges if e.rel_type == "implements"}
    return [_resolve(s, c, docs, supersedes, implements) for s in supporting for c in contradicting if s != c]


def _resolve(s: str, c: str, docs: dict[str, DocInfo], supersedes: set, implements: set) -> GovernanceConflict:
    ds, dc = docs.get(s), docs.get(c)
    if (s, c) in supersedes or (c, s) in supersedes:
        governing = s if (s, c) in supersedes else c
        return GovernanceConflict(supporting=[s], contradicting=[c], resolution="resolved_by_supersession",
                                  governing_document=governing,
                                  explanation=f"{governing} explicitly supersedes {c if governing == s else s}, so {governing} governs.")
    if ds and dc and (ds.status == "superseded" or dc.status == "superseded"):
        governing = s if dc.status == "superseded" else c
        return GovernanceConflict(supporting=[s], contradicting=[c], resolution="resolved_by_supersession",
                                  governing_document=governing,
                                  explanation=f"{c if governing == s else s} is marked superseded; {governing} is the version in force.")
    if ds and dc and ("potentially_stale" in ds.flags or "potentially_stale" in dc.flags):
        stale = s if "potentially_stale" in ds.flags else c
        other = c if stale == s else s
        reason = (f"{stale} predates the document it implements ({other})" if (stale, other) in implements
                  else f"{stale} is flagged as potentially out of date")
        return GovernanceConflict(
            supporting=[s], contradicting=[c], resolution="resolved_by_staleness", governing_document=other,
            explanation=f"{reason}; {other} is treated as governing, but {stale} should be updated or confirmed by its owner.")
    if ds and dc and "draft" in (ds.status, dc.status):
        governing = s if dc.status == "draft" else c
        return GovernanceConflict(supporting=[s], contradicting=[c], resolution="resolved_by_proposal_status",
                                  governing_document=governing,
                                  explanation=f"{c if governing == s else s} is a draft that was never approved; {governing} remains in force.")
    return GovernanceConflict(supporting=[s], contradicting=[c], resolution="unresolved",
                              explanation=f"No recorded supersession, approval status or staleness establishes "
                                          f"whether {s} or {c} governs; human review needed.")
