"""Answer rule questions ("who can approve X?", "can my manager approve a $700 expense?",
"is a 12% renewal discount allowed?") directly from the policy rulebook, then add what the records
show actually happens.

The rulebook (`config/compliance.yaml`) restates the formal requirements of the new-business discount
policies, the renewal-discount policy and the expense policy. Everything else goes to the claim
pipeline. Topic keywords come from `config/answers.yaml`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from okc.answers.compliance import fmt_date, to_date
from okc.settings import load_config

_INTENT = re.compile(r"\b(approv\w*|sign(s|ed)?[- ]?off|authori[sz]\w*|allowed|permitted|cap(ped)?|limit|threshold|"
                     r"max(imum)?|who (can|decides|approves|signs)|ok|okay|fine|need(s|ed)?|requir\w*|must|without)\b", re.I)
_AMOUNT = re.compile(r"(?:\$\s?([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s?(?:\$|\b(?:dollars?|aud|usd)\b))", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent|per cent)", re.I)
# questions / statements about what actually happens, rather than what the policy allows
_PRACTICE = re.compile(r"\b(in practice|actually|really|sometimes|often|usually|regularly|ever|in reality|herself|himself|"
                       r"themselves|directly|bypass\w*|workarounds?|informally|without (?:waiting|going through)|"
                       r"has (?:been )?approved|have (?:been )?approved|approved (?:any|some))\b"
                       r"|\b(?:has|have|did|had)\b[^?.]{0,40}\bapprov\w*", re.I)
_LEVEL = re.compile(r"\b(above|over|exceed\w*|more than|higher than|beyond|under|below|within|up to)\b", re.I)
_CLAIM_WORD = re.compile(r"\bclaims?\b", re.I)


@dataclass
class RuleAnswer:
    outcome: str  # YES | NO | INFO
    headline: str
    answer: str
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    practice_records: list[str] = field(default_factory=list)


@lru_cache
def _topics() -> list[tuple[str, re.Pattern[str]]]:
    return [(name, re.compile(pattern, re.I)) for name, pattern in load_config("answers").get("rule_topics", {}).items()]


def _topic_pattern(name: str) -> re.Pattern[str]:
    return dict(_topics())[name]


def topic_of(text: str) -> str | None:
    """'expense' / 'renewal' / 'discount' when the text is about an approval rule, a cap or a threshold."""
    level = bool(_LEVEL.search(text) and (_PCT.search(text) or _AMOUNT.search(text)))
    if not (_INTENT.search(text) or level):
        return None
    for name, pattern in _topics():
        if pattern.search(text):
            return name
    # "who would approve my $600 claim?": a claim with a money amount is an expense claim
    if _AMOUNT.search(text) and _CLAIM_WORD.search(text):
        return "expense"
    return None


# ---------------------------------------------------------------- small helpers

def _short(rid: str, f: Any) -> str:
    """'DEAL-014 (18%, recorded outside CRM (Slack), closed 5 Jul 2025)'"""
    x = f.extra or {}
    bits = [f"{x['pct']:g}%" if x.get("pct") is not None else ""]
    if x.get("channel"):
        bits.append(x["channel"][:1].lower() + x["channel"][1:])
    if x.get("close"):
        bits.append(f"closed {fmt_date(to_date(x['close']))}")
    return f"{rid} ({', '.join(b for b in bits if b)})"


def _amount(text: str) -> float | None:
    m = _AMOUNT.search(text)
    return float((m.group(1) or m.group(2)).replace(",", "")) if m else None


def _pct(text: str) -> float | None:
    m = _PCT.search(text)
    return float(m.group(1)) if m else None


def _actor(text: str, gz: Any) -> tuple[str | None, str | None, bool]:
    """(person, role, is_generic_manager) named in the question as the approver, if any."""
    for a in gz.find(text):
        if a.kind == "person":
            return a.person, a.role, False
        if a.kind == "role" and a.role:
            return None, a.role, False
        if a.kind == "generic":
            return None, None, True
    return None, None, False


def _who(person: str | None, role: str | None) -> str:
    if person and role:
        return f"{person} ({role})"
    return person or (f"the {role}" if role else "they")


def _without_approver(text: str, finance_first_name: str | None) -> bool:
    """'... without Finance', 'without Harriet', 'without approval / sign-off'."""
    words = [*load_config("answers").get("without_approver_words", []), "approval", r"sign[- ]?off"]
    if finance_first_name:
        words.append(re.escape(finance_first_name.lower()))
    return bool(re.search(rf"\bwithout\b[^.?]{{0,30}}\b({'|'.join(words)})", text, re.I))


@dataclass
class _Context:
    """What every topic answer needs: the rulebook, the roster-derived people, and the question's actor."""

    text: str
    rb: dict[str, Any]
    finance_role: str
    finance_who: str
    finance_first_name: str | None
    person: str | None
    role: str | None
    asked_who: str | None


def _context(text: str, rb: dict[str, Any], gz: Any) -> _Context:
    finance_role = rb["finance_approver_role"]
    finance_name = (gz.role_people.get(finance_role) or [None])[0]
    person, role, manager = _actor(text, gz)
    asked_who = _who(person, role) if (person or role) else ("your manager" if manager else None)
    return _Context(text, rb, finance_role, _who(finance_name, finance_role),
                    finance_name.split()[0] if finance_name else None, person, role, asked_who)


def _newest_version(rb: dict[str, Any]) -> dict[str, Any]:
    return sorted(rb["discount_new_business"]["versions"], key=lambda x: str(x["effective_from"]))[-1]


# ---------------------------------------------------------------- practice: what the records show

def _deal_practice(text: str, rb: dict[str, Any], gz: Any, deals: list[tuple[str, Any]]) -> RuleAnswer:
    """What actually happens with discount approvals, from the CRM records checked against the rulebook."""
    ctx = _context(text, rb, gz)
    if _topic_pattern("renewal").search(text):
        return _renewal_practice(rb, deals)
    version = _newest_version(rb)
    policies = {x["policy"] for x in rb["discount_new_business"]["versions"]}
    needed = [(rid, f) for rid, f in deals if (f.extra or {}).get("needed_approval") and (f.rule or {}).get("policy") in policies]
    by_finance = [(rid, f) for rid, f in needed if f.outcome == "COMPLIANT"]
    other = [(rid, f) for rid, f in needed if f.outcome == "VIOLATION" and (f.extra or {}).get("approver_role") != ctx.finance_role]
    unrecorded = [(rid, f) for rid, f in needed if f.outcome == "LIKELY_VIOLATION"]
    rule_line = (f"{version['policy']} requires {ctx.finance_who} to approve new-business discounts of "
                 f"{version['threshold_pct']}% or more, recorded in the CRM before {rb['stages']['won']}.")

    if ctx.person or ctx.role:
        who = _who(ctx.person, ctx.role)
        if ctx.role == ctx.finance_role:
            return RuleAnswer("YES", f"Yes: {who} approved {len(by_finance)} of {len(needed)}",
                              f"Yes. {who} approved {len(by_finance)} of the {len(needed)} closed deals that needed approval, "
                              f"as {version['policy']} requires.", [rule_line], [], [version["policy"]],
                              [x for x, _ in by_finance[:3]])
        mine = [(rid, f) for rid, f in other if (f.extra or {}).get("approver_role") == ctx.role
                or (ctx.person and (f.extra or {}).get("approver_name") == ctx.person)]
        if mine:
            n = len(mine)
            return RuleAnswer("YES", f"Yes, in practice: {who} approved {n} discount{'s' if n > 1 else ''}",
                              f"Yes. In practice {who} approved {n} discount{'s' if n > 1 else ''} without the {ctx.finance_role}: "
                              + "; ".join(_short(rid, f) for rid, f in mine[:4]) + f". That breaks {version['policy']}.",
                              [rule_line] + [f.answer for _, f in mine[:3]], [], [version["policy"]], [x for x, _ in mine])
        return RuleAnswer("NO", f"No: the records show no discount approved by {who}",
                          f"No. None of the {len(needed)} closed deals that needed approval was approved by {who}.",
                          [rule_line], [], [version["policy"]])

    parts = [f"{ctx.finance_who} approved {len(by_finance)} of the {len(needed)} closed deals with discounts of "
             f"{version['threshold_pct']}% or more, as {version['policy']} requires"]
    if other:
        names = sorted({_who((f.extra or {}).get("approver_name"), (f.extra or {}).get("approver_role")) for _, f in other})
        parts.append(f"{len(other)} {'was' if len(other) == 1 else 'were'} approved by {', '.join(names)} instead "
                     f"({', '.join(x for x, _ in other)})")
    if unrecorded:
        parts.append(f"{len(unrecorded)} {'has' if len(unrecorded) == 1 else 'have'} no approval recorded "
                     f"({', '.join(x for x, _ in unrecorded)})")
    return RuleAnswer("INFO", f"Mostly {ctx.finance_who}, but not always", "In practice: " + "; ".join(parts) + ".",
                      [rule_line] + [f.answer for _, f in (other + unrecorded)[:4]], [], [version["policy"]],
                      [x for x, _ in (other + unrecorded)])


def _renewal_practice(rb: dict[str, Any], deals: list[tuple[str, Any]]) -> RuleAnswer:
    r = rb["discount_renewal"]
    over = [(rid, f) for rid, f in deals if (f.rule or {}).get("policy") == r["policy"] and f.outcome == "VIOLATION"]
    if over:
        return RuleAnswer("YES", f"Yes: {len(over)} renewal{'s' if len(over) > 1 else ''} went over the {r['cap_pct']}% cap",
                          f"Yes. In practice the {r['cap_pct']}% renewal cap was exceeded: "
                          + "; ".join(_short(rid, f) for rid, f in over[:4])
                          + f". That breaks {r['policy']}, which allows no exceptions.",
                          [" ".join(r["rule_text"].split())] + [f.answer for _, f in over[:3]], [], [r["policy"]],
                          [x for x, _ in over])
    return RuleAnswer("NO", "No renewal went over the cap", f"No closed renewal in the CRM exceeds {r['policy']}'s "
                      f"{r['cap_pct']}% cap.", [], [], [r["policy"]])


# ---------------------------------------------------------------- rules: what the policy allows

def _expense_answer(ctx: _Context, violations: list[tuple[str, Any]]) -> RuleAnswer:
    r = ctx.rb["expense_approval"]
    threshold, amount, finance = float(r["threshold_amount"]), _amount(ctx.text), ctx.finance_role
    reasons = [f"{r['policy']}: expense claims over ${threshold:,.0f} need approval from the {finance}; claims of "
               f"${threshold:,.0f} or less can be approved by the employee's direct manager. Every claim needs an "
               f"itemised receipt."]
    if violations:
        reasons.append("In practice this was bypassed: " + " ".join(f.answer for _, f in violations[:3]))
    records = [x for x, _ in violations]
    if amount is not None and amount > threshold:
        if (ctx.asked_who and ctx.role != finance) or _without_approver(ctx.text, ctx.finance_first_name):
            return RuleAnswer("NO", f"No: a ${amount:,.0f} expense needs the {finance}",
                              f"No. ${amount:,.0f} is over {r['policy']}'s ${threshold:,.0f} limit, so it must be approved by "
                              f"{ctx.finance_who}" + (f", not {ctx.asked_who}." if ctx.asked_who and ctx.role != finance else "."),
                              reasons, [], [r["policy"]], records)
        return RuleAnswer("YES" if ctx.role == finance else "INFO", f"A ${amount:,.0f} expense needs {finance} approval",
                          f"{'Yes. ' if ctx.role == finance else ''}${amount:,.0f} is over {r['policy']}'s ${threshold:,.0f} limit, "
                          f"so it needs approval from {ctx.finance_who} before it is reimbursed.",
                          reasons, [], [r["policy"]], records)
    if amount is not None:
        return RuleAnswer("INFO", f"Your direct manager can approve a ${amount:,.0f} expense",
                          f"A ${amount:,.0f} claim is within {r['policy']}'s ${threshold:,.0f} limit, so the employee's direct "
                          f"manager can approve it; {finance} sign-off is not needed. It still needs an itemised receipt.",
                          reasons, [], [r["policy"]])
    return RuleAnswer("INFO", f"It depends on the amount: over ${threshold:,.0f} needs the {finance}",
                      f"Up to ${threshold:,.0f}, the employee's direct manager can approve an expense claim. Over "
                      f"${threshold:,.0f}, it needs approval from {ctx.finance_who} ({r['policy']}).",
                      reasons, [], [r["policy"]], records)


def _renewal_answer(ctx: _Context, deals: list[tuple[str, Any]]) -> RuleAnswer:
    r = ctx.rb["discount_renewal"]
    cap, pct = float(r["cap_pct"]), _pct(ctx.text)
    over = [(rid, f) for rid, f in deals if (f.rule or {}).get("policy") == r["policy"]]
    decider = f"the {r['decider_role']} decides, consulting {r['consulted_function']}"
    reasons = [f"{r['policy']}: renewal discounts are capped at {cap:g}% off the current contracted rate, with no "
               f"exceptions; {decider}."]
    if over:
        reasons.append("In practice the cap was exceeded: " + " ".join(f.answer for _, f in over[:3]))
    records = [x for x, _ in over]
    if pct is not None and pct > cap:
        return RuleAnswer("NO", f"No: renewal discounts are capped at {cap:g}%",
                          f"No. A {pct:g}% renewal discount is above {r['policy']}'s {cap:g}% cap, and the policy "
                          f"allows no exceptions, so no one can approve it" + (f", including {ctx.asked_who}" if ctx.asked_who else "")
                          + ".", reasons, [], [r["policy"]], records)
    if pct is not None:
        return RuleAnswer("YES", f"Yes: {pct:g}% is within the {cap:g}% renewal cap",
                          f"Yes. {pct:g}% is within {r['policy']}'s {cap:g}% renewal cap; the {r['decider_role']} decides.",
                          reasons, [], [r["policy"]])
    return RuleAnswer("INFO", f"Renewal discounts are capped at {cap:g}%",
                      f"Renewal discounts are capped at {cap:g}% ({r['policy']}), with no exceptions. The {r['decider_role']} "
                      f"decides within the cap, consulting {r['consulted_function']}.", reasons, [], [r["policy"]], records)


def _new_business_answer(ctx: _Context, deals: list[tuple[str, Any]]) -> RuleAnswer:
    rb, finance = ctx.rb, ctx.finance_role
    version = _newest_version(rb)
    section, renewal = rb["discount_new_business"], rb["discount_renewal"]
    threshold, pct = float(version["threshold_pct"]), _pct(ctx.text)
    below = section["below_threshold_approver_role"]
    breaches = [(rid, f) for rid, f in deals if (f.rule or {}).get("policy") == version["policy"]]
    breach_ids = [x for x, _ in breaches]
    reasons = [f"{version['policy']}: new-business discounts of {threshold:g}% or more need written approval from "
               f"{ctx.finance_who}, recorded in the CRM before the deal is marked {rb['stages']['won']}. Below "
               f"{threshold:g}%, the {below} can approve. Renewals follow {renewal['policy']} instead "
               f"({float(renewal['cap_pct']):g}% cap)."]
    if pct is not None and pct >= threshold and _without_approver(ctx.text, ctx.finance_first_name):
        return RuleAnswer("NO", f"No: {pct:g}% needs {finance} approval",
                          f"No. A {pct:g}% new-business discount is at or above {version['policy']}'s {threshold:g}% threshold, so it "
                          f"needs written approval from {ctx.finance_who}, recorded in the CRM before the deal is marked "
                          f"{rb['stages']['won']}.", reasons, [], [version["policy"]], breach_ids)
    if pct is not None and pct < threshold:
        return RuleAnswer("YES", f"Yes: below {threshold:g}% no Finance sign-off is needed",
                          f"Yes. A {pct:g}% discount is below {version['policy']}'s {threshold:g}% threshold, so the {below} "
                          f"can approve it without Finance sign-off.", reasons, [], [version["policy"]])
    if ctx.asked_who and ctx.role != finance:
        same = [(rid, f) for rid, f in breaches if ctx.role and (f.extra or {}).get("approver_role") == ctx.role]
        if same:
            reasons.append(f"In practice the {ctx.role} has approved some anyway, which breaks {version['policy']}: "
                           + " ".join(f.answer for _, f in same[:3]))
        elif breaches:
            reasons.append("In practice the rule was not always followed: " + " ".join(f.answer for _, f in breaches[:3]))
        return RuleAnswer("NO", f"No: only the {finance} can approve that",
                          f"No. Under {version['policy']}, new-business discounts of {threshold:g}% or more must be approved by "
                          f"{ctx.finance_who}, not {ctx.asked_who}.", reasons, [], [version["policy"]],
                          [x for x, _ in (same or breaches)])
    if breaches:
        reasons.append("In practice the rule was not always followed: " + " ".join(f.answer for _, f in breaches[:3]))
    if ctx.role == finance:
        return RuleAnswer("YES", f"Yes: the {finance} approves discounts of {threshold:g}% or more",
                          f"Yes. {ctx.finance_who} is the approver {version['policy']} requires for new-business discounts of "
                          f"{threshold:g}% or more.", reasons, [], [version["policy"]], breach_ids)
    big = pct is None or pct >= threshold
    head = f"{ctx.finance_who} approves discounts of {threshold:g}% or more" if big else f"The {below} can approve it"
    return RuleAnswer("INFO", head,
                      f"{ctx.finance_who} approves new-business discounts of {threshold:g}% or more ({version['policy']}); below "
                      f"{threshold:g}%, the {below} can approve. Renewal discounts are capped at "
                      f"{float(renewal['cap_pct']):g}% ({renewal['policy']}).",
                      reasons, [], [version["policy"], renewal["policy"]], breach_ids)


def answer_rule_question(text: str, topic: str, rb: dict[str, Any], gz: Any,
                         practice: dict[str, list[tuple[str, Any]]]) -> RuleAnswer:
    """practice: {'deals': [(record_id, Finding)], 'expenses': [...]}: every record checked against the rulebook."""
    if topic in ("discount", "renewal") and _PRACTICE.search(text):
        return _deal_practice(text, rb, gz, practice.get("deals", []))
    breaches = {k: [(rid, f) for rid, f in v if f.outcome in ("VIOLATION", "LIKELY_VIOLATION")] for k, v in practice.items()}
    ctx = _context(text, rb, gz)
    if topic == "expense":
        return _expense_answer(ctx, breaches.get("expenses", []))
    if topic == "renewal":
        return _renewal_answer(ctx, breaches.get("deals", []))
    return _new_business_answer(ctx, breaches.get("deals", []))
