"""Combines the rules classifier with optional NLI / LLM signals.

Modes (config/reasoning.yaml -> classifier.mode, overridable per assessment):
  rules      deterministic rules only (always available)
  rules+nli  rules, with an NLI cross-encoder able to fill in NEUTRAL cases and flag disagreements
  llm        LLM label/scope; rule signals still recorded for audit
  llm+rules  LLM label unless it disagrees with a strong rule signal -> AMBIGUOUS
A component that is unavailable falls back to `rules`; the fallback is recorded.
"""

from __future__ import annotations

from typing import Any

from app.claims.analysis import ClaimAnalysis
from app.reasoning.nli import NliScorer
from app.reasoning.rules import RulesClassifier
from app.reasoning.schemas import EvidenceContext, Judgement

_DIRECTNESS = {"direct": 1.0, "indirect": 0.7, "hearsay": 0.4}


class EvidenceClassifier:
    def __init__(self, rules: RulesClassifier, cfg: dict[str, Any], nli: NliScorer | None = None, llm=None,
                 mode: str | None = None):
        self.rules = rules
        self.cfg = cfg
        self.nli = nli
        self.llm = llm
        requested = mode or cfg.get("classifier", {}).get("mode", "rules")
        self.notes: list[str] = []
        if "nli" in requested and not (nli and nli.available):
            self.notes.append(f"NLI unavailable ({getattr(nli, 'error', 'disabled')}); using rules")
            requested = requested.replace("+nli", "") or "rules"
        if requested.startswith("llm") and not (llm and llm.available):
            self.notes.append(f"LLM unavailable ({getattr(llm, 'error', 'disabled')}); using rules")
            requested = "rules"
        self.mode = requested

    @property
    def version(self) -> dict[str, Any]:
        v = {"mode": self.mode, "rules": self.rules.version}
        if "nli" in self.mode and self.nli:
            v["nli_model"] = self.nli.model_name
        if self.mode.startswith("llm") and self.llm:
            v["llm"] = self.llm.version
        return v

    def classify_all(self, analysis: ClaimAnalysis, contexts: list[EvidenceContext]) -> list[Judgement]:
        judgements = [self.rules.classify(analysis, c) for c in contexts]
        if self.mode == "rules+nli":
            self._apply_nli(analysis, contexts, judgements)
        elif self.mode.startswith("llm"):
            # A local model on CPU is slow (~40 s per passage), so only the most relevant passages are
            # sent to it; the rest keep their rule-based label (recorded in the signals).
            limit = int(self.cfg.get("llm", {}).get("max_units", 8))
            floor = self.cfg.get("thresholds", {}).get("min_relevance", 0.12)
            ranked = sorted(range(len(contexts)), key=lambda k: -judgements[k].signals.get("relevance", 0))
            chosen = {k for k in ranked[:limit] if judgements[k].signals.get("relevance", 0) >= floor}
            for i, (ctx, j) in enumerate(zip(contexts, judgements)):
                if i in chosen:
                    judgements[i] = self._apply_llm(analysis, ctx, j)
                elif j.signals.get("relevance", 0) >= floor:
                    j.signals["llm_skipped"] = f"not among the {limit} most relevant passages; rule-based label kept"
        return judgements

    # Rule signals that rest on an explicit, checkable fact in the passage. Everything else
    # (word overlap, cue words) is "soft" and may be overridden by the NLI model.
    HARD_SUPPORT = ("numeric_match", "actor_match", "date_match", "within_limit")
    HARD_CONFLICT = ("value_superseded", "numeric_mismatch", "actor_conflict", "cap_violation", "decisive_conflict",
                     "replaced_by")

    def _apply_nli(self, analysis, contexts, judgements) -> None:
        cfg = self.cfg.get("nli", {})
        ent_thr = cfg.get("entail_override", 0.85)
        con_thr = cfg.get("contradict_override", 0.85)
        min_topic = cfg.get("min_topic_relevance", 0.25)
        idx = [i for i, j in enumerate(judgements) if j.signals.get("relevance", 0) >= 0.12]
        scores = self.nli.score([(contexts[i].span[:1000], analysis.normalized) for i in idx])
        # A claim that summarises several steps may be entailed by the whole section but by no single unit.
        sec_idx = [i for i in idx if contexts[i].span_end - contexts[i].span_start < len(contexts[i].section_text)
                   and len(contexts[i].section_text) <= 1500 and contexts[i].doc_kind != "record"]
        sec_scores = dict(zip(sec_idx, self.nli.score([(contexts[i].section_text, analysis.normalized) for i in sec_idx])))
        for i, sc in zip(idx, scores):
            j = judgements[i]
            j.signals["nli"] = {k: round(v, 3) for k, v in sc.items()}
            ent, con = sc.get("entailment", 0), sc.get("contradiction", 0)
            if i in sec_scores and sec_scores[i].get("entailment", 0) >= ent_thr > ent and con < con_thr:
                ent = sec_scores[i]["entailment"]
                j.signals["nli_section"] = {k: round(v, 3) for k, v in sec_scores[i].items()}
                ctx = contexts[i]
                ctx.span_start, ctx.span_end = 0, len(ctx.section_text)  # the whole section is the evidence
            topical = max(j.signals.get("relevance", 0), j.signals.get("section_relevance", 0)) >= min_topic
            if con >= con_thr and not topical:
                # NLI models tend to call unrelated text "contradiction"; require topical overlap first
                j.signals["nli_ignored"] = "contradiction on an off-topic passage"
                con = 0.0
            hard_s = [s for s in self.HARD_SUPPORT if s in j.signals]
            hard_c = [s for s in self.HARD_CONFLICT if s in j.signals]
            before = j.label
            if j.label in ("NEUTRAL", "AMBIGUOUS") and ent >= ent_thr and not hard_c:
                j.label, j.strength = "SUPPORTS", round(max(j.strength, 0.75 * ent), 4)
                j.rationale = f"The NLI model judges that the passage entails the claim (entailment {ent:.2f})."
            elif j.label in ("NEUTRAL", "AMBIGUOUS") and con >= con_thr and not hard_s:
                j.label, j.strength = "CONTRADICTS", round(max(j.strength, 0.7 * con), 4)
                j.rationale = f"The NLI model judges that the passage contradicts the claim (contradiction {con:.2f})."
            elif j.label == "SUPPORTS" and con >= con_thr:
                if hard_s:
                    j.label = "AMBIGUOUS"
                    j.rationale += f" However the NLI model reads it as contradicting the claim (contradiction {con:.2f})."
                else:
                    j.label, j.strength = "CONTRADICTS", round(0.7 * con, 4)
                    j.rationale = (f"Shares the claim's wording, but the NLI model judges that it contradicts the claim "
                                   f"(contradiction {con:.2f}).")
            elif j.label == "CONTRADICTS" and ent >= ent_thr:
                if hard_c:
                    j.label = "AMBIGUOUS"
                    j.rationale += f" However the NLI model reads it as entailing the claim (entailment {ent:.2f})."
                else:
                    j.label, j.strength = "SUPPORTS", round(0.7 * ent, 4)
                    j.rationale = f"The NLI model judges that the passage entails the claim (entailment {ent:.2f}); rule cues were weak."
            if j.label != before:
                j.signals["nli_changed_label"] = f"{before} -> {j.label}"
            j.classifier = f"{j.classifier}+nli"

    def _apply_llm(self, analysis: ClaimAnalysis, ctx: EvidenceContext, rule_j: Judgement) -> Judgement:
        stance, meta = self.llm.classify(analysis.normalized, ctx)
        signals = {**rule_j.signals, "rules_label": rule_j.label, "rules_rationale": rule_j.rationale, "llm": meta}
        if stance is None:
            signals["llm_fallback"] = "LLM call failed; rules label kept"
            return rule_j.model_copy(update={"signals": signals})
        signals["llm"] = {**meta, "label": stance.label, "scope": stance.scope, "directness": stance.directness}
        label = stance.label
        if self.mode == "llm+rules":
            strong_rule = rule_j.label in ("SUPPORTS", "CONTRADICTS") and max(
                rule_j.signals.get("support_score", 0), rule_j.signals.get("conflict_score", 0)) >= 0.7
            if strong_rule and label in ("SUPPORTS", "CONTRADICTS") and label != rule_j.label:
                label = "AMBIGUOUS"
                signals["disagreement"] = f"LLM={stance.label}, rules={rule_j.label}"
        strength = {"SUPPORTS": 0.7, "CONTRADICTS": 0.7, "AMBIGUOUS": 0.25, "NEUTRAL": 0.0}[label]
        directness = _DIRECTNESS.get(stance.directness, 0.7)
        # Scope: keep metadata-derived scope for draft/superseded/records (facts about the document, not judgements)
        scope = rule_j.scope if (ctx.status in ("draft", "superseded") or ctx.doc_kind == "record") else stance.scope
        rationale = stance.rationale.strip()
        if stance.evidence_quote:
            rationale += f' Quote: "{stance.evidence_quote[:200]}"'
        return Judgement(label=label, scope=scope, strength=strength, directness=directness, modality=rule_j.modality,
                         conditions=stance.conditions or rule_j.conditions, rationale=rationale, signals=signals,
                         classifier=self.llm.version if self.mode == "llm" else f"{self.llm.version}+rules")
