"""Answer rule questions ("who can approve X?", "can my manager approve a $700 expense?",
"is a 12% renewal discount allowed?") directly from the policy rulebook, then add what the
records show actually happens.

The rulebook (config/compliance.yaml) restates the formal requirements of POL-01/02 (new-business
discounts), POL-06 (renewal discounts) and POL-07 (expenses). Everything else goes to the claim
pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_TOPIC = [
    ("expense", re.compile(r"\b(expenses?|reimburs\w*|expense claims?)\b", re.I)),
    ("renewal", re.compile(r"\brenewals?\b", re.I)),
    ("discount", re.compile(r"\bdiscounts?\b", re.I)),
]
_INTENT = re.compile(r"\b(approv\w*|sign(s|ed)?[- ]?off|authori[sz]\w*|allowed|permitted|cap(ped)?|limit|threshold|"
                     r"max(imum)?|who (can|decides|approves|signs)|ok|okay|fine|need(s|ed)?|requir\w*|must|without)\b", re.I)
_WITHOUT_FINANCE = re.compile(r"\bwithout\b[^.?]{0,30}\b(finance|harriet|approval|sign[- ]?off|head of finance)", re.I)
_AMOUNT = re.compile(r"(?:\$\s?([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s?(?:\$|\b(?:dollars?|aud|usd)\b))", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent|per cent)", re.I)
_SELF = re.compile(r"\b(i|me|my|myself)\b", re.I)
# questions / statements about what actually happens, rather than what the policy allows
_PRACTICE = re.compile(r"\b(in practice|actually|really|sometimes|often|usually|regularly|ever|in reality|herself|himself|"
                       r"themselves|directly|bypass\w*|workarounds?|informally|without (?:waiting|going through)|"
                       r"has (?:been )?approved|have (?:been )?approved|approved (?:any|some))\b"
                       r"|\b(?:has|have|did|had)\b[^?.]{0,40}\bapprov\w*", re.I)
_LEVEL = re.compile(r"\b(above|over|exceed\w*|more than|higher than|beyond|under|below|within|up to)\b", re.I)


@dataclass
class RuleAnswer:
    outcome: str  # YES | NO | INFO
    headline: str
    answer: str
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    practice_records: list[str] = field(default_factory=list)


def topic_of(text: str) -> str | None:
    """'expense' / 'renewal' / 'discount' when the text is about an approval rule, a cap or a threshold."""
    level = bool(_LEVEL.search(text) and (_PCT.search(text) or _AMOUNT.search(text)))
    if not (_INTENT.search(text) or level):
        return None
    for name, rx in _TOPIC:
        if rx.search(text):
            return name
    # "who would approve my $600 claim?": a claim with a money amount is an expense claim
    if _AMOUNT.search(text) and re.search(r"(?i)\bclaims?\b", text):
        return "expense"
    return None


def _short(rid: str, f: Any) -> str:
    """'DEAL-014 (18%, recorded outside CRM (Slack), closed 5 Jul 2025)'"""
    from app.answers.compliance import fmt_date, to_date

    x = f.extra or {}
    bits = [f"{x['pct']:g}%" if x.get("pct") is not None else ""]
    if x.get("channel"):
        bits.append(x["channel"][:1].lower() + x["channel"][1:])
    if x.get("close"):
        bits.append(f"closed {fmt_date(to_date(x['close']))}")
    return f"{rid} ({', '.join(b for b in bits if b)})"


def _amount(text: str) -> float | None:
    m = _AMOUNT.search(text)
    if not m:
        return None
    return float((m.group(1) or m.group(2)).replace(",", ""))


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


def _deal_practice(text: str, rb: dict[str, Any], gz: Any, deals: list[tuple[str, Any]]) -> RuleAnswer:
    """What actually happens with discount approvals, from the CRM records checked against the rulebook."""
    hof = rb.get("finance_approver_role", "Head of Finance & People Ops")
    hof_who = _who((gz.role_people.get(hof) or [None])[0], hof)
    renewal = bool(_TOPIC[1][1].search(text))
    person, role, _ = _actor(text, gz)
    if renewal:
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
    v = sorted(rb["discount_new_business"]["versions"], key=lambda x: str(x["effective_from"]))[-1]
    needed = [(rid, f) for rid, f in deals if (f.extra or {}).get("needed_approval")
              and (f.rule or {}).get("policy") in {x["policy"] for x in rb["discount_new_business"]["versions"]}]
    by_hof = [(rid, f) for rid, f in needed if f.outcome == "COMPLIANT"]
    other = [(rid, f) for rid, f in needed if f.outcome == "VIOLATION" and (f.extra or {}).get("approver_role") != hof]
    none = [(rid, f) for rid, f in needed if f.outcome == "LIKELY_VIOLATION"]
    rule_line = (f"{v['policy']} requires {hof_who} to approve new-business discounts of {v['threshold_pct']}% or more, "
                 f"recorded in the CRM before Closed Won.")
    if person or role:
        mine = [(rid, f) for rid, f in other if (f.extra or {}).get("approver_role") == role
                or (person and (f.extra or {}).get("approver_name") == person)]
        who = _who(person, role)
        if role == hof:
            return RuleAnswer("YES", f"Yes: {who} approved {len(by_hof)} of {len(needed)}",
                              f"Yes. {who} approved {len(by_hof)} of the {len(needed)} closed deals that needed approval, "
                              f"as {v['policy']} requires.", [rule_line], [], [v["policy"]], [x for x, _ in by_hof[:3]])
        if mine:
            n = len(mine)
            return RuleAnswer("YES", f"Yes, in practice: {who} approved {n} discount{'s' if n > 1 else ''}",
                              f"Yes. In practice {who} approved {n} discount{'s' if n > 1 else ''} without the {hof}: "
                              + "; ".join(_short(rid, f) for rid, f in mine[:4]) + f". That breaks {v['policy']}.",
                              [rule_line] + [f.answer for _, f in mine[:3]], [], [v["policy"]], [x for x, _ in mine])
        return RuleAnswer("NO", f"No: the records show no discount approved by {who}",
                          f"No. None of the {len(needed)} closed deals that needed approval was approved by {who}.",
                          [rule_line], [], [v["policy"]])
    parts = [f"{hof_who} approved {len(by_hof)} of the {len(needed)} closed deals with discounts of "
             f"{v['threshold_pct']}% or more, as {v['policy']} requires"]
    if other:
        names = sorted({_who((f.extra or {}).get("approver_name"), (f.extra or {}).get("approver_role")) for _, f in other})
        parts.append(f"{len(other)} {'was' if len(other) == 1 else 'were'} approved by {', '.join(names)} instead "
                     f"({', '.join(x for x, _ in other)})")
    if none:
        parts.append(f"{len(none)} {'has' if len(none) == 1 else 'have'} no approval recorded ({', '.join(x for x, _ in none)})")
    answer = "In practice: " + "; ".join(parts) + "."
    return RuleAnswer("INFO", f"Mostly {hof_who}, but not always", answer,
                      [rule_line] + [f.answer for _, f in (other + none)[:4]], [], [v["policy"]],
                      [x for x, _ in (other + none)])


def answer_rule_question(text: str, topic: str, rb: dict[str, Any], gz: Any,
                         practice: dict[str, list[tuple[str, Any]]]) -> RuleAnswer:
    """practice: {'deals': [(record_id, Finding)], 'expenses': [...]}: every record checked against the rulebook."""
    all_deals = practice.get("deals", [])
    if topic in ("discount", "renewal") and _PRACTICE.search(text):
        return _deal_practice(text, rb, gz, all_deals)
    practice = {k: [(rid, f) for rid, f in v if f.outcome in ("VIOLATION", "LIKELY_VIOLATION")] for k, v in practice.items()}
    hof = rb.get("finance_approver_role", "Head of Finance & People Ops")
    hof_name = (gz.role_people.get(hof) or [None])[0]
    hof_who = _who(hof_name, hof)
    person, role, manager = _actor(text, gz)
    asked_who = _who(person, role) if (person or role) else ("your manager" if manager else None)

    if topic == "expense":
        r = rb["expense_approval"]
        thr = float(r["threshold_amount"])
        amt = _amount(text)
        viol = practice.get("expenses", [])
        reasons = [f"{r['policy']}: expense claims over ${thr:,.0f} need approval from the {hof}; claims of "
                   f"${thr:,.0f} or less can be approved by the employee's direct manager. Every claim needs an "
                   f"itemised receipt."]
        if viol:
            reasons.append("In practice this was bypassed: " + " ".join(f.answer for _, f in viol[:3]))
        if amt is not None and amt > thr:
            if (asked_who and role != hof) or _WITHOUT_FINANCE.search(text):
                return RuleAnswer("NO", f"No: a ${amt:,.0f} expense needs the {hof}",
                                  f"No. ${amt:,.0f} is over {r['policy']}'s ${thr:,.0f} limit, so it must be approved by "
                                  f"{hof_who}" + (f", not {asked_who}." if asked_who and role != hof else "."),
                                  reasons, [], [r["policy"]],
                                  [x for x, _ in viol])
            return RuleAnswer("YES" if role == hof else "INFO", f"A ${amt:,.0f} expense needs {hof} approval",
                              f"{'Yes. ' if role == hof else ''}${amt:,.0f} is over {r['policy']}'s ${thr:,.0f} limit, "
                              f"so it needs approval from {hof_who} before it is reimbursed.",
                              reasons, [], [r["policy"]], [x for x, _ in viol])
        if amt is not None:
            return RuleAnswer("INFO", f"Your direct manager can approve a ${amt:,.0f} expense",
                              f"A ${amt:,.0f} claim is within {r['policy']}'s ${thr:,.0f} limit, so the employee's direct "
                              f"manager can approve it; {hof} sign-off is not needed. It still needs an itemised receipt.",
                              reasons, [], [r["policy"]])
        return RuleAnswer("INFO", f"It depends on the amount: over ${thr:,.0f} needs the {hof}",
                          f"Up to ${thr:,.0f}, the employee's direct manager can approve an expense claim. Over "
                          f"${thr:,.0f}, it needs approval from {hof_who} ({r['policy']}).",
                          reasons, [], [r["policy"]], [x for x, _ in viol])

    deals = practice.get("deals", [])
    if topic == "renewal":
        r = rb["discount_renewal"]
        cap = float(r["cap_pct"])
        pct = _pct(text)
        viol = [(rid, f) for rid, f in deals if (f.rule or {}).get("policy") == r["policy"]]
        reasons = [f"{r['policy']}: renewal discounts are capped at {cap:g}% off the current contracted rate, with no "
                   f"exceptions; the Account Executive decides, consulting Customer Success."]
        if viol:
            reasons.append("In practice the cap was exceeded: " + " ".join(f.answer for _, f in viol[:3]))
        if pct is not None and pct > cap:
            return RuleAnswer("NO", f"No: renewal discounts are capped at {cap:g}%",
                              f"No. A {pct:g}% renewal discount is above {r['policy']}'s {cap:g}% cap, and the policy "
                              f"allows no exceptions, so no one can approve it" + (f", including {asked_who}" if asked_who else "")
                              + ".", reasons, [], [r["policy"]], [x for x, _ in viol])
        if pct is not None:
            return RuleAnswer("YES", f"Yes: {pct:g}% is within the {cap:g}% renewal cap",
                              f"Yes. {pct:g}% is within {r['policy']}'s {cap:g}% renewal cap; the Account Executive "
                              f"decides.", reasons, [], [r["policy"]])
        return RuleAnswer("INFO", f"Renewal discounts are capped at {cap:g}%",
                          f"Renewal discounts are capped at {cap:g}% ({r['policy']}), with no exceptions. The Account "
                          f"Executive decides within the cap, consulting Customer Success.", reasons, [], [r["policy"]],
                          [x for x, _ in viol])

    # new-business discounts
    v = sorted(rb["discount_new_business"]["versions"], key=lambda x: str(x["effective_from"]))[-1]
    thr = float(v["threshold_pct"])
    pct = _pct(text)
    big = pct is None or pct >= thr
    hof_viol = [(rid, f) for rid, f in deals if (f.rule or {}).get("policy") == v["policy"]]
    reasons = [f"{v['policy']}: new-business discounts of {thr:g}% or more need written approval from {hof_who}, "
               f"recorded in the CRM before the deal is marked Closed Won. Below {thr:g}%, the Account Executive can "
               f"approve. Renewals follow {rb['discount_renewal']['policy']} instead ({float(rb['discount_renewal']['cap_pct']):g}% cap)."]
    if pct is not None and pct >= thr and _WITHOUT_FINANCE.search(text):
        return RuleAnswer("NO", f"No: {pct:g}% needs {hof} approval",
                          f"No. A {pct:g}% new-business discount is at or above {v['policy']}'s {thr:g}% threshold, so it "
                          f"needs written approval from {hof_who}, recorded in the CRM before the deal is marked Closed Won.",
                          reasons, [], [v["policy"]], [x for x, _ in hof_viol])
    if pct is not None and pct < thr:
        return RuleAnswer("YES", f"Yes: below {thr:g}% no Finance sign-off is needed",
                          f"Yes. A {pct:g}% discount is below {v['policy']}'s {thr:g}% threshold, so the Account "
                          f"Executive can approve it without Finance sign-off.", reasons, [], [v["policy"]])
    if asked_who and role != hof:
        same = [(rid, f) for rid, f in hof_viol if role and (f.extra or {}).get("approver_role") == role]
        if same:
            reasons.append(f"In practice the {role} has approved some anyway, which breaks {v['policy']}: "
                           + " ".join(f.answer for _, f in same[:3]))
        elif hof_viol:
            reasons.append("In practice the rule was not always followed: "
                           + " ".join(f.answer for _, f in hof_viol[:3]))
        return RuleAnswer("NO", f"No: only the {hof} can approve that",
                          f"No. Under {v['policy']}, new-business discounts of {thr:g}% or more must be approved by "
                          f"{hof_who}, not {asked_who}.", reasons, [], [v["policy"]],
                          [x for x, _ in (same or hof_viol)])
    if hof_viol:
        reasons.append("In practice the rule was not always followed: "
                       + " ".join(f.answer for _, f in hof_viol[:3]))
    if role == hof:
        return RuleAnswer("YES", f"Yes: the {hof} approves discounts of {thr:g}% or more",
                          f"Yes. {hof_who} is the approver {v['policy']} requires for new-business discounts of "
                          f"{thr:g}% or more.", reasons, [], [v["policy"]], [x for x, _ in hof_viol])
    head = f"{hof_who} approves discounts of {thr:g}% or more" if big else "The Account Executive can approve it"
    return RuleAnswer("INFO", head,
                      f"{hof_who} approves new-business discounts of {thr:g}% or more ({v['policy']}); below {thr:g}%, "
                      f"the Account Executive can approve. Renewal discounts are capped at "
                      f"{float(rb['discount_renewal']['cap_pct']):g}% ({rb['discount_renewal']['policy']}).",
                      reasons, [], [v["policy"], rb["discount_renewal"]["policy"]], [x for x, _ in hof_viol])
