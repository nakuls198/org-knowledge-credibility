"""Transparent rule-based evidence classifier.

For one claim and one evidence unit it computes named signals, e.g.
  numeric_match / numeric_mismatch / value_superseded / cap_violation / condition_not_met
  actor_match / actor_conflict           (who approves / decides / owns)
  polarity_conflict                       (the unit negates the claim's predicate)
  deviation / deviation_aligned / compliance   (workarounds vs. compliance language)
  replaced_by                             (a tool / system the claim names was migrated away from or retired)
  lexical overlap (relevance)
and turns them into SUPPORTS / CONTRADICTS / NEUTRAL / AMBIGUOUS with an explicit
rationale. All signals are stored with the assessment, so every label can be audited.
These are heuristics; their accuracy is measured in evaluation/, not assumed.
"""

from __future__ import annotations

import re
from typing import Any

from app.claims.analysis import ClaimAnalysis, NumericCondition, parse_numbers
from app.claims.gazetteer import Actor, Gazetteer
from app.core.text import content_terms, stem, tokenize
from app.reasoning.schemas import EvidenceContext, Judgement

_CLAUSE_SPLIT = re.compile(r"(?<=[.;!?])\s+|\s+(?:but|however|whereas|although)\s+|\s+[—–-]{1,2}\s+")
_BYPASS_BEFORE = re.compile(r"(without(?: waiting(?: for| on)?)?|instead of(?: routing(?: \w+)?(?: to)?)?|rather than(?: routing(?: \w+)?(?: to)?)?|"
                            r"don'?t wait on|not wait(?:ing)? (?:on|for)|waiting (?:on|for))\s+(?:the\s+)?$", re.I)
_FIRST_PERSON = re.compile(r"\b(I|I'll|I'd|I've|I'm|my|me)\b")
_QUOTE_SPEAKER = re.compile(r"\b([A-Z][a-z]+)(?: [A-Z][a-z]+)?(?: \([^)]*\))?(?: said| says| replied| added| wrote)?:\s*[*_]*[\"“]")
_CONDITIONAL = re.compile(r"\b(if|unless|would|surprised)\b", re.I)
# "X was archived / replaced", "migrated from X to Y", "re-entered X cards into Y", "replaced X with Y"
_RETIRED = r"(?:archived|retired|deprecated|decommissioned|replaced|sunset|phased out|shut down|no longer used)"
_MOVE = r"(?:migrat\w*|mov(?:e|ed|es|ing)|switch\w*|transition\w*|re-?entered|port(?:ed|ing)|transferr?\w*)"
_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z0-9]{2,})\b")
_NOT_NAMES = {"The", "This", "That", "These", "Those", "Head", "Finance", "Sales", "People", "Ops", "Support", "Lead", "Team",
              "Account", "Executive", "Customer", "Success", "Delivery", "Engineering", "Product", "Manager", "Northfield",
              "Closed", "Won", "Lost", "Sev", "Policy", "Process", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December", "Monday", "Friday", "Slack", "Email"}
_CONDITION_PHRASE = re.compile(r"\b(if|unless|provided|except|while|for (?:urgent|genuinely)|as long as|once)\b[^.;]{3,80}", re.I)


def _cues(text: str, cues: list[str]) -> list[str]:
    low = text.lower()
    return [c for c in cues if c in low]


def _direction(cmp: str) -> str:
    return {"gt": "up", "gte": "up", "lt": "down", "lte": "down", "cap": "down"}.get(cmp, "eq")


class RulesClassifier:
    name = "rules"

    def __init__(self, cfg: dict[str, Any], gazetteer: Gazetteer):
        self.cfg = cfg
        self.cues: dict[str, list[str]] = cfg.get("cues", {})
        self.th = cfg.get("thresholds", {})
        self.gz = gazetteer
        self.version = cfg.get("classifier", {}).get("version", "rules")
        self._value_change = re.compile(cfg.get("value_change_pattern", r"from\s+(?P<old>\d+)\s?%?\s+to\s+(?P<new>\d+)"), re.I)
        self._approval_cues = sorted(self.cues.get("approval_predicates", []), key=len, reverse=True)

    # ------------------------------------------------------------- agents
    def agents(self, text: str, actors: list[Actor], first_person: Actor | None = None) -> tuple[list[Actor], list[Actor]]:
        """(agents, bypassed): actors who approve/decide in `text`, and actors explicitly bypassed."""
        low = text.lower()
        preds = []
        for cue in self._approval_cues:
            for m in re.finditer(re.escape(cue), low):
                if not any(m.start() >= s and m.end() <= e for s, e in preds):
                    preds.append((m.start(), m.end()))
        agents: list[Actor] = []
        bypassed: list[Actor] = []
        for a in actors:
            clause_start = max(low.rfind(".", 0, a.start), low.rfind(";", 0, a.start), low.rfind("\n", 0, a.start)) + 1
            if _BYPASS_BEFORE.search(text[clause_start : a.start]):
                bypassed.append(a)
                continue
            for ps, pe in preds:
                gap_after = text[a.end : ps] if a.end <= ps else None
                gap_before = text[pe : a.start] if pe <= a.start else None
                if gap_after is not None and len(gap_after) <= 40 and "." not in gap_after and "|" not in gap_after:
                    agents.append(a)
                    break
                if gap_before is not None and len(gap_before) <= 25 and re.search(r"\b(by|from|to|with)\b", gap_before):
                    agents.append(a)
                    break
        if first_person is not None and preds:
            for m in _FIRST_PERSON.finditer(text):
                if any(abs(ps - m.end()) <= 45 or abs(m.start() - pe) <= 25 for ps, pe in preds):
                    agents.append(first_person)
                    break
        # chat turns: "Lior: Yep go for it, approved."
        for line in text.split("\n"):
            sp = self.gz.speaker_of(line)
            if sp and any(c in line.lower() for c in self._approval_cues + ["fine with it", "go ahead", "close it"]):
                if not any(x.person == sp.person for x in agents):
                    agents.append(sp)
        # structured records: "approval role: Diane Okafor (...)"
        for f in self.cfg.get("record_approver_fields", []):
            for m in re.finditer(rf"(?im)^{re.escape(f)}:\s*(.+)$", text):
                for a in self.gz.find(m.group(1)):
                    if not any(x.key() == a.key() for x in agents):
                        agents.append(a)
        return agents, bypassed

    def claim_agents(self, analysis: ClaimAnalysis) -> tuple[list[Actor], list[Actor]]:
        agents, bypassed = self.agents(analysis.normalized, analysis.actors)
        # most specific first: person > role > function > generic
        order = {"person": 0, "role": 1, "function": 2, "generic": 3}
        return sorted(agents, key=lambda a: order[a.kind]), bypassed

    # ------------------------------------------------------------ numbers
    def unit_numbers(self, text: str, ctx: EvidenceContext) -> list[NumericCondition]:
        nums = parse_numbers(text)
        if ctx.doc_kind == "record":
            for field, unit in (self.cfg.get("record_quantities") or {}).items():
                m = re.search(rf"(?im)^{re.escape(field)}:\s*(-?\d+(?:\.\d+)?)", text)
                if m:
                    nums.append(NumericCondition(value=float(m.group(1)), unit=unit, comparator="eq", raw=m.group(0)))
        return nums

    def numeric_signals(self, analysis: ClaimAnalysis, text: str, ctx: EvidenceContext) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if not analysis.numbers:
            return out
        unums = self.unit_numbers(text, ctx)
        changes = [(float(m.group("old")), float(m.group("new"))) for m in self._value_change.finditer(text)]
        instance = ctx.doc_kind in ("record", "thread")
        for c in analysis.numbers:
            same = [u for u in unums if u.unit == c.unit]
            if not same:
                continue
            for old, new in changes:
                if abs(old - c.value) < 1e-9 and abs(new - c.value) > 1e-9:
                    out["value_superseded"] = f"{c.raw}: value changed from {old:g} to {new:g}"
                elif abs(new - c.value) < 1e-9:
                    out["numeric_match"] = f"{c.raw} matches the updated value {new:g}"
            if "value_superseded" in out or "numeric_match" in out:
                continue
            cdir = _direction(c.comparator)
            equal = [u for u in same if abs(u.value - c.value) < 1e-9]
            rules = [u for u in same if u.comparator != "eq"]
            if equal:
                u = equal[0]
                udir = _direction(u.comparator)
                if cdir != "eq" and udir != "eq" and cdir != udir:
                    out["complement_condition"] = f"passage covers the opposite range ({u.raw}) of {c.raw}"
                else:
                    out["numeric_match"] = f"{c.raw} ~ {u.raw}"
            elif rules and not instance:
                out["numeric_mismatch"] = f"claim says {c.raw}; passage states {', '.join(u.raw for u in rules[:3])}"
            elif cdir == "up":
                vals = [u.value for u in same]
                if any(v >= c.value for v in vals):
                    out["condition_applies"] = f"instance value(s) {', '.join(f'{v:g}' for v in vals)} meet {c.raw}"
                else:
                    out["condition_not_met"] = f"instance value(s) {', '.join(f'{v:g}' for v in vals)} below {c.raw}"
            elif cdir == "down":
                vals = [u.value for u in same]
                if any(v > c.value for v in vals):
                    out["cap_violation"] = f"instance value(s) {', '.join(f'{v:g}' for v in vals)} exceed {c.raw}"
                else:
                    out["within_limit"] = f"instance value(s) {', '.join(f'{v:g}' for v in vals)} within {c.raw}"
            elif instance and any(abs(u.value - c.value) > 1e-9 for u in same) and c.comparator == "eq":
                out["numeric_mismatch_instance"] = f"claim says {c.raw}; passage has {', '.join(u.raw for u in same[:3])}"
        return out

    # ----------------------------------------------------------- polarity
    def polarity(self, analysis: ClaimAnalysis, text: str) -> tuple[str | None, float, str]:
        """('conflict'|'agree'|None, overlap, clause) for the clause most about the claim."""
        claim_terms = set(analysis.content_terms)
        if not claim_terms:
            return None, 0.0, ""
        syn = {stem(w) for k, alts in self.cfg.get("_synonyms", {}).items() if stem(k) in claim_terms
               for alt in alts for w in tokenize(alt)}
        predicate_terms = claim_terms | syn
        neg_cues = self.cues.get("negation", [])
        exempt = self.cues.get("negation_exempt", [])
        best: tuple[str | None, float, str] = (None, 0.0, "")
        for clause in _CLAUSE_SPLIT.split(text):
            toks = tokenize(clause)
            stems = [stem(t) for t in toks]
            overlap = len(claim_terms & set(stems)) / len(claim_terms)
            if overlap < 0.25:
                continue
            low = " " + clause.lower() + " "
            if any(e in low for e in exempt):
                low = low.replace(next(e for e in exempt if e in low), " ")
            neg = False
            for i, t in enumerate(toks):
                if t in ("not", "no", "never", "none", "nothing", "nobody", "neither", "nor") or t.endswith("n't"):
                    window = stems[i + 1 : i + 5]
                    if predicate_terms & set(window):
                        neg = True
                        break
            if not neg and any(c in low for c in ("not adopted", "rather than formal", "never revisited", "hasn't happened",
                                                   "still hasn't", "not yet", "no one was assigned", "does not specify")):
                neg = True
            pol = "conflict" if neg != analysis.negated else "agree"
            if _CONDITIONAL.search(clause) and pol == "conflict":
                overlap *= 0.5  # counterfactual / conditional phrasing is weak evidence
            if overlap > best[1]:
                best = (pol, overlap, clause.strip())
        return best

    # -------------------------------------------------------------- scope
    def scope(self, text: str, ctx: EvidenceContext, belief: list[str], practice: list[str], proposal: list[str]) -> tuple[str, str]:
        defaults = self.cfg.get("scope_defaults", {})
        role, dt = ctx.section_role, ctx.doc_type
        if role in ("proposal", "options") or ctx.status == "draft":
            return "proposal", "proposal"
        if role == "history":
            return "history", "history"
        if belief and dt in ("interview", "communication", "meeting_note"):
            return "belief", "belief"
        if ctx.doc_kind == "record":
            return "practice", "record"
        if dt in ("policy", "procedure", "org_reference"):
            low = text.lower()
            if any(p in low for p in ("in practice", "informally", "observed in practice", "by habit", "in reality")):
                return "practice", "practice_report"
            return "formal", "requirement" if role in ("requirement", "procedure_step", "responsibility", "header") else "context"
        if role in ("decision", "action") and dt == "meeting_note":
            return "decision", "decision"
        if dt == "interview":
            return "testimony", "testimony"
        if dt == "communication":
            return "practice", "practice_report"
        sd = defaults.get("section_role", {}).get(role) or defaults.get("doc_type", {}).get(dt) or "discussion"
        return sd, sd

    # ------------------------------------------------------------ classify
    def claim_names(self, analysis: ClaimAnalysis) -> list[str]:
        """Proper names in the claim that are not people, roles or functions (tools, systems, boards...)."""
        people = {w for name in self.gz.people for w in name.split()}
        roles = {w for r in self.gz.role_function for w in r.split()}
        return [m.group(1) for m in _NAME.finditer(analysis.normalized)
                if m.group(1) not in _NOT_NAMES and m.group(1) not in people and m.group(1) not in roles]

    def replacement(self, analysis: ClaimAnalysis, text: str) -> str | None:
        """The passage says a tool / system named in the claim was moved away from or retired."""
        for name in self.claim_names(analysis):
            if not re.search(rf"\b{re.escape(name)}\b", text, re.I):
                continue
            x = rf"(?i:{re.escape(name)})"
            pats = [rf"\b{x}\b[^.;]{{0,40}}?\b(?:(?:was|were|is|are|has been|have been|got|been|being)\s+)?{_RETIRED}",
                    rf"\b(?i:replac\w*)\s+(?:the\s+)?{x}\b[^.;]{{0,30}}?\bwith\s+([A-Z]\w+)",
                    rf"\b(?i:{_MOVE})\b[^.;]{{0,60}}?\b(?i:from)\s+(?:the\s+)?{x}\b[^.;]{{0,40}}?\b(?i:to|into)\s+([A-Z]\w+)",
                    rf"\b(?i:{_MOVE})\b[^.;]{{0,40}}?\b{x}\b[^.;]{{0,40}}?\b(?i:into|to)\s+([A-Z]\w+)"]
            for pat in pats:
                m = re.search(pat, text)
                if not m:
                    continue
                new = m.group(1) if m.lastindex else None
                if new and re.search(rf"\b{re.escape(new)}\b", analysis.normalized):
                    continue  # the claim already says it moved to the new tool
                return f"{name} was replaced or retired: \"{m.group(0)[:100]}\""
        return None

    def classify(self, analysis: ClaimAnalysis, ctx: EvidenceContext) -> Judgement:
        th = self.th
        text = ctx.span
        rel = ctx.relevance
        signals: dict[str, Any] = {"relevance": round(rel, 3)}
        # A multi-step claim is often covered by a whole section (e.g. procedure steps) rather than one sentence.
        rel_support = max(rel, 0.85 * ctx.section_relevance) if ctx.doc_kind != "record" else rel
        if rel_support > rel:
            signals["section_relevance"] = round(ctx.section_relevance, 3)
        belief = _cues(text, self.cues.get("belief", []))
        practice = _cues(text, self.cues.get("practice", []))
        proposal = _cues(text, self.cues.get("proposal", []))
        hedge = _cues(text, self.cues.get("hedge", []))
        deviation = _cues(text, self.cues.get("deviation", []))
        compliance = _cues(text, self.cues.get("compliance", []))
        univ_denial = _cues(text, self.cues.get("universal_denial", []))
        scope, modality = self.scope(text, ctx, belief, practice, proposal)
        directness = {"formal": 1.0, "decision": 0.85, "practice": 0.85, "testimony": 0.7, "discussion": 0.6,
                      "proposal": 0.5, "history": 0.6, "belief": 0.4}.get(scope, 0.6)
        if ctx.doc_kind == "record":
            directness = 0.9
        if hedge:
            directness *= th.get("hedge_factor", 0.6)
        for k, v in (("belief_cues", belief), ("practice_cues", practice), ("proposal_cues", proposal), ("hedge_cues", hedge),
                     ("deviation_cues", deviation), ("compliance_cues", compliance), ("universal_denial", univ_denial)):
            if v:
                signals[k] = v
        conditions = [m.group(0).strip() for m in _CONDITION_PHRASE.finditer(text)][:4]

        if rel < th.get("min_relevance", 0.12):
            return Judgement(label="NEUTRAL", scope=scope, strength=0.0, directness=directness, modality=modality,
                             conditions=conditions, rationale="Passage shares too little with the claim to bear on it.",
                             signals=signals, classifier=self.version)

        # --- signals
        num = self.numeric_signals(analysis, text, ctx)
        signals.update(num)
        c_agents, c_bypassed = self.claim_agents(analysis)
        speaker = None
        if ctx.doc_type == "interview" and ctx.doc_people:
            p = ctx.doc_people[0]
            info = self.gz.people.get(p, {})
            speaker = Actor(mention=p, kind="person", person=p, role=info.get("role"), function=info.get("function"))
        else:
            qm = _QUOTE_SPEAKER.search(text)
            if qm:
                speaker = self.gz.speaker_of(qm.group(1) + ": x")
        u_actors = self.gz.find(text)
        u_agents, u_bypassed = self.agents(text, u_actors, first_person=speaker)
        if c_agents and u_agents:
            primary = c_agents[0]
            rels = [self.gz.relation(primary, a) for a in u_agents]
            if any(r in ("match", "compatible") for r in rels):
                signals["actor_match"] = f"{primary.key()} ~ {', '.join(a.key() for a in u_agents)}"
            elif any(r == "conflict" for r in rels):
                signals["actor_conflict"] = f"claim: {primary.key()}; passage: {', '.join(a.key() for a in u_agents)}"
        if c_bypassed and u_bypassed and any(self.gz.relation(c_bypassed[0], b) in ("match", "compatible") for b in u_bypassed):
            signals["bypass_match"] = f"{c_bypassed[0].key()} is bypassed in both"
        pol, pol_overlap, pol_clause = (None, 0.0, "") if ctx.doc_kind == "record" else self.polarity(analysis, text)
        if pol:
            signals["polarity"] = {"result": pol, "overlap": round(pol_overlap, 2), "clause": pol_clause[:160]}
        repl = self.replacement(analysis, text) if rel >= 0.15 else None
        if repl:
            signals["replaced_by"] = repl
        if analysis.dates and any(d in text or (ctx.doc_date == d) for d in analysis.dates):
            signals["date_match"] = [d for d in analysis.dates if d in text or ctx.doc_date == d]

        claim_deviation = bool(_cues(analysis.normalized, self.cues.get("deviation", []) + ["themselves", "herself", "himself",
                                                                                           "sometimes", "informally"]))
        existential = analysis.quantifier == "existential"

        # --- support strength
        lex = 0.0
        if rel_support >= th.get("lexical_support", 0.5):
            lex = min(0.8, 0.45 + 0.7 * (rel_support - 0.5))
        elif rel_support >= 0.25:
            lex = 0.15 + 1.2 * (rel_support - 0.25)
        support = lex
        reasons_s: list[str] = []
        if lex >= 0.3:
            reasons_s.append(f"substantial wording overlap with the claim (relevance {rel_support:.2f})")
        if "numeric_match" in num and rel < 0.25:
            # the same number in an unrelated context ("30-day reminder" vs "30-day trial") is a coincidence
            signals["numeric_coincidence"] = num.pop("numeric_match")
            signals.pop("numeric_match", None)
        if "numeric_match" in num:
            support += 0.35
            reasons_s.append(num["numeric_match"])
        if "within_limit" in num:
            support += 0.2
            reasons_s.append(num["within_limit"])
        if "actor_match" in signals:
            support += 0.3
            reasons_s.append(f"same responsible actor ({signals['actor_match']})")
        if "bypass_match" in signals:
            support += 0.2
            reasons_s.append(signals["bypass_match"])
        if claim_deviation and deviation:
            support += 0.35
            signals["deviation_aligned"] = deviation
            reasons_s.append(f"describes the same informal practice ({', '.join(deviation[:3])})")
        if "condition_applies" in num and not deviation and compliance:
            support += 0.25
            reasons_s.append(f"{num['condition_applies']} and the record shows compliance ({', '.join(compliance[:2])})")
        elif "condition_applies" in num and not deviation and "actor_match" in signals:
            support += 0.15
        if "date_match" in signals:
            support += 0.2
            reasons_s.append(f"date {', '.join(signals['date_match'])} matches")
        if pol == "agree" and pol_overlap >= 0.5:
            support += 0.1

        # --- conflict strength
        conflict = 0.0
        reasons_c: list[str] = []

        def bump(value: float, why: str):
            nonlocal conflict
            conflict = max(conflict, value)
            reasons_c.append(why)

        if "value_superseded" in num:
            bump(0.8, num["value_superseded"])
        if repl:
            bump(0.75, repl)
        if "numeric_mismatch" in num:
            bump(0.75, num["numeric_mismatch"])
        if "numeric_mismatch_instance" in num and rel >= 0.3:
            bump(0.5, num["numeric_mismatch_instance"])
        if "actor_conflict" in signals and (not existential or univ_denial):
            bump(0.6 + (0.1 if ctx.doc_kind == "record" else 0.0), f"a different actor approves/decides ({signals['actor_conflict']})")
        if "cap_violation" in num and (not existential or univ_denial):
            bump(0.6, num["cap_violation"])
        if pol == "conflict":
            bump(0.45 + 0.4 * min(1.0, pol_overlap), f"the passage negates the claim (\"{pol_clause[:90]}\")")
        rule_like = analysis.claim_type == "normative" or (analysis.quantifier == "universal" and analysis.claim_type == "practice")
        if deviation and not claim_deviation and rule_like and rel >= 0.25:
            bump(0.55, f"describes a workaround / deviation ({', '.join(deviation[:3])})")
        # A compliant instance does not refute "sometimes ..."; only a universal denial ("no exceptions") does.
        if claim_deviation and (univ_denial or (compliance and not existential)) and not deviation and rel >= 0.25:
            bump(0.5 if univ_denial else 0.35, f"asserts compliance / no exceptions ({', '.join((univ_denial or compliance)[:2])})")
        if "condition_not_met" in num or "complement_condition" in num:
            support *= 0.3
            conflict *= 0.3
            signals["condition_scope"] = num.get("condition_not_met") or num.get("complement_condition")

        decisive = ("value_superseded" in num or "numeric_mismatch" in num or bool(repl)
                    or (pol == "conflict" and pol_overlap >= 0.5 and not _CONDITIONAL.search(pol_clause)))
        if decisive and conflict >= 0.6:
            support = min(support, 0.3)  # a contradicted key value / negated predicate outweighs topical overlap
            signals["decisive_conflict"] = True
        support, conflict = min(1.0, support), min(1.0, conflict)
        rel_factor = 0.5 + 0.5 * min(1.0, rel / 0.6)
        hf = th.get("hedge_factor", 0.6) if hedge else 1.0
        signals["support_score"] = round(support, 3)
        signals["conflict_score"] = round(conflict, 3)

        margin = th.get("ambiguity_margin", 0.12)
        if support >= 0.45 and conflict >= 0.45 and abs(support - conflict) < margin:
            label, strength = "AMBIGUOUS", max(support, conflict) * 0.5
            rationale = f"Mixed signals: supports ({'; '.join(reasons_s[:2])}) but also conflicts ({'; '.join(reasons_c[:2])})."
        elif conflict >= th.get("contradict_label", 0.35) and conflict >= support:
            label, strength = "CONTRADICTS", conflict * rel_factor * hf
            rationale = "Conflicts with the claim: " + "; ".join(reasons_c[:3]) + "."
        elif support >= th.get("support_label", 0.35):
            label, strength = "SUPPORTS", support * hf
            rationale = "Supports the claim: " + "; ".join(reasons_s[:3]) + "."
        elif hedge and rel >= 0.3:
            label, strength = "AMBIGUOUS", 0.2
            rationale = f"Relevant but hedged / uncertain ({', '.join(hedge[:2])})."
        else:
            label, strength = "NEUTRAL", 0.0
            rationale = "Related to the claim's topic but neither confirms nor disputes it."
        if hedge and label in ("SUPPORTS", "CONTRADICTS"):
            rationale += f" Statement is hedged ({', '.join(hedge[:2])})."
        if scope in ("proposal",) and label == "SUPPORTS":
            rationale += " Note: this is a proposal/draft, not an implemented rule."
        return Judgement(label=label, scope=scope, strength=round(min(1.0, strength), 4), directness=round(directness, 3),
                         modality=modality, conditions=conditions, rationale=rationale, signals=signals,
                         classifier=self.version)
