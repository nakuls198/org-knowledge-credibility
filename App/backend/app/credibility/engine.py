"""Credibility engine (Stage A transparent baseline).

Inputs : the claim analysis, classified evidence items, relationships between the
         evidence documents, the configuration (config/credibility.yaml).
Outputs: a heuristic credibility score, verdict, evidence-weight interval, coverage,
         conflict, dependence clusters, temporal/governance analysis, missing
         information and next steps - with every feature and contribution recorded.

Nothing here is a calibrated probability. See evaluation/ for how the score tracks labels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from scipy.stats import beta as beta_dist

from app.claims.analysis import ClaimAnalysis
from app.core.text import jaccard, shingles, stem, tokenize
from app.reasoning.schemas import EvidenceContext, Judgement

Verdict = str  # SUPPORTED | PARTIALLY_SUPPORTED | CONTESTED | CONTRADICTED | INSUFFICIENT_EVIDENCE


@dataclass
class EvidenceItem:
    ctx: EvidenceContext
    judgement: Judgement
    retrieval: dict[str, Any] = field(default_factory=dict)
    features: dict[str, float] = field(default_factory=dict)
    weight: float = 0.0
    contribution: float = 0.0
    cluster_id: str | None = None


@dataclass
class DocInfo:
    id: str
    title: str
    doc_type: str
    status: str
    owner: str | None
    flags: list[str]
    doc_date: date | None
    effective_date: date | None
    superseded_date: date | None


@dataclass
class Edge:
    source: str
    target: str
    rel_type: str
    origin: str
    evidence: str
    attributes: dict[str, Any] = field(default_factory=dict)


class Cluster(BaseModel):
    id: str
    label: str
    members: list[int]  # evidence indexes
    documents: list[str]
    reasons: list[str]
    mass: float
    strongest: int


class GovernanceConflict(BaseModel):
    supporting: list[str]
    contradicting: list[str]
    resolution: str  # resolved_by_supersession | resolved_by_staleness | resolved_by_proposal_status | unresolved
    explanation: str
    governing_document: str | None = None


class CredibilityResult(BaseModel):
    method: str
    assessment_kind: str = "heuristic"  # never presented as a calibrated probability
    score: float
    verdict: str
    needs_review: bool
    review_reasons: list[str]
    interval: tuple[float, float]
    uncertainty_std: float
    support_mass: float
    contradict_mass: float
    ambiguous_count: int
    conflict: float
    coverage: float
    coverage_detail: dict[str, list[str]]
    independent_support: int
    independent_contradiction: int
    clusters: list[Cluster]
    practice_adherence: float | None = None
    temporal_notes: list[str] = Field(default_factory=list)
    governance: list[GovernanceConflict] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    as_of: str | None = None


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))
        self.why: dict[int, list[str]] = {}

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int, why: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra
            self.why.setdefault(ra, []).extend(self.why.pop(rb, []))
        self.why.setdefault(ra, []).append(why)


class CredibilityEngine:
    def __init__(self, cfg: dict[str, Any], embedder=None):
        self.cfg = cfg
        self.embedder = embedder

    # ---------------------------------------------------------------- features
    def item_features(self, item: EvidenceItem, analysis: ClaimAnalysis, doc: DocInfo, as_of: date | None) -> dict[str, float]:
        cfg = self.cfg
        j, ctx = item.judgement, item.ctx
        auth_table = cfg["authority"].get(analysis.claim_type, cfg["authority"]["factual"])
        authority = auth_table.get(j.scope, 0.3)
        dmod = cfg.get("doc_type_modifier", {})
        authority *= dmod.get(doc.doc_type, dmod.get("default", 1.0))

        t = cfg["temporal"]
        temporal, notes = 1.0, []
        current_claim = analysis.temporal_focus != "historical"
        if doc.status == "superseded" and current_claim:
            temporal *= t["superseded_current_claim"]
        if "potentially_stale" in doc.flags:
            temporal *= t["potentially_stale"]
        if "review_overdue" in doc.flags:
            temporal *= t["review_overdue"]
        if j.scope == "history" and current_claim:
            temporal *= t["history_section_current_claim"]
        if doc.doc_date is None:
            temporal *= t["undated"]
        if as_of and doc.doc_date and doc.doc_date > as_of:
            temporal = t["after_as_of"]

        pfields = cfg["provenance"]["fields"]
        present = {
            "date": doc.doc_date is not None,
            "author": bool(doc.owner or ctx.doc_people),
            "status": doc.status not in ("unspecified", ""),
            "source_path": bool(ctx.source_path),
        }
        if doc.doc_type in ("meeting_note", "interview", "communication"):
            present["status"] = True  # status is not a meaningful field for these document types
        frac = sum(present.get(f, False) for f in pfields) / max(1, len(pfields))
        floor = cfg["provenance"]["floor"]
        provenance = floor + (1 - floor) * frac
        return {"authority": round(authority, 4), "temporal": round(temporal, 4), "directness": round(j.directness, 4),
                "provenance": round(provenance, 4), "strength": round(j.strength, 4)}

    # -------------------------------------------------------------- clustering
    def clusters(self, items: list[EvidenceItem], edges: list[Edge]) -> tuple[_UF, list[str]]:
        dcfg = self.cfg["dependence"]
        uf = _UF(len(items))
        notes: list[str] = []
        by_doc: dict[str, list[int]] = {}
        for i, it in enumerate(items):
            by_doc.setdefault(it.ctx.document_id, []).append(i)
        for doc_id, idx in by_doc.items():
            for k in idx[1:]:
                uf.union(idx[0], k, f"same document {doc_id}")
        link_types = set(dcfg.get("link_types", []))
        for e in edges:
            if e.rel_type in link_types and e.source in by_doc and e.target in by_doc:
                uf.union(by_doc[e.source][0], by_doc[e.target][0], f"{e.source} {e.rel_type} {e.target} ({e.origin})")
                notes.append(f"{e.source} and {e.target} are linked ({e.rel_type}, {e.origin}); counted as one source lineage")
        sh = [shingles(it.ctx.span) for it in items]
        thr = float(dcfg.get("near_duplicate_jaccard", 0.5))
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                if items[a].ctx.document_id == items[b].ctx.document_id:
                    continue
                if items[a].ctx.doc_kind == "record" or items[b].ctx.doc_kind == "record":
                    continue  # templated system records look alike but record independent events
                sim = jaccard(sh[a], sh[b])
                if sim >= thr:
                    uf.union(a, b, f"near-duplicate wording (Jaccard {sim:.2f})")
                    notes.append(f"{items[a].ctx.document_id} and {items[b].ctx.document_id} repeat near-identical wording")
        if self.embedder is not None and len(items) > 1:
            try:
                vecs = self.embedder.encode([it.ctx.span[:500] for it in items])
                import numpy as np

                m = np.asarray(vecs)
                sims = m @ m.T
                cthr = float(dcfg.get("semantic_duplicate_cosine", 0.82))
                for a in range(len(items)):
                    for b in range(a + 1, len(items)):
                        if items[a].ctx.doc_kind == "record" or items[b].ctx.doc_kind == "record":
                            continue
                        if items[a].ctx.document_id != items[b].ctx.document_id and sims[a, b] >= cthr \
                                and items[a].judgement.label == items[b].judgement.label:
                            uf.union(a, b, f"semantically near-identical (cosine {sims[a, b]:.2f})")
                            notes.append(f"{items[a].ctx.document_id} and {items[b].ctx.document_id} make the same "
                                         f"statement in near-identical terms (cosine {sims[a, b]:.2f})")
            except Exception:  # noqa: BLE001 - dependence detection degrades gracefully
                pass
        same_person = set(dcfg.get("same_person_scopes", []))
        by_person: dict[str, int] = {}
        for i, it in enumerate(items):
            if it.judgement.scope in same_person and it.ctx.doc_people and it.ctx.doc_type == "interview":
                p = it.ctx.doc_people[0]
                if p in by_person:
                    uf.union(by_person[p], i, f"repeated statements by {p}")
                else:
                    by_person[p] = i
        return uf, sorted(set(notes))

    # ---------------------------------------------------------------- coverage
    @staticmethod
    def coverage(analysis: ClaimAnalysis, items: list[EvidenceItem]) -> tuple[float, dict[str, list[str]]]:
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
            v = f"{n.value:g}"
            aspects[f"value:{n.raw}"] = bool(re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", text))
        generic = {"requir", "approv", "process", "document", "polic", "current", "formal", "deal", "team", "without",
                   "within", "above", "below", "over", "under", "before", "after", "currently", "formally"}
        for t in analysis.content_terms:
            if len(aspects) >= 12:
                break
            if t in generic or t.isdigit() or any(t in k.lower() for k in aspects):
                continue
            aspects[f"term:{t}"] = t in stems
        if not aspects:
            return 0.0, {"covered": [], "missing": []}
        covered = [k for k, v in aspects.items() if v]
        missing = [k for k, v in aspects.items() if not v]
        return round(len(covered) / len(aspects), 3), {"covered": covered, "missing": missing}

    # ------------------------------------------------------------- governance
    @staticmethod
    def governance(items: list[EvidenceItem], docs: dict[str, DocInfo], edges: list[Edge]) -> list[GovernanceConflict]:
        formal = [it for it in items if it.judgement.scope in ("formal", "decision", "proposal")
                  and it.judgement.label in ("SUPPORTS", "CONTRADICTS")]
        sup = sorted({it.ctx.document_id for it in formal if it.judgement.label == "SUPPORTS"})
        con = sorted({it.ctx.document_id for it in formal if it.judgement.label == "CONTRADICTS"})
        out: list[GovernanceConflict] = []
        supersedes = {(e.source, e.target) for e in edges if e.rel_type == "supersedes"}
        implements = {(e.source, e.target) for e in edges if e.rel_type == "implements"}
        for s in sup:
            for c in con:
                if s == c:
                    continue
                ds, dc = docs.get(s), docs.get(c)
                if (s, c) in supersedes or (c, s) in supersedes:
                    gov = s if (s, c) in supersedes else c
                    out.append(GovernanceConflict(supporting=[s], contradicting=[c], resolution="resolved_by_supersession",
                                                  governing_document=gov,
                                                  explanation=f"{gov} explicitly supersedes {c if gov == s else s}, so {gov} governs."))
                elif ds and dc and (ds.status == "superseded" or dc.status == "superseded"):
                    gov = s if dc.status == "superseded" else c
                    out.append(GovernanceConflict(supporting=[s], contradicting=[c], resolution="resolved_by_supersession",
                                                  governing_document=gov,
                                                  explanation=f"{c if gov == s else s} is marked superseded; {gov} is the version in force."))
                elif ds and dc and ("potentially_stale" in ds.flags or "potentially_stale" in dc.flags):
                    stale = s if "potentially_stale" in ds.flags else c
                    other = c if stale == s else s
                    linked = (stale, other) in implements
                    out.append(GovernanceConflict(
                        supporting=[s], contradicting=[c], resolution="resolved_by_staleness", governing_document=other,
                        explanation=(f"{stale} predates the document it implements ({other})" if linked else
                                     f"{stale} is flagged as potentially out of date") +
                                    f"; {other} is treated as governing, but {stale} should be updated or confirmed by its owner."))
                elif ds and dc and ("draft" in (ds.status, dc.status)):
                    gov = s if dc.status == "draft" else c
                    out.append(GovernanceConflict(supporting=[s], contradicting=[c], resolution="resolved_by_proposal_status",
                                                  governing_document=gov,
                                                  explanation=f"{c if gov == s else s} is a draft that was never approved; {gov} remains in force."))
                else:
                    out.append(GovernanceConflict(supporting=[s], contradicting=[c], resolution="unresolved",
                                                  explanation=f"No recorded supersession, approval status or staleness establishes "
                                                              f"whether {s} or {c} governs; human review needed."))
        return out

    # -------------------------------------------------------------- aggregate
    def assess(self, analysis: ClaimAnalysis, items: list[EvidenceItem], docs: dict[str, DocInfo],
               edges: list[Edge], as_of: date | None = None) -> CredibilityResult:
        cfg = self.cfg
        for it in items:
            it.features = self.item_features(it, analysis, docs[it.ctx.document_id], as_of)
        # A later record that a tool / system named in the claim was replaced makes earlier descriptions of it stale.
        replaced_on = min((docs[it.ctx.document_id].doc_date for it in items
                           if it.judgement.label == "CONTRADICTS" and "replaced_by" in it.judgement.signals
                           and docs[it.ctx.document_id].doc_date), default=None)
        stale_factor = float(cfg["temporal"].get("replaced_later", 0.3))
        replaced_note = None
        if replaced_on and analysis.temporal_focus != "historical":
            for it in items:
                d = docs[it.ctx.document_id]
                if it.judgement.label == "SUPPORTS" and (d.doc_date is None or d.doc_date <= replaced_on):
                    it.features["temporal"] = round(it.features["temporal"] * stale_factor, 4)
                    it.judgement.signals["predates_replacement"] = str(replaced_on)
            src = next(it for it in items if "replaced_by" in it.judgement.signals
                       and docs[it.ctx.document_id].doc_date == replaced_on)
            replaced_note = (f"{src.ctx.document_id} ({replaced_on}) records that {src.judgement.signals['replaced_by']}; "
                             f"evidence from before then counts less.")
        for it in items:
            f = it.features
            it.weight = round(f["authority"] * f["temporal"] * f["directness"] * f["provenance"] * f["strength"], 5)
            sign = 1 if it.judgement.label == "SUPPORTS" else -1 if it.judgement.label == "CONTRADICTS" else 0
            it.contribution = sign * it.weight

        uf, dep_notes = self.clusters(items, edges)
        disc = float(cfg["dependence"]["dependent_discount"])
        groups: dict[tuple[int, str], list[int]] = {}
        for i, it in enumerate(items):
            if it.judgement.label in ("SUPPORTS", "CONTRADICTS"):
                groups.setdefault((uf.find(i), it.judgement.label), []).append(i)
        clusters: list[Cluster] = []
        S = C = 0.0
        for (root, label), members in sorted(groups.items(), key=lambda x: (x[0][1], x[0][0])):
            ws = sorted(((items[m].weight, m) for m in members), reverse=True)
            mass = ws[0][0] + disc * sum(w for w, _ in ws[1:])
            cid = f"{'S' if label == 'SUPPORTS' else 'C'}{len(clusters) + 1}"
            for m in members:
                items[m].cluster_id = cid
            clusters.append(Cluster(id=cid, label=label, members=members,
                                    documents=sorted({items[m].ctx.document_id for m in members}),
                                    reasons=sorted(set(uf.why.get(root, []))) or ["single source"],
                                    mass=round(mass, 4), strongest=ws[0][1]))
            if label == "SUPPORTS":
                S += mass
            else:
                C += mass
        # attribute cluster mass back to members (so contributions sum to S - C)
        for cl in clusters:
            total_w = sum(items[m].weight for m in cl.members) or 1.0
            sign = 1 if cl.label == "SUPPORTS" else -1
            for m in cl.members:
                items[m].contribution = round(sign * cl.mass * items[m].weight / total_w, 5)

        agg = cfg["aggregation"]
        a, b = float(agg["prior_support"]), float(agg["prior_contradict"])
        score = (S + a) / (S + C + a + b)
        lo, hi = beta_dist.interval(float(agg["interval"]), S + a, C + b)
        std = float(beta_dist.std(S + a, C + b))
        conflict = (min(S, C) / max(S, C)) if max(S, C) > 0 else 0.0
        coverage, cov_detail = self.coverage(analysis, items)

        v = cfg["verdict"]
        sup_items = [it for it in items if it.judgement.label == "SUPPORTS"]
        max_sup_auth = max((it.features["authority"] for it in sup_items), default=0.0)
        if S + C < v["min_mass"]:
            verdict = "INSUFFICIENT_EVIDENCE"
        elif coverage < v.get("min_coverage", 0.0):
            verdict = "INSUFFICIENT_EVIDENCE"  # the decisive passages do not address most of what the claim says
        elif score >= v["supported"]:
            partial = max_sup_auth < v["partial_max_authority"] or coverage < v["partial_min_coverage"]
            verdict = "PARTIALLY_SUPPORTED" if partial else "SUPPORTED"
        elif score <= v["contradicted"]:
            verdict = "CONTRADICTED"
        elif S >= v["contested_min_side"] and C >= v["contested_min_side"]:
            verdict = "CONTESTED"
        else:
            verdict = "INSUFFICIENT_EVIDENCE"

        # --- temporal notes / governance / caveats
        temporal_notes: list[str] = [replaced_note] if replaced_note else []
        seen_docs: set[str] = set()
        for it in items:
            d = docs[it.ctx.document_id]
            if d.id in seen_docs or it.judgement.label not in ("SUPPORTS", "CONTRADICTS", "AMBIGUOUS"):
                continue
            seen_docs.add(d.id)
            if d.status == "superseded":
                by = next((e.source for e in edges if e.rel_type == "supersedes" and e.target == d.id), None)
                temporal_notes.append(f"{d.id} is superseded" + (f" by {by}" if by else "") +
                                      (f" (from {d.superseded_date})" if d.superseded_date else "") +
                                      "; it is treated as historical, not current, evidence.")
            if "potentially_stale" in d.flags:
                temporal_notes.append(f"{d.id} (last updated {d.doc_date}) may be out of date relative to the policy it implements.")
            if d.status == "draft":
                temporal_notes.append(f"{d.id} is a draft that is not formally in force.")
            if "review_overdue" in d.flags:
                temporal_notes.append(f"{d.id} is past its review date.")
            if as_of and d.doc_date and d.doc_date > as_of:
                temporal_notes.append(f"{d.id} ({d.doc_date}) post-dates the as-of date {as_of} and was excluded.")
        props = [it for it in items if it.judgement.scope == "proposal" and it.judgement.label == "SUPPORTS"]
        if props and not any(it.judgement.scope in ("formal", "decision") and it.judgement.label == "SUPPORTS" for it in items):
            temporal_notes.append("Support comes only from proposals/drafts/pilots, which do not establish an implemented rule.")
        # For practice claims a gap between written rules and practice is the finding itself, not a
        # conflict about which document governs.
        governance = self.governance(items, docs, edges) if analysis.claim_type != "practice" else []

        caveats: list[str] = []
        practice_adherence = None
        if analysis.claim_type == "normative":
            ps = sum(it.weight for it in items if it.judgement.scope == "practice" and it.judgement.label == "SUPPORTS")
            pc = sum(it.weight for it in items if it.judgement.scope in ("practice", "testimony")
                     and it.judgement.label == "CONTRADICTS")
            if ps + pc > 0:
                practice_adherence = round(ps / (ps + pc), 3)
            if pc >= v["practice_caveat_mass"] and verdict in ("SUPPORTED", "PARTIALLY_SUPPORTED"):
                ex = sorted({it.ctx.document_id for it in items if it.judgement.scope in ("practice", "testimony")
                             and it.judgement.label == "CONTRADICTS"})
                caveats.append(f"The rule is documented, but practice deviates from it in {len(ex)} source(s) "
                               f"({', '.join(ex[:6])}).")
        if dep_notes:
            caveats.extend(dep_notes[:6])

        # --- review triggers
        review: list[str] = []
        if verdict == "CONTESTED":
            review.append("Supporting and contradicting evidence are both substantial.")
        for g in governance:
            if g.resolution == "unresolved":
                review.append(g.explanation)
            elif g.resolution == "resolved_by_staleness":
                review.append(f"Resolution relies on an inferred staleness flag: {g.explanation}")
        if verdict not in ("INSUFFICIENT_EVIDENCE",) and (hi - lo) > v["review_interval_width"]:
            review.append(f"Evidence-weight interval is wide ({lo:.2f}-{hi:.2f}).")
        if props and verdict in ("SUPPORTED", "PARTIALLY_SUPPORTED"):
            review.append("Support relies on a proposal or pilot rather than an approved rule.")
        if verdict in ("SUPPORTED", "PARTIALLY_SUPPORTED") and any(
                it.judgement.label == "CONTRADICTS" and it.features["authority"] >= 0.7 and it.features["temporal"] >= 0.6
                for it in items):
            review.append("At least one authoritative, current source contradicts the claim.")

        # --- missing information / next steps
        missing: list[str] = []
        if cov_detail["missing"]:
            missing.append("No decisive evidence mentions: " + ", ".join(m.split(":", 1)[1] for m in cov_detail["missing"][:6]))
        n_sup_clusters = sum(1 for c in clusters if c.label == "SUPPORTS")
        n_con_clusters = sum(1 for c in clusters if c.label == "CONTRADICTS")
        if n_sup_clusters == 1 and verdict in ("SUPPORTED", "PARTIALLY_SUPPORTED"):
            missing.append("Only one independent source lineage supports the claim.")
        if analysis.claim_type == "normative" and not any(it.judgement.scope == "formal" and it.judgement.label != "NEUTRAL"
                                                           for it in items):
            missing.append("No formal (policy / procedure) source addresses this rule.")
        if analysis.claim_type == "practice" and not any(it.ctx.doc_kind == "record" and it.judgement.label != "NEUTRAL"
                                                          for it in items):
            missing.append("No system record (CRM / task / audit) corroborates the described practice.")
        undated = sorted({it.ctx.document_id for it in items if it.judgement.label != "NEUTRAL" and docs[it.ctx.document_id].doc_date is None})
        if undated:
            missing.append(f"Undated evidence: {', '.join(undated[:5])}.")

        steps = self.next_steps(verdict, governance, items, docs, missing, caveats, analysis)
        return CredibilityResult(
            method=cfg.get("version", "baseline"), score=round(score, 4), verdict=verdict, needs_review=bool(review),
            review_reasons=list(dict.fromkeys(review)), interval=(round(float(lo), 4), round(float(hi), 4)), uncertainty_std=round(std, 4),
            support_mass=round(S, 4), contradict_mass=round(C, 4),
            ambiguous_count=sum(1 for it in items if it.judgement.label == "AMBIGUOUS"),
            conflict=round(conflict, 4), coverage=coverage, coverage_detail=cov_detail,
            independent_support=n_sup_clusters, independent_contradiction=n_con_clusters, clusters=clusters,
            practice_adherence=practice_adherence, temporal_notes=temporal_notes, governance=governance, caveats=caveats,
            missing_information=missing, next_steps=steps, as_of=as_of.isoformat() if as_of else None,
        )

    @staticmethod
    def next_steps(verdict, governance, items, docs, missing, caveats, analysis) -> list[str]:
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
            pol_owner = next((docs[it.ctx.document_id].owner for it in items
                              if it.judgement.scope == "formal" and it.judgement.label == "SUPPORTS" and docs[it.ctx.document_id].owner), None)
            steps.append(f"Review the documented deviations with {pol_owner or 'the policy owner'} and decide whether to "
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
        return steps[:6]
