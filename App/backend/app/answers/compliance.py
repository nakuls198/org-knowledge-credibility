"""Record compliance checks: one record against the policy rule that governs it.

Pure functions over a record's fields (no database access) so each rule is easy to test and audit.
The rules come from config/compliance.yaml, which restates the formal requirements of the policies
(with the section each comes from). A check returns a Finding: an outcome, a one-line headline, a
plain answer, the reasons behind it and any caveats.

Outcomes
  VIOLATION         the record shows the rule was broken
  LIKELY_VIOLATION  the rule required something the records do not show (e.g. no approval recorded)
  COMPLIANT         the record meets the rule
  NOT_APPLICABLE    the rule does not apply to this record (e.g. the deal never closed)
  CANNOT_TELL       a field the rule depends on is missing
  NO_RULE           no rule in the rulebook covers this kind of record
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

OUTCOME_HEADLINE = {
    "VIOLATION": "Policy violation",
    "LIKELY_VIOLATION": "Likely policy violation (not confirmed)",
    "COMPLIANT": "Follows policy",
    "NOT_APPLICABLE": "The rule does not apply",
    "CANNOT_TELL": "Can't tell from the records",
    "NO_RULE": "No policy check for this record",
}

# lookup(name) -> (full name, role): resolves first names ("Lior") via the staff roster; role is None if unknown.
Lookup = Callable[[str], "tuple[str, str | None]"]


@dataclass
class Finding:
    outcome: str
    answer: str
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    rule: dict[str, Any] | None = None  # {policy, section, rule_text}
    facts: list[tuple[str, str]] = field(default_factory=list)  # key record fields, in display order
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. approver_name / approver_role for practice summaries

    @property
    def headline(self) -> str:
        policy = (self.rule or {}).get("policy")
        base = OUTCOME_HEADLINE[self.outcome]
        if policy and self.outcome in ("VIOLATION", "LIKELY_VIOLATION", "COMPLIANT"):
            return f"{base} ({policy})"
        return base


# ---------------------------------------------------------------- helpers

def fix_text(s: str | None) -> str:
    """Undo UTF-8 read as cp1252 (e.g. 'â€”' -> '—') that appears in some exported CSV cells."""
    s = (s or "").strip()
    if "â" in s or "Ã" in s:
        try:
            return s.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s.replace("â€”", "—").replace("â€™", "'").replace("�", "—")
    return s.replace("�", "—")


def to_date(s: Any) -> date | None:
    if isinstance(s, date):
        return s
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(s or ""))
    return date.fromisoformat(m.group(1)) if m else None


def fmt_date(d: date | None) -> str:
    return f"{d.day} {d.strftime('%b %Y')}" if d else "an unknown date"


def fmt_pct(p: float) -> str:
    return f"{p:g}%"


def a_pct(p: float) -> str:
    """'a 15%' / 'an 18%' / 'an 8%' / 'an 11%'."""
    s = f"{p:g}"
    an = s.startswith("8") or s in ("11", "18") or (s.startswith("18") and len(s.split(".")[0]) in (2, 5))
    return f"{'an' if an else 'a'} {s}%"


def _version_for(versions: list[dict[str, Any]], on: date | None) -> dict[str, Any]:
    """The rule version in force on a date (the newest version when the date is unknown)."""
    ordered = sorted(versions, key=lambda v: to_date(v["effective_from"]) or date.min, reverse=True)
    if on is None:
        return ordered[0]
    for v in ordered:
        start, end = to_date(v.get("effective_from")), to_date(v.get("effective_to"))
        if (start is None or on >= start) and (end is None or on <= end):
            return v
    return ordered[-1]


def _rule(v: dict[str, Any]) -> dict[str, Any]:
    return {"policy": v["policy"], "section": v["section"], "rule_text": " ".join(str(v["rule_text"]).split())}


def _person_role(text: str, lookup: Lookup) -> tuple[str | None, str | None]:
    """'Diane Okafor (Slack, 2025-07-02)' -> ('Diane Okafor', 'Head of Sales')."""
    name = re.split(r"[(,;]", text or "")[0].strip()
    if not name:
        return None, None
    return lookup(name)


def _where(arec: str) -> str:
    """'Recorded outside CRM (Slack)' -> ', recorded outside CRM (Slack)'."""
    return f", {arec[:1].lower()}{arec[1:]}" if arec else ""


def _who(name: str | None, role: str | None) -> str:
    return f"{name} ({role})" if name and role else (name or role or "someone")


# ---------------------------------------------------------------- CRM deals: POL-01 / POL-02 / POL-06

def check_deal(m: dict[str, Any], rb: dict[str, Any], lookup: Lookup,
               approvals: list[dict[str, Any]] | None = None) -> Finding:
    """Check a CRM deal against the discount approval rules.

    m: the deal's fields (crm_deals.csv columns); approvals: its 'Approved discount' audit events.
    """
    approvals = approvals or []
    did = m.get("deal_id") or "This deal"
    cust = fix_text(m.get("customer_name"))
    stage = fix_text(m.get("stage"))
    close = to_date(m.get("close_date"))
    notes = fix_text(m.get("notes"))
    arec = fix_text(m.get("approval_record"))
    arole = fix_text(m.get("approval_role"))
    reason = fix_text(m.get("reason_lost_won"))
    raw_pct = str(m.get("discount_pct") or "").strip()

    facts = [("Customer", cust), ("Stage", stage), ("Discount", f"{raw_pct}%" if raw_pct else "not recorded"),
             ("Closed", fmt_date(close) if close else "not recorded"), ("Approval record", arec or "(empty)"),
             ("Approved by", arole or "(empty)")]
    if reason:
        facts.append(("Outcome reason", reason))
    if notes:
        facts.append(("Notes", notes))

    caveats: list[str] = []
    pct: float | None
    try:
        pct = float(raw_pct)
    except ValueError:
        pct = None
    if pct is not None and pct > 100:
        fixed = re.search(r"should be (\d+(?:\.\d+)?)", notes, re.I)
        if fixed:
            caveats.append(f"The record says {fmt_pct(pct)}, which is impossible; its own notes say it should be "
                           f"{fixed.group(1)}%, so {fixed.group(1)}% is used.")
            pct = float(fixed.group(1))
        else:
            caveats.append(f"The recorded discount ({fmt_pct(pct)}) is impossible, so the record looks wrong.")

    markers = [w.lower() for w in rb["discount_renewal"].get("renewal_markers", ["renewal"])]
    renewal = any(w in f"{reason} {notes} {arole}".lower() for w in markers)
    kind = "renewal" if renewal else "new-business deal"
    who_what = f"{did}{f' ({cust})' if cust else ''}"

    if stage.lower() == "closed lost":
        r = _rule(_version_for(rb["discount_new_business"]["versions"], close)) if not renewal else _rule(
            {**rb["discount_renewal"]})
        return Finding("NOT_APPLICABLE", f"{who_what} was lost, so it never reached Closed Won and the discount rule "
                       f"never came into force.", [f"Stage: Closed Lost ({fmt_date(close)})."], caveats, r, facts)
    if stage.lower() != "closed won":
        rule = rb["discount_renewal"] if renewal else _version_for(rb["discount_new_business"]["versions"], None)
        if pct is None:
            need = "The discount isn't recorded yet."
        elif renewal:
            need = (f"Its {fmt_pct(pct)} discount is above POL-06's {rule['cap_pct']}% renewal cap, so it cannot close "
                    f"at that discount." if pct > rule["cap_pct"] else f"Its {fmt_pct(pct)} discount is within the cap.")
        else:
            need = (f"At {fmt_pct(pct)} it will need {rule['approver_role']} approval, recorded in the CRM, before "
                    f"it can be marked Closed Won." if pct >= rule["threshold_pct"]
                    else f"At {fmt_pct(pct)} the Account Executive can approve it.")
        return Finding("NOT_APPLICABLE", f"Not yet: {who_what} is still open ({stage}). {need}",
                       [f"The rule applies when a deal is marked Closed Won; {did} is at {stage}."], caveats,
                       _rule(rule), facts)
    if pct is None:
        return Finding("CANNOT_TELL", f"{who_what} closed won, but its discount isn't recorded, so it can't be checked "
                       f"against the discount rules.", ["The discount_pct field is empty."],
                       caveats + ([notes] if notes else []), _rule(_version_for(rb["discount_new_business"]["versions"], close)),
                       facts)

    hof_role = rb.get("finance_approver_role", "Head of Finance & People Ops")
    name, role = _person_role(arole, lookup)
    hof_events = [a for a in approvals if (a.get("actor_role") == hof_role or lookup(a.get("actor_name", ""))[1] == hof_role)]
    first_ok = min((to_date(a.get("timestamp")) for a in hof_events if to_date(a.get("timestamp"))), default=None)

    # ---- renewals: POL-06 cap, no exceptions
    if renewal:
        rr = rb["discount_renewal"]
        rule = _rule(rr)
        cap = float(rr["cap_pct"])
        if pct <= cap:
            return Finding("COMPLIANT", f"{who_what} is a renewal with {a_pct(pct)} discount, within POL-06's "
                           f"{fmt_pct(cap)} renewal cap.", [f"Renewal discount {fmt_pct(pct)} ≤ cap {fmt_pct(cap)}."],
                           caveats, rule, facts)
        reasons = [f"It is a renewal (\"{reason}\"), so POL-06 applies, not the new-business rule.",
                   f"The discount was {fmt_pct(pct)}; POL-06 caps renewal discounts at {fmt_pct(cap)} and allows no exceptions."]
        if name:
            reasons.append(f"It was approved by {_who(name, role)}{_where(arec)}, but POL-06 "
                           f"has no exception process, so an approval does not make it compliant.")
        return Finding("VIOLATION", f"{who_what} broke the renewal discount cap: it closed on {fmt_date(close)} with "
                       f"{a_pct(pct)} discount, above POL-06's {fmt_pct(cap)} cap.", reasons, caveats, rule, facts,
                       {"pct": pct, "close": str(close), "approver_name": name, "approver_role": role, "channel": arec})

    # ---- new business: POL-01 (or POL-02 before 1 Mar 2025)
    v = _version_for(rb["discount_new_business"]["versions"], close)
    rule = _rule(v)
    thr = float(v["threshold_pct"])
    if pct < thr:
        return Finding("COMPLIANT", f"{who_what} closed with {a_pct(pct)} discount, below {v['policy']}'s "
                       f"{fmt_pct(thr)} threshold, so the Account Executive could approve it without Finance sign-off.",
                       [f"Discount {fmt_pct(pct)} < threshold {fmt_pct(thr)} ({v['policy']}, in force on {fmt_date(close)})."],
                       caveats, rule, facts, {"needed_approval": False, "pct": pct})

    base = (f"{v['policy']} (in force on the close date, {fmt_date(close)}) requires {v['approver_role']} approval, "
            f"recorded in the CRM, for discounts of {fmt_pct(thr)} or more before Closed Won.")
    in_crm = "recorded in crm" in arec.lower()
    approver_is_hof = role == hof_role
    if approver_is_hof or hof_events:
        when = first_ok
        reasons = [base, f"The discount was {fmt_pct(pct)}, so approval was required."]
        if when and close and when > close:
            reasons.append(f"The audit log shows {hof_role} approval on {fmt_date(when)}, after the deal closed on "
                           f"{fmt_date(close)}.")
            return Finding("VIOLATION", f"{who_what} was approved by the {hof_role} only after it closed.",
                           reasons, caveats, rule, facts,
                           {"needed_approval": True, "pct": pct, "approver_role": hof_role, "close": str(close)})
        if not in_crm and v.get("must_be_recorded_in_crm"):
            reasons.append(f"The approval came from the {hof_role} but is recorded as \"{arec}\", not in the CRM.")
            return Finding("VIOLATION", f"{who_what} had the right approver, but the approval is not recorded in the "
                           f"CRM as {v['policy']} requires.", reasons, caveats, rule, facts,
                           {"needed_approval": True, "pct": pct, "approver_role": hof_role, "close": str(close)})
        approver = _who(name, role) if approver_is_hof else _who(hof_events[0].get("actor_name"), hof_role)
        timing = (f" on {fmt_date(when)}, before the deal closed on {fmt_date(close)}" if when else "")
        reasons.append(f"{approver} approved it{timing}, and the approval is recorded in the CRM.")
        if not when:
            caveats.append("No 'Approved discount' audit event was found, so the approval date can't be confirmed.")
        return Finding("COMPLIANT", f"{who_what} followed {v['policy']}: the {fmt_pct(pct)} discount was approved by "
                       f"{approver}{timing}.", reasons, caveats, rule, facts,
                       {"needed_approval": True, "pct": pct, "approver_role": hof_role,
                        "approver_name": name if approver_is_hof else hof_events[0].get("actor_name"), "close": str(close)})

    if name:  # approved, but by the wrong person
        where = _where(arec)
        reasons = [base, f"The discount was {fmt_pct(pct)}, so {v['approver_role']} approval was required.",
                   f"It was approved by {_who(name, role)}{where}, not by the {v['approver_role']}."]
        if not in_crm:
            reasons.append("The approval is not recorded in the CRM's approval_record field.")
        if notes:
            reasons.append(f"The record's own note: \"{notes}\"")
        return Finding("VIOLATION", f"{who_what} broke {v['policy']}: its {fmt_pct(pct)} discount was approved by "
                       f"{_who(name, role)} instead of the {v['approver_role']}.", reasons, caveats, rule, facts,
                       {"needed_approval": True, "pct": pct, "approver_name": name, "approver_role": role,
                        "channel": arec, "close": str(close)})

    reasons = [base, f"The discount was {fmt_pct(pct)}, so approval was required.",
               f"The approval record says \"{arec or 'nothing'}\" and no approver is named.",
               f"The audit log has no 'Approved discount' event for {did}."]
    caveats.append("An approval may have been given without being recorded; the files can't confirm it either way.")
    return Finding("LIKELY_VIOLATION", f"{who_what} closed on {fmt_date(close)} with {a_pct(pct)} discount, "
                   f"which needed {v['approver_role']} approval, but no approval is recorded anywhere.",
                   reasons, caveats, rule, facts, {"needed_approval": True, "pct": pct, "close": str(close)})


# ---------------------------------------------------------------- expense claims: POL-07

_AMOUNT = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
_APPROVED_BY = re.compile(r"[Aa]pproved by ([A-Z][a-z]+(?:[ -][A-Z][\w'-]+)?)")
_SIGNED_OFF = re.compile(r"([A-Z][a-z]+(?: [A-Z][\w'-]+)?) signed off")


def check_expense(m: dict[str, Any], rb: dict[str, Any], lookup: Lookup) -> Finding:
    r = rb["expense_approval"]
    rule = _rule(r)
    tid = m.get("task_id") or "This task"
    text = fix_text(f"{m.get('project', '')}. {m.get('comments', '')}")
    comments = fix_text(m.get("comments"))
    owner = fix_text(m.get("owner_name"))
    status_hist = fix_text(m.get("status_history"))
    last_status = status_hist.split(";")[-1].split(":")[0] if status_hist else ""
    facts = [("Claim", fix_text(m.get("project"))), ("Claimant", _who(owner, fix_text(m.get("owner_role")))),
             ("Status", last_status or "unknown"), ("Comments", comments or "(none)")]
    thr = float(r["threshold_amount"])
    hof_role = r["approver_role"]

    am = _AMOUNT.search(text)
    amount = float(am.group(1).replace(",", "")) if am else None
    mm = _APPROVED_BY.search(comments) or _SIGNED_OFF.search(comments)
    approver = mm.group(1) if mm else None
    full, role = lookup(approver) if approver else (None, None)
    if amount is not None:
        facts.insert(1, ("Amount", f"${amount:,.0f}"))
    who = _who(full, role)
    auto = "auto-approved" in comments.lower() or "under threshold" in comments.lower()

    if auto and (amount is None or amount <= thr):
        return Finding("COMPLIANT", f"{tid} is a claim under the ${thr:,.0f} threshold that was approved without "
                       f"Finance, which POL-07 allows.", [f"The record says: \"{comments}\""], [], rule, facts)
    if role == hof_role:
        return Finding("COMPLIANT", f"{tid} was approved by {who}, which meets POL-07 whatever the amount.",
                       [f"POL-07 requires {hof_role} approval for claims over ${thr:,.0f}; {who} approved it."], [], rule, facts)
    if amount is not None and amount > thr:
        return Finding("VIOLATION", f"{tid} broke POL-07: a ${amount:,.0f} expense was approved by {who} instead of "
                       f"the {hof_role}.",
                       [f"POL-07 requires {hof_role} approval for expense claims over ${thr:,.0f}.",
                        f"This claim was ${amount:,.0f}.", f"It was approved by {who}" + (f": \"{comments}\"" if comments else ".")],
                       [], rule, facts, {"approver_name": full, "approver_role": role})
    if amount is not None:
        return Finding("COMPLIANT", f"{tid} is a ${amount:,.0f} claim, at or under ${thr:,.0f}, so a manager could "
                       f"approve it.", [f"${amount:,.0f} ≤ ${thr:,.0f}."], [], rule, facts)
    if approver:
        return Finding("CANNOT_TELL", f"{tid} was approved by {who}. That is only allowed if the claim was ${thr:,.0f} "
                       f"or less, and the amount isn't recorded.",
                       [f"POL-07 requires {hof_role} approval above ${thr:,.0f}.", "The task record has no amount."],
                       [], rule, facts)
    return Finding("CANNOT_TELL", f"{tid} is recorded as approved, but neither the amount nor the approver is in the "
                   f"record, so it can't be checked against POL-07.", [f"The record says: \"{comments}\""] if comments else [],
                   [], rule, facts)


# ---------------------------------------------------------------- dispatcher

def check_record(doc_type: str, meta: dict[str, Any], rb: dict[str, Any], lookup: Lookup,
                 approvals: list[dict[str, Any]] | None = None) -> Finding:
    if doc_type == rb["discount_new_business"]["record_type"] and meta.get("deal_id"):
        return check_deal(meta, rb, lookup, approvals)
    if doc_type == rb["expense_approval"]["record_type"] and meta.get("process_or_capability") == rb["expense_approval"]["process"]:
        return check_expense(meta, rb, lookup)
    facts = [(k.replace("_", " ").capitalize(), fix_text(str(v))) for k, v in meta.items() if str(v or "").strip()][:12]
    return Finding("NO_RULE", "No policy rule in the rulebook covers this kind of record, so here is what it says.",
                   [], [], None, facts)
