"""Credibility engine (Stage A transparent baseline).

Inputs : the claim analysis, classified evidence items, relationships between the evidence documents,
         and the configuration (config/credibility.yaml).
Outputs: a heuristic credibility score, verdict, evidence-weight interval, coverage, conflict,
         dependence clusters, temporal / governance analysis, missing information and next steps,
         with every feature and contribution recorded.

Nothing here is a calibrated probability. See `evaluation/` for how the score tracks labels.

    weight_i = authority x doc-type modifier x temporal x directness x provenance x strength
    S, C     = cluster-aggregated support / contradiction mass (a cluster counts its strongest item
               fully and the rest at `dependent_discount`)
    score    = (S + a) / (S + C + a + b)             a = prior_support, b = prior_contradict
    interval = central interval of Beta(S + a, C + b)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from scipy.stats import beta as beta_dist

from okc.claims.analysis import ClaimAnalysis
from okc.credibility import guidance
from okc.credibility.dependence import UnionFind, cluster_items
from okc.credibility.features import coverage as claim_coverage
from okc.credibility.features import item_features
from okc.credibility.governance import find_conflicts
from okc.credibility.models import (
    Cluster,
    CredibilityResult,
    DocInfo,
    EvidenceItem,
    RelationEdge,
)

_MAX_DEPENDENCE_CAVEATS = 6


class CredibilityEngine:
    def __init__(self, cfg: dict[str, Any], embedder=None):
        self.cfg = cfg
        self.embedder = embedder

    def assess(self, analysis: ClaimAnalysis, items: list[EvidenceItem], docs: dict[str, DocInfo],
               edges: list[RelationEdge], as_of: date | None = None) -> CredibilityResult:
        cfg = self.cfg
        for it in items:
            it.features = item_features(cfg, it, analysis, docs[it.ctx.document_id], as_of)
        replaced_note = self._discount_evidence_before_replacement(analysis, items, docs)
        for it in items:
            f = it.features
            it.weight = round(f["authority"] * f["temporal"] * f["directness"] * f["provenance"] * f["strength"], 5)
            sign = 1 if it.judgement.label == "SUPPORTS" else -1 if it.judgement.label == "CONTRADICTS" else 0
            it.contribution = sign * it.weight

        uf, dependence_notes = cluster_items(cfg, items, edges, self.embedder)
        clusters, support, contradict = self._aggregate(items, uf)

        agg = cfg["aggregation"]
        a, b = float(agg["prior_support"]), float(agg["prior_contradict"])
        score = (support + a) / (support + contradict + a + b)
        lo, hi = beta_dist.interval(float(agg["interval"]), support + a, contradict + b)
        std = float(beta_dist.std(support + a, contradict + b))
        conflict = (min(support, contradict) / max(support, contradict)) if max(support, contradict) > 0 else 0.0
        coverage, coverage_detail = claim_coverage(analysis, items)
        verdict = self._verdict(score, support, contradict, coverage, items)

        v = cfg["verdict"]
        governance = find_conflicts(items, docs, edges) if analysis.claim_type != "practice" else []
        adherence, caveats = guidance.practice_gap(analysis, items, verdict, v["practice_caveat_mass"])
        caveats = caveats + dependence_notes[:_MAX_DEPENDENCE_CAVEATS]
        review = guidance.review_reasons(verdict, governance, (float(lo), float(hi)), v["review_interval_width"], items)
        missing = guidance.missing_information(analysis, items, docs, verdict, clusters, coverage_detail)
        return CredibilityResult(
            method=cfg.get("version", "baseline"), score=round(score, 4), verdict=verdict, needs_review=bool(review),
            review_reasons=list(dict.fromkeys(review)), interval=(round(float(lo), 4), round(float(hi), 4)),
            uncertainty_std=round(std, 4), support_mass=round(support, 4), contradict_mass=round(contradict, 4),
            ambiguous_count=sum(1 for it in items if it.judgement.label == "AMBIGUOUS"),
            conflict=round(conflict, 4), coverage=coverage, coverage_detail=coverage_detail,
            independent_support=sum(1 for c in clusters if c.label == "SUPPORTS"),
            independent_contradiction=sum(1 for c in clusters if c.label == "CONTRADICTS"), clusters=clusters,
            practice_adherence=adherence,
            temporal_notes=guidance.temporal_notes(items, docs, edges, as_of, replaced_note), governance=governance,
            caveats=caveats, missing_information=missing,
            next_steps=guidance.next_steps(verdict, governance, items, docs, missing, caveats),
            as_of=as_of.isoformat() if as_of else None,
        )

    # ------------------------------------------------------------------ steps
    def _discount_evidence_before_replacement(self, analysis: ClaimAnalysis, items: list[EvidenceItem],
                                              docs: dict[str, DocInfo]) -> str | None:
        """A later record that a tool / system named in the claim was replaced makes earlier descriptions of it stale."""
        replaced_on = min((docs[it.ctx.document_id].doc_date for it in items
                           if it.judgement.label == "CONTRADICTS" and "replaced_by" in it.judgement.signals
                           and docs[it.ctx.document_id].doc_date), default=None)
        if not replaced_on or analysis.temporal_focus == "historical":
            return None
        factor = float(self.cfg["temporal"].get("replaced_later", 0.3))
        for it in items:
            d = docs[it.ctx.document_id]
            if it.judgement.label == "SUPPORTS" and (d.doc_date is None or d.doc_date <= replaced_on):
                it.features["temporal"] = round(it.features["temporal"] * factor, 4)
                it.judgement.signals["predates_replacement"] = str(replaced_on)
        source = next(it for it in items if "replaced_by" in it.judgement.signals
                      and docs[it.ctx.document_id].doc_date == replaced_on)
        return (f"{source.ctx.document_id} ({replaced_on}) records that {source.judgement.signals['replaced_by']}; "
                f"evidence from before then counts less.")

    def _aggregate(self, items: list[EvidenceItem], uf: UnionFind) -> tuple[list[Cluster], float, float]:
        """Group decisive items into dependence clusters; returns (clusters, support mass S, contradiction mass C)."""
        discount = float(self.cfg["dependence"]["dependent_discount"])
        groups: dict[tuple[int, str], list[int]] = {}
        for i, it in enumerate(items):
            if it.judgement.label in ("SUPPORTS", "CONTRADICTS"):
                groups.setdefault((uf.find(i), it.judgement.label), []).append(i)
        clusters: list[Cluster] = []
        support = contradict = 0.0
        for (root, label), members in sorted(groups.items(), key=lambda x: (x[0][1], x[0][0])):
            ranked = sorted(((items[m].weight, m) for m in members), reverse=True)
            mass = ranked[0][0] + discount * sum(w for w, _ in ranked[1:])
            cluster_id = f"{'S' if label == 'SUPPORTS' else 'C'}{len(clusters) + 1}"
            for m in members:
                items[m].cluster_id = cluster_id
            clusters.append(Cluster(id=cluster_id, label=label, members=members,
                                    documents=sorted({items[m].ctx.document_id for m in members}),
                                    reasons=sorted(set(uf.reasons.get(root, []))) or ["single source"],
                                    mass=round(mass, 4), strongest=ranked[0][1]))
            if label == "SUPPORTS":
                support += mass
            else:
                contradict += mass
        # Attribute each cluster's mass back to its members, so contributions sum to S - C.
        for cluster in clusters:
            total = sum(items[m].weight for m in cluster.members) or 1.0
            sign = 1 if cluster.label == "SUPPORTS" else -1
            for m in cluster.members:
                items[m].contribution = round(sign * cluster.mass * items[m].weight / total, 5)
        return clusters, support, contradict

    def _verdict(self, score: float, support: float, contradict: float, coverage: float, items: list[EvidenceItem]) -> str:
        v = self.cfg["verdict"]
        max_support_authority = max((it.features["authority"] for it in items if it.judgement.label == "SUPPORTS"),
                                    default=0.0)
        if support + contradict < v["min_mass"]:
            return "INSUFFICIENT_EVIDENCE"
        if coverage < v.get("min_coverage", 0.0):
            return "INSUFFICIENT_EVIDENCE"  # the decisive passages do not address most of what the claim says
        if score >= v["supported"]:
            partial = max_support_authority < v["partial_max_authority"] or coverage < v["partial_min_coverage"]
            return "PARTIALLY_SUPPORTED" if partial else "SUPPORTED"
        if score <= v["contradicted"]:
            return "CONTRADICTED"
        if support >= v["contested_min_side"] and contradict >= v["contested_min_side"]:
            return "CONTESTED"
        return "INSUFFICIENT_EVIDENCE"
