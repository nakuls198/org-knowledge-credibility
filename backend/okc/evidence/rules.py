"""Transparent rule-based evidence classifier.

For one claim and one evidence unit it reads named signals (see `okc.evidence.signals`) and turns
them into SUPPORTS / CONTRADICTS / NEUTRAL / AMBIGUOUS with an explicit rationale. Every signal is
stored with the assessment, so every label can be audited. These are heuristics; their accuracy is
measured in `evaluation/`, not assumed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from okc.claims.actors import Actor, Gazetteer
from okc.claims.analysis import ClaimAnalysis
from okc.evidence.schemas import EvidenceContext, Judgement
from okc.evidence.signals import CONDITIONAL, SignalExtractor, cues_in

_QUOTE_SPEAKER = re.compile(r"\b([A-Z][a-z]+)(?: [A-Z][a-z]+)?(?: \([^)]*\))?(?: said| says| replied| added| wrote)?:\s*[*_]*[\"“]")
_CONDITION_PHRASE = re.compile(r"\b(if|unless|provided|except|while|for (?:urgent|genuinely)|as long as|once)\b[^.;]{3,80}", re.I)
# How directly each scope speaks to what is true (before hedging).
_DIRECTNESS = {"formal": 1.0, "decision": 0.85, "practice": 0.85, "testimony": 0.7, "discussion": 0.6,
               "proposal": 0.5, "history": 0.6, "belief": 0.4}
_RECORD_DIRECTNESS = 0.9


@dataclass
class _Cues:
    """Cue-word lists found in the unit (lower-case substring lookups from the config lexicons)."""

    belief: list[str]
    practice: list[str]
    proposal: list[str]
    hedge: list[str]
    deviation: list[str]
    compliance: list[str]
    universal_denial: list[str]

    def as_signals(self) -> dict[str, list[str]]:
        named = {"belief_cues": self.belief, "practice_cues": self.practice, "proposal_cues": self.proposal,
                 "hedge_cues": self.hedge, "deviation_cues": self.deviation, "compliance_cues": self.compliance,
                 "universal_denial": self.universal_denial}
        return {k: v for k, v in named.items() if v}


@dataclass
class _Strength:
    """Accumulated support / conflict for one unit, with the reasons behind each."""

    support: float = 0.0
    conflict: float = 0.0
    support_reasons: list[str] = field(default_factory=list)
    conflict_reasons: list[str] = field(default_factory=list)

    def add_support(self, amount: float, why: str | None = None) -> None:
        self.support += amount
        if why:
            self.support_reasons.append(why)

    def bump_conflict(self, value: float, why: str) -> None:
        self.conflict = max(self.conflict, value)
        self.conflict_reasons.append(why)


class RulesClassifier:
    name = "rules"

    def __init__(self, cfg: dict[str, Any], gazetteer: Gazetteer, synonyms: dict[str, list[str]] | None = None):
        self.cfg = cfg
        self.gz = gazetteer
        self.th = cfg.get("thresholds", {})
        self.cues: dict[str, list[str]] = cfg.get("cues", {})
        self.version = cfg.get("classifier", {}).get("version", "rules")
        self.signals = SignalExtractor(cfg, gazetteer, synonyms)

    # ------------------------------------------------------------- classify
    def classify(self, analysis: ClaimAnalysis, ctx: EvidenceContext) -> Judgement:
        th = self.th
        text, rel = ctx.span, ctx.relevance
        signals: dict[str, Any] = {"relevance": round(rel, 3)}
        # A multi-step claim is often covered by a whole section (e.g. procedure steps) rather than one sentence.
        rel_support = max(rel, 0.85 * ctx.section_relevance) if ctx.doc_kind != "record" else rel
        if rel_support > rel:
            signals["section_relevance"] = round(ctx.section_relevance, 3)

        cues = self._read_cues(text)
        scope, modality = self.signals.scope(text, ctx, cues.belief, cues.proposal)
        directness = self._directness(scope, ctx, bool(cues.hedge))
        signals.update(cues.as_signals())
        conditions = [m.group(0).strip() for m in _CONDITION_PHRASE.finditer(text)][:4]

        if rel < th.get("min_relevance", 0.12):
            return Judgement(label="NEUTRAL", scope=scope, strength=0.0, directness=directness, modality=modality,
                             conditions=conditions, rationale="Passage shares too little with the claim to bear on it.",
                             signals=signals, classifier=self.version)

        numbers = self.signals.numeric_signals(analysis, text, ctx)
        signals.update(numbers)
        self._actor_signals(analysis, ctx, text, signals)
        polarity, polarity_overlap, polarity_clause = (
            (None, 0.0, "") if ctx.doc_kind == "record" else self.signals.polarity(analysis, text))
        if polarity:
            signals["polarity"] = {"result": polarity, "overlap": round(polarity_overlap, 2), "clause": polarity_clause[:160]}
        replaced = self.signals.replacement(analysis, text) if rel >= 0.15 else None
        if replaced:
            signals["replaced_by"] = replaced
        if analysis.dates and any(d in text or (ctx.doc_date == d) for d in analysis.dates):
            signals["date_match"] = [d for d in analysis.dates if d in text or ctx.doc_date == d]

        claim_deviation = bool(cues_in(analysis.normalized, self.cues.get("deviation", []) + self.cues.get("claim_deviation_words", [])))
        existential = analysis.quantifier == "existential"

        strength = _Strength()
        self._support_strength(strength, rel, rel_support, numbers, signals, cues, claim_deviation, polarity, polarity_overlap)
        self._conflict_strength(strength, analysis, ctx, rel, numbers, signals, cues, claim_deviation, existential,
                                polarity, polarity_overlap, polarity_clause, replaced)
        if "condition_not_met" in numbers or "complement_condition" in numbers:
            strength.support *= 0.3
            strength.conflict *= 0.3
            signals["condition_scope"] = numbers.get("condition_not_met") or numbers.get("complement_condition")
        decisive = ("value_superseded" in numbers or "numeric_mismatch" in numbers or bool(replaced)
                    or (polarity == "conflict" and polarity_overlap >= 0.5 and not CONDITIONAL.search(polarity_clause)))
        if decisive and strength.conflict >= 0.6:
            strength.support = min(strength.support, 0.3)  # a contradicted key value / negated predicate outweighs topical overlap
            signals["decisive_conflict"] = True
        support, conflict = min(1.0, strength.support), min(1.0, strength.conflict)
        signals["support_score"] = round(support, 3)
        signals["conflict_score"] = round(conflict, 3)

        label, label_strength, rationale = self._decide(support, conflict, rel, strength, cues)
        if cues.hedge and label in ("SUPPORTS", "CONTRADICTS"):
            rationale += f" Statement is hedged ({', '.join(cues.hedge[:2])})."
        if scope == "proposal" and label == "SUPPORTS":
            rationale += " Note: this is a proposal/draft, not an implemented rule."
        return Judgement(label=label, scope=scope, strength=round(min(1.0, label_strength), 4),
                         directness=round(directness, 3), modality=modality, conditions=conditions,
                         rationale=rationale, signals=signals, classifier=self.version)

    # ---------------------------------------------------------------- steps
    def _read_cues(self, text: str) -> _Cues:
        def found(key: str) -> list[str]:
            return cues_in(text, self.cues.get(key, []))

        return _Cues(belief=found("belief"), practice=found("practice"), proposal=found("proposal"), hedge=found("hedge"),
                     deviation=found("deviation"), compliance=found("compliance"), universal_denial=found("universal_denial"))

    def _directness(self, scope: str, ctx: EvidenceContext, hedged: bool) -> float:
        directness = _RECORD_DIRECTNESS if ctx.doc_kind == "record" else _DIRECTNESS.get(scope, 0.6)
        return directness * self.th.get("hedge_factor", 0.6) if hedged else directness

    def _speaker(self, ctx: EvidenceContext, text: str) -> Actor | None:
        """Who is speaking in an interview or a quoted chat turn (for first-person statements)."""
        if ctx.doc_type == "interview" and ctx.doc_people:
            person = ctx.doc_people[0]
            info = self.gz.people.get(person, {})
            return Actor(mention=person, kind="person", person=person, role=info.get("role"), function=info.get("function"))
        quote = _QUOTE_SPEAKER.search(text)
        return self.gz.speaker_of(quote.group(1) + ": x") if quote else None

    def _actor_signals(self, analysis: ClaimAnalysis, ctx: EvidenceContext, text: str, signals: dict[str, Any]) -> None:
        """actor_match / actor_conflict (who approves) and bypass_match (who is skipped)."""
        claim_agents, claim_bypassed = self.signals.claim_agents(analysis)
        unit_agents, unit_bypassed = self.signals.agents(text, self.gz.find(text), first_person=self._speaker(ctx, text))
        if claim_agents and unit_agents:
            primary = claim_agents[0]
            relations = [self.gz.relation(primary, a) for a in unit_agents]
            if any(r in ("match", "compatible") for r in relations):
                signals["actor_match"] = f"{primary.key()} ~ {', '.join(a.key() for a in unit_agents)}"
            elif any(r == "conflict" for r in relations):
                signals["actor_conflict"] = f"claim: {primary.key()}; passage: {', '.join(a.key() for a in unit_agents)}"
        if claim_bypassed and unit_bypassed and any(
                self.gz.relation(claim_bypassed[0], b) in ("match", "compatible") for b in unit_bypassed):
            signals["bypass_match"] = f"{claim_bypassed[0].key()} is bypassed in both"

    def _support_strength(self, s: _Strength, rel, rel_support, numbers, signals, cues, claim_deviation, polarity, polarity_overlap) -> None:
        if rel_support >= self.th.get("lexical_support", 0.5):
            s.support = min(0.8, 0.45 + 0.7 * (rel_support - 0.5))
        elif rel_support >= 0.25:
            s.support = 0.15 + 1.2 * (rel_support - 0.25)
        if s.support >= 0.3:
            s.support_reasons.append(f"substantial wording overlap with the claim (relevance {rel_support:.2f})")
        if "numeric_match" in numbers and rel < 0.25:
            # the same number in an unrelated context ("30-day reminder" vs "30-day trial") is a coincidence
            signals["numeric_coincidence"] = numbers.pop("numeric_match")
            signals.pop("numeric_match", None)
        if "numeric_match" in numbers:
            s.add_support(0.35, numbers["numeric_match"])
        if "within_limit" in numbers:
            s.add_support(0.2, numbers["within_limit"])
        if "actor_match" in signals:
            s.add_support(0.3, f"same responsible actor ({signals['actor_match']})")
        if "bypass_match" in signals:
            s.add_support(0.2, signals["bypass_match"])
        if claim_deviation and cues.deviation:
            s.add_support(0.35, f"describes the same informal practice ({', '.join(cues.deviation[:3])})")
            signals["deviation_aligned"] = cues.deviation
        if "condition_applies" in numbers and not cues.deviation and cues.compliance:
            s.add_support(0.25, f"{numbers['condition_applies']} and the record shows compliance ({', '.join(cues.compliance[:2])})")
        elif "condition_applies" in numbers and not cues.deviation and "actor_match" in signals:
            s.add_support(0.15)
        if "date_match" in signals:
            s.add_support(0.2, f"date {', '.join(signals['date_match'])} matches")
        if polarity == "agree" and polarity_overlap >= 0.5:
            s.add_support(0.1)

    def _conflict_strength(self, s: _Strength, analysis, ctx, rel, numbers, signals, cues, claim_deviation, existential,
                           polarity, polarity_overlap, polarity_clause, replaced) -> None:
        # An existential claim ("sometimes ...") is not refuted by a single counter-example.
        can_refute = not existential or bool(cues.universal_denial)
        if "value_superseded" in numbers:
            s.bump_conflict(0.8, numbers["value_superseded"])
        if replaced:
            s.bump_conflict(0.75, replaced)
        if "numeric_mismatch" in numbers:
            s.bump_conflict(0.75, numbers["numeric_mismatch"])
        if "numeric_mismatch_instance" in numbers and rel >= 0.3:
            s.bump_conflict(0.5, numbers["numeric_mismatch_instance"])
        if "actor_conflict" in signals and can_refute:
            s.bump_conflict(0.6 + (0.1 if ctx.doc_kind == "record" else 0.0),
                            f"a different actor approves/decides ({signals['actor_conflict']})")
        if "cap_violation" in numbers and can_refute:
            s.bump_conflict(0.6, numbers["cap_violation"])
        if polarity == "conflict":
            s.bump_conflict(0.45 + 0.4 * min(1.0, polarity_overlap), f"the passage negates the claim (\"{polarity_clause[:90]}\")")
        rule_like = analysis.claim_type == "normative" or (analysis.quantifier == "universal" and analysis.claim_type == "practice")
        if cues.deviation and not claim_deviation and rule_like and rel >= 0.25:
            s.bump_conflict(0.55, f"describes a workaround / deviation ({', '.join(cues.deviation[:3])})")
        # A compliant instance does not refute "sometimes ..."; only a universal denial ("no exceptions") does.
        if claim_deviation and (cues.universal_denial or (cues.compliance and not existential)) and not cues.deviation and rel >= 0.25:
            s.bump_conflict(0.5 if cues.universal_denial else 0.35,
                            f"asserts compliance / no exceptions ({', '.join((cues.universal_denial or cues.compliance)[:2])})")

    def _decide(self, support: float, conflict: float, rel: float, s: _Strength, cues: _Cues) -> tuple[str, float, str]:
        th = self.th
        rel_factor = 0.5 + 0.5 * min(1.0, rel / 0.6)
        hedge_factor = th.get("hedge_factor", 0.6) if cues.hedge else 1.0
        margin = th.get("ambiguity_margin", 0.12)
        if support >= 0.45 and conflict >= 0.45 and abs(support - conflict) < margin:
            return ("AMBIGUOUS", max(support, conflict) * 0.5,
                    f"Mixed signals: supports ({'; '.join(s.support_reasons[:2])}) but also conflicts ({'; '.join(s.conflict_reasons[:2])}).")
        if conflict >= th.get("contradict_label", 0.35) and conflict >= support:
            return ("CONTRADICTS", conflict * rel_factor * hedge_factor,
                    "Conflicts with the claim: " + "; ".join(s.conflict_reasons[:3]) + ".")
        if support >= th.get("support_label", 0.35):
            return "SUPPORTS", support * hedge_factor, "Supports the claim: " + "; ".join(s.support_reasons[:3]) + "."
        if cues.hedge and rel >= 0.3:
            return "AMBIGUOUS", 0.2, f"Relevant but hedged / uncertain ({', '.join(cues.hedge[:2])})."
        return "NEUTRAL", 0.0, "Related to the claim's topic but neither confirms nor disputes it."
