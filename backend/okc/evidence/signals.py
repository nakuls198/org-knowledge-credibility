"""Named signals the rules classifier reads from one evidence unit.

  numbers   numeric_match / numeric_mismatch / value_superseded / cap_violation / condition_not_met ...
  actors    who approves / decides in the passage (and who is explicitly bypassed)
  polarity  whether the passage negates the claim's predicate
  scope     formal rule, decision, proposal, practice, testimony, belief, history ...
  replaced  a tool / system the claim names was migrated away from or retired

All lexicons come from `config/reasoning.yaml` and `config/entities.yaml`.
"""

from __future__ import annotations

import re
from typing import Any

from okc.claims.actors import Actor, Gazetteer
from okc.claims.analysis import ClaimAnalysis, NumericCondition, parse_numbers
from okc.common.text import stem, tokenize
from okc.evidence.schemas import EvidenceContext

_CLAUSE_SPLIT = re.compile(r"(?<=[.;!?])\s+|\s+(?:but|however|whereas|although)\s+|\s+[—–-]{1,2}\s+")
_BYPASS_BEFORE = re.compile(r"(without(?: waiting(?: for| on)?)?|instead of(?: routing(?: \w+)?(?: to)?)?|rather than(?: routing(?: \w+)?(?: to)?)?|"
                            r"don'?t wait on|not wait(?:ing)? (?:on|for)|waiting (?:on|for))\s+(?:the\s+)?$", re.I)
_FIRST_PERSON = re.compile(r"\b(I|I'll|I'd|I've|I'm|my|me)\b")
CONDITIONAL = re.compile(r"\b(if|unless|would|surprised)\b", re.I)
_NEGATING_TOKENS = ("not", "no", "never", "none", "nothing", "nobody", "neither", "nor")
# "X was archived / replaced", "migrated from X to Y", "re-entered X cards into Y", "replaced X with Y"
_RETIRED = r"(?:archived|retired|deprecated|decommissioned|replaced|sunset|phased out|shut down|no longer used)"
_MOVE = r"(?:migrat\w*|mov(?:e|ed|es|ing)|switch\w*|transition\w*|re-?entered|port(?:ed|ing)|transferr?\w*)"
_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z0-9]{2,})\b")
_DIRECTION = {"gt": "up", "gte": "up", "lt": "down", "lte": "down", "cap": "down"}


def cues_in(text: str, cues: list[str]) -> list[str]:
    low = text.lower()
    return [c for c in cues if c in low]


def _direction(comparator: str) -> str:
    return _DIRECTION.get(comparator, "eq")


class SignalExtractor:
    def __init__(self, cfg: dict[str, Any], gazetteer: Gazetteer, synonyms: dict[str, list[str]] | None = None):
        self.cfg = cfg
        self.gz = gazetteer
        self.synonyms = synonyms or {}
        self.cues: dict[str, list[str]] = cfg.get("cues", {})
        self._value_change = re.compile(
            cfg.get("value_change_pattern", r"from\s+(?P<old>\d+)\s?%?\s+to\s+(?P<new>\d+)"), re.I)
        self._approval_cues = sorted(self.cues.get("approval_predicates", []), key=len, reverse=True)
        self._chat_approval_cues = self._approval_cues + self.cues.get("chat_approval_extras", [])

    # ------------------------------------------------------------- actors
    def agents(self, text: str, actors: list[Actor], first_person: Actor | None = None) -> tuple[list[Actor], list[Actor]]:
        """(agents, bypassed): actors who approve / decide in `text`, and actors explicitly bypassed."""
        low = text.lower()
        predicates: list[tuple[int, int]] = []
        for cue in self._approval_cues:
            for m in re.finditer(re.escape(cue), low):
                if not any(m.start() >= s and m.end() <= e for s, e in predicates):
                    predicates.append((m.start(), m.end()))
        agents: list[Actor] = []
        bypassed: list[Actor] = []
        for a in actors:
            clause_start = max(low.rfind(".", 0, a.start), low.rfind(";", 0, a.start), low.rfind("\n", 0, a.start)) + 1
            if _BYPASS_BEFORE.search(text[clause_start : a.start]):
                bypassed.append(a)
                continue
            for ps, pe in predicates:
                gap_after = text[a.end : ps] if a.end <= ps else None
                gap_before = text[pe : a.start] if pe <= a.start else None
                if gap_after is not None and len(gap_after) <= 40 and "." not in gap_after and "|" not in gap_after:
                    agents.append(a)
                    break
                if gap_before is not None and len(gap_before) <= 25 and re.search(r"\b(by|from|to|with)\b", gap_before):
                    agents.append(a)
                    break
        if first_person is not None and predicates:
            for m in _FIRST_PERSON.finditer(text):
                if any(abs(ps - m.end()) <= 45 or abs(m.start() - pe) <= 25 for ps, pe in predicates):
                    agents.append(first_person)
                    break
        for line in text.split("\n"):  # chat turns: "Lior: Yep go for it, approved."
            speaker = self.gz.speaker_of(line)
            if speaker and any(c in line.lower() for c in self._chat_approval_cues):
                if not any(x.person == speaker.person for x in agents):
                    agents.append(speaker)
        for field in self.cfg.get("record_approver_fields", []):  # structured records: "approval role: Diane Okafor (...)"
            for m in re.finditer(rf"(?im)^{re.escape(field)}:\s*(.+)$", text):
                for a in self.gz.find(m.group(1)):
                    if not any(x.key() == a.key() for x in agents):
                        agents.append(a)
        return agents, bypassed

    def claim_agents(self, analysis: ClaimAnalysis) -> tuple[list[Actor], list[Actor]]:
        agents, bypassed = self.agents(analysis.normalized, analysis.actors)
        order = {"person": 0, "role": 1, "function": 2, "generic": 3}  # most specific first
        return sorted(agents, key=lambda a: order[a.kind]), bypassed

    # ------------------------------------------------------------ numbers
    def unit_numbers(self, text: str, ctx: EvidenceContext) -> list[NumericCondition]:
        numbers = parse_numbers(text)
        if ctx.doc_kind == "record":
            for field, unit in (self.cfg.get("record_quantities") or {}).items():
                m = re.search(rf"(?im)^{re.escape(field)}:\s*(-?\d+(?:\.\d+)?)", text)
                if m:
                    numbers.append(NumericCondition(value=float(m.group(1)), unit=unit, comparator="eq", raw=m.group(0)))
        return numbers

    def numeric_signals(self, analysis: ClaimAnalysis, text: str, ctx: EvidenceContext) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if not analysis.numbers:
            return out
        unit_numbers = self.unit_numbers(text, ctx)
        changes = [(float(m.group("old")), float(m.group("new"))) for m in self._value_change.finditer(text)]
        instance = ctx.doc_kind in ("record", "thread")
        for c in analysis.numbers:
            same_unit = [u for u in unit_numbers if u.unit == c.unit]
            if not same_unit:
                continue
            for old, new in changes:
                if abs(old - c.value) < 1e-9 and abs(new - c.value) > 1e-9:
                    out["value_superseded"] = f"{c.raw}: value changed from {old:g} to {new:g}"
                elif abs(new - c.value) < 1e-9:
                    out["numeric_match"] = f"{c.raw} matches the updated value {new:g}"
            if "value_superseded" in out or "numeric_match" in out:
                continue
            claim_dir = _direction(c.comparator)
            equal = [u for u in same_unit if abs(u.value - c.value) < 1e-9]
            rules = [u for u in same_unit if u.comparator != "eq"]
            values = ", ".join(f"{u.value:g}" for u in same_unit)
            if equal:
                u = equal[0]
                unit_dir = _direction(u.comparator)
                if claim_dir != "eq" and unit_dir != "eq" and claim_dir != unit_dir:
                    out["complement_condition"] = f"passage covers the opposite range ({u.raw}) of {c.raw}"
                else:
                    out["numeric_match"] = f"{c.raw} ~ {u.raw}"
            elif rules and not instance:
                out["numeric_mismatch"] = f"claim says {c.raw}; passage states {', '.join(u.raw for u in rules[:3])}"
            elif claim_dir == "up":
                if any(u.value >= c.value for u in same_unit):
                    out["condition_applies"] = f"instance value(s) {values} meet {c.raw}"
                else:
                    out["condition_not_met"] = f"instance value(s) {values} below {c.raw}"
            elif claim_dir == "down":
                if any(u.value > c.value for u in same_unit):
                    out["cap_violation"] = f"instance value(s) {values} exceed {c.raw}"
                else:
                    out["within_limit"] = f"instance value(s) {values} within {c.raw}"
            elif instance and any(abs(u.value - c.value) > 1e-9 for u in same_unit) and c.comparator == "eq":
                out["numeric_mismatch_instance"] = f"claim says {c.raw}; passage has {', '.join(u.raw for u in same_unit[:3])}"
        return out

    # ----------------------------------------------------------- polarity
    def polarity(self, analysis: ClaimAnalysis, text: str) -> tuple[str | None, float, str]:
        """('conflict' | 'agree' | None, overlap, clause) for the clause most about the claim."""
        claim_terms = set(analysis.content_terms)
        if not claim_terms:
            return None, 0.0, ""
        synonym_terms = {stem(w) for k, alts in self.synonyms.items() if stem(k) in claim_terms
                         for alt in alts for w in tokenize(alt)}
        predicate_terms = claim_terms | synonym_terms
        exempt = self.cues.get("negation_exempt", [])
        negation_phrases = self.cues.get("negation_phrases", [])
        best: tuple[str | None, float, str] = (None, 0.0, "")
        for clause in _CLAUSE_SPLIT.split(text):
            tokens = tokenize(clause)
            stems = [stem(t) for t in tokens]
            overlap = len(claim_terms & set(stems)) / len(claim_terms)
            if overlap < 0.25:
                continue
            low = " " + clause.lower() + " "
            if any(e in low for e in exempt):
                low = low.replace(next(e for e in exempt if e in low), " ")
            negated = False
            for i, t in enumerate(tokens):
                if t in _NEGATING_TOKENS or t.endswith("n't"):
                    if predicate_terms & set(stems[i + 1 : i + 5]):
                        negated = True
                        break
            if not negated and any(c in low for c in negation_phrases):
                negated = True
            polarity = "conflict" if negated != analysis.negated else "agree"
            if CONDITIONAL.search(clause) and polarity == "conflict":
                overlap *= 0.5  # counterfactual / conditional phrasing is weak evidence
            if overlap > best[1]:
                best = (polarity, overlap, clause.strip())
        return best

    # -------------------------------------------------------------- scope
    def scope(self, text: str, ctx: EvidenceContext, belief: list[str], proposal: list[str]) -> tuple[str, str]:
        """(scope, modality): what kind of statement this is (rule, decision, practice, hearsay ...)."""
        defaults = self.cfg.get("scope_defaults", {})
        role, doc_type = ctx.section_role, ctx.doc_type
        if role in ("proposal", "options") or ctx.status == "draft":
            return "proposal", "proposal"
        if role == "history":
            return "history", "history"
        if belief and doc_type in ("interview", "communication", "meeting_note"):
            return "belief", "belief"
        if ctx.doc_kind == "record":
            return "practice", "record"
        if doc_type in ("policy", "procedure", "org_reference"):
            if cues_in(text, self.cues.get("practice_report_phrases", [])):
                return "practice", "practice_report"
            return "formal", "requirement" if role in ("requirement", "procedure_step", "responsibility", "header") else "context"
        if role in ("decision", "action") and doc_type == "meeting_note":
            return "decision", "decision"
        if doc_type == "interview":
            return "testimony", "testimony"
        if doc_type == "communication":
            return "practice", "practice_report"
        scope = defaults.get("section_role", {}).get(role) or defaults.get("doc_type", {}).get(doc_type) or "discussion"
        return scope, scope

    # ------------------------------------------------------- replacements
    def claim_names(self, analysis: ClaimAnalysis) -> list[str]:
        """Proper names in the claim that are not people, roles or functions (tools, systems, boards...)."""
        people = {w for name in self.gz.people for w in name.split()}
        roles = {w for r in self.gz.role_function for w in r.split()}
        ignore = self.gz.non_tool_names
        return [m.group(1) for m in _NAME.finditer(analysis.normalized)
                if m.group(1) not in ignore and m.group(1) not in people and m.group(1) not in roles]

    def replacement(self, analysis: ClaimAnalysis, text: str) -> str | None:
        """The passage says a tool / system named in the claim was moved away from or retired."""
        for name in self.claim_names(analysis):
            if not re.search(rf"\b{re.escape(name)}\b", text, re.I):
                continue
            x = rf"(?i:{re.escape(name)})"
            patterns = [rf"\b{x}\b[^.;]{{0,40}}?\b(?:(?:was|were|is|are|has been|have been|got|been|being)\s+)?{_RETIRED}",
                        rf"\b(?i:replac\w*)\s+(?:the\s+)?{x}\b[^.;]{{0,30}}?\bwith\s+([A-Z]\w+)",
                        rf"\b(?i:{_MOVE})\b[^.;]{{0,60}}?\b(?i:from)\s+(?:the\s+)?{x}\b[^.;]{{0,40}}?\b(?i:to|into)\s+([A-Z]\w+)",
                        rf"\b(?i:{_MOVE})\b[^.;]{{0,40}}?\b{x}\b[^.;]{{0,40}}?\b(?i:into|to)\s+([A-Z]\w+)"]
            for pattern in patterns:
                m = re.search(pattern, text)
                if not m:
                    continue
                new = m.group(1) if m.lastindex else None
                if new and re.search(rf"\b{re.escape(new)}\b", analysis.normalized):
                    continue  # the claim already says it moved to the new tool
                return f"{name} was replaced or retired: \"{m.group(0)[:100]}\""
        return None
