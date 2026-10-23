"""Record compliance checks: one record against the policy rule that governs it.

Pure functions over a record's fields (no database access) so each rule is easy to test and audit.
The rules come from `config/compliance.yaml`, which restates the formal requirements of the policies
(with the section each comes from). A check returns a Finding: an outcome, a one-line headline, a
plain answer, the reasons behind it and any caveats.

Outcomes
  VIOLATION         the record shows the rule was broken
  LIKELY_VIOLATION  the rule required something the records do not show (e.g. no approval recorded)
  COMPLIANT         the record meets the rule
  NOT_APPLICABLE    the rule does not apply to this record (e.g. the deal never closed)
  CANNOT_TELL       a field the rule depends on is missing
  NO_RULE           no rule in the rulebook covers this kind of record

CRM deal columns used: deal_id, customer_name, stage, close_date, discount_pct, approval_record,
approval_role, reason_lost_won, notes. Task columns used: task_id, project, comments, owner_name,
owner_role, status_history, process_or_capability.
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


def version_in_force(versions: list[dict[str, Any]], on: date | None) -> dict[str, Any]:
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
    return lookup(name) if name else (None, None)


def _where(approval_record: str) -> str:
    """'Recorded outside CRM (Slack)' -> ', recorded outside CRM (Slack)'."""
    return f", {approval_record[:1].lower()}{approval_record[1:]}" if approval_record else ""


def _who(name: str | None, role: str | None) -> str:
    return f"{name} ({role})" if name and role else (name or role or "someone")


# ---------------------------------------------------------------- CRM deals: discount approval rules

@dataclass
class _Deal:
    """One CRM deal, with the derived facts the rules need."""

    id: str
    customer: str
    stage: str
    close: date | None
    notes: str
    approval_record: str
    approver_text: str
    reason: str
    pct: float | None
    renewal: bool
    caveats: list[str]
    facts: list[tuple[str, str]]

    @property
    def label(self) -> str:
        return f"{self.id}{f' ({self.customer})' if self.customer else ''}"


def _read_deal(m: dict[str, Any], rb: dict[str, Any]) -> _Deal:
    did = m.get("deal_id") or "This deal"
    customer, stage = fix_text(m.get("customer_name")), fix_text(m.get("stage"))
    close = to_date(m.get("close_date"))
    notes, approval_record = fix_text(m.get("notes")), fix_text(m.get("approval_record"))
    approver_text, reason = fix_text(m.get("approval_role")), fix_text(m.get("reason_lost_won"))
    raw_pct = str(m.get("discount_pct") or "").strip()

    facts = [("Customer", customer), ("Stage", stage), ("Discount", f"{raw_pct}%" if raw_pct else "not recorded"),
             ("Closed", fmt_date(close) if close else "not recorded"), ("Approval record", approval_record or "(empty)"),
             ("Approved by", approver_text or "(empty)")]
    if reason:
        facts.append(("Outcome reason", reason))
    if notes:
        facts.append(("Notes", notes))

    caveats: list[str] = []
    try:
        pct: float | None = float(raw_pct)
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
    renewal = any(w in f"{reason} {notes} {approver_text}".lower() for w in markers)
    return _Deal(did, customer, stage, close, notes, approval_record, approver_text, reason, pct, renewal, caveats, facts)


def check_deal(m: dict[str, Any], rb: dict[str, Any], lookup: Lookup,
               approvals: list[dict[str, Any]] | None = None) -> Finding:
    """Check a CRM deal against the discount approval rules.

    m: the deal's fields (crm_deals.csv columns); approvals: its approval audit events.
    """
    deal = _read_deal(m, rb)
    stages = rb["stages"]
    if deal.stage.lower() == stages["lost"].lower():
        return _lost_deal(deal, rb)
    if deal.stage.lower() != stages["won"].lower():
        return _open_deal(deal, rb)
    if deal.pct is None:
        rule = _rule(version_in_force(rb["discount_new_business"]["versions"], deal.close))
        return Finding("CANNOT_TELL", f"{deal.label} closed won, but its discount isn't recorded, so it can't be checked "
                       f"against the discount rules.", ["The discount_pct field is empty."],
                       deal.caveats + ([deal.notes] if deal.notes else []), rule, deal.facts)
    if deal.renewal:
        return _closed_renewal(deal, rb, lookup)
    return _closed_new_business(deal, rb, lookup, approvals or [])


def _lost_deal(deal: _Deal, rb: dict[str, Any]) -> Finding:
    rule = _rule({**rb["discount_renewal"]}) if deal.renewal else _rule(
        version_in_force(rb["discount_new_business"]["versions"], deal.close))
    return Finding("NOT_APPLICABLE", f"{deal.label} was lost, so it never reached {rb['stages']['won']} and the discount "
                   f"rule never came into force.", [f"Stage: {rb['stages']['lost']} ({fmt_date(deal.close)})."],
                   deal.caveats, rule, deal.facts)


def _open_deal(deal: _Deal, rb: dict[str, Any]) -> Finding:
    new_business = rb["discount_new_business"]
    rule = rb["discount_renewal"] if deal.renewal else version_in_force(new_business["versions"], None)
    pct = deal.pct
    if pct is None:
        need = "The discount isn't recorded yet."
    elif deal.renewal:
        need = (f"Its {fmt_pct(pct)} discount is above {rule['policy']}'s {rule['cap_pct']}% renewal cap, so it cannot close "
                f"at that discount." if pct > rule["cap_pct"] else f"Its {fmt_pct(pct)} discount is within the cap.")
    else:
        need = (f"At {fmt_pct(pct)} it will need {rule['approver_role']} approval, recorded in the CRM, before "
                f"it can be marked {rb['stages']['won']}." if pct >= rule["threshold_pct"]
                else f"At {fmt_pct(pct)} the {new_business['below_threshold_approver_role']} can approve it.")
    return Finding("NOT_APPLICABLE", f"Not yet: {deal.label} is still open ({deal.stage}). {need}",
                   [f"The rule applies when a deal is marked {rb['stages']['won']}; {deal.id} is at {deal.stage}."],
                   deal.caveats, _rule(rule), deal.facts)


def _closed_renewal(deal: _Deal, rb: dict[str, Any], lookup: Lookup) -> Finding:
    """POL-06 style: a hard cap on renewal discounts, no exceptions."""
    renewal = rb["discount_renewal"]
    rule, cap, pct = _rule(renewal), float(renewal["cap_pct"]), deal.pct
    if pct <= cap:
        return Finding("COMPLIANT", f"{deal.label} is a renewal with {a_pct(pct)} discount, within {renewal['policy']}'s "
                       f"{fmt_pct(cap)} renewal cap.", [f"Renewal discount {fmt_pct(pct)} ≤ cap {fmt_pct(cap)}."],
                       deal.caveats, rule, deal.facts)
    reasons = [f"It is a renewal (\"{deal.reason}\"), so {renewal['policy']} applies, not the new-business rule.",
               f"The discount was {fmt_pct(pct)}; {renewal['policy']} caps renewal discounts at {fmt_pct(cap)} and allows no exceptions."]
    name, role = _person_role(deal.approver_text, lookup)
    if name:
        reasons.append(f"It was approved by {_who(name, role)}{_where(deal.approval_record)}, but {renewal['policy']} "
                       f"has no exception process, so an approval does not make it compliant.")
    return Finding("VIOLATION", f"{deal.label} broke the renewal discount cap: it closed on {fmt_date(deal.close)} with "
                   f"{a_pct(pct)} discount, above {renewal['policy']}'s {fmt_pct(cap)} cap.", reasons, deal.caveats, rule,
                   deal.facts, {"pct": pct, "close": str(deal.close), "approver_name": name, "approver_role": role,
                                "channel": deal.approval_record})


def _closed_new_business(deal: _Deal, rb: dict[str, Any], lookup: Lookup, approvals: list[dict[str, Any]]) -> Finding:
    """POL-01 / POL-02 style: discounts at or above a threshold need the finance approver's recorded approval."""
    section = rb["discount_new_business"]
    version = version_in_force(section["versions"], deal.close)
    rule, threshold, pct = _rule(version), float(version["threshold_pct"]), deal.pct
    policy, approver_role = version["policy"], version["approver_role"]
    if pct < threshold:
        return Finding("COMPLIANT", f"{deal.label} closed with {a_pct(pct)} discount, below {policy}'s "
                       f"{fmt_pct(threshold)} threshold, so the {section['below_threshold_approver_role']} could approve it "
                       f"without Finance sign-off.",
                       [f"Discount {fmt_pct(pct)} < threshold {fmt_pct(threshold)} ({policy}, in force on {fmt_date(deal.close)})."],
                       deal.caveats, rule, deal.facts, {"needed_approval": False, "pct": pct})

    finance_role = rb.get("finance_approver_role", approver_role)
    name, role = _person_role(deal.approver_text, lookup)
    finance_events = [a for a in approvals if (a.get("actor_role") == finance_role
                                               or lookup(a.get("actor_name", ""))[1] == finance_role)]
    first_approval = min((to_date(a.get("timestamp")) for a in finance_events if to_date(a.get("timestamp"))), default=None)
    base = (f"{policy} (in force on the close date, {fmt_date(deal.close)}) requires {approver_role} approval, "
            f"recorded in the CRM, for discounts of {fmt_pct(threshold)} or more before {rb['stages']['won']}.")
    in_crm = rb["crm_approval_marker"].lower() in deal.approval_record.lower()
    approver_is_finance = role == finance_role
    extra = {"needed_approval": True, "pct": pct, "close": str(deal.close)}

    if approver_is_finance or finance_events:
        reasons = [base, f"The discount was {fmt_pct(pct)}, so approval was required."]
        if first_approval and deal.close and first_approval > deal.close:
            reasons.append(f"The audit log shows {finance_role} approval on {fmt_date(first_approval)}, after the deal "
                           f"closed on {fmt_date(deal.close)}.")
            return Finding("VIOLATION", f"{deal.label} was approved by the {finance_role} only after it closed.",
                           reasons, deal.caveats, rule, deal.facts, {**extra, "approver_role": finance_role})
        if not in_crm and version.get("must_be_recorded_in_crm"):
            reasons.append(f"The approval came from the {finance_role} but is recorded as \"{deal.approval_record}\", not in the CRM.")
            return Finding("VIOLATION", f"{deal.label} had the right approver, but the approval is not recorded in the "
                           f"CRM as {policy} requires.", reasons, deal.caveats, rule, deal.facts,
                           {**extra, "approver_role": finance_role})
        approver = _who(name, role) if approver_is_finance else _who(finance_events[0].get("actor_name"), finance_role)
        timing = (f" on {fmt_date(first_approval)}, before the deal closed on {fmt_date(deal.close)}" if first_approval else "")
        reasons.append(f"{approver} approved it{timing}, and the approval is recorded in the CRM.")
        caveats = list(deal.caveats)
        if not first_approval:
            caveats.append(f"No '{rb['approval_audit_action']}' audit event was found, so the approval date can't be confirmed.")
        return Finding("COMPLIANT", f"{deal.label} followed {policy}: the {fmt_pct(pct)} discount was approved by "
                       f"{approver}{timing}.", reasons, caveats, rule, deal.facts,
                       {**extra, "approver_role": finance_role,
                        "approver_name": name if approver_is_finance else finance_events[0].get("actor_name")})

    if name:  # approved, but by the wrong person
        reasons = [base, f"The discount was {fmt_pct(pct)}, so {approver_role} approval was required.",
                   f"It was approved by {_who(name, role)}{_where(deal.approval_record)}, not by the {approver_role}."]
        if not in_crm:
            reasons.append("The approval is not recorded in the CRM's approval_record field.")
        if deal.notes:
            reasons.append(f"The record's own note: \"{deal.notes}\"")
        return Finding("VIOLATION", f"{deal.label} broke {policy}: its {fmt_pct(pct)} discount was approved by "
                       f"{_who(name, role)} instead of the {approver_role}.", reasons, deal.caveats, rule, deal.facts,
                       {**extra, "approver_name": name, "approver_role": role, "channel": deal.approval_record})

    reasons = [base, f"The discount was {fmt_pct(pct)}, so approval was required.",
               f"The approval record says \"{deal.approval_record or 'nothing'}\" and no approver is named.",
               f"The audit log has no '{rb['approval_audit_action']}' event for {deal.id}."]
    caveats = deal.caveats + ["An approval may have been given without being recorded; the files can't confirm it either way."]
    return Finding("LIKELY_VIOLATION", f"{deal.label} closed on {fmt_date(deal.close)} with {a_pct(pct)} discount, "
                   f"which needed {approver_role} approval, but no approval is recorded anywhere.",
                   reasons, caveats, rule, deal.facts, extra)


# ---------------------------------------------------------------- expense claims

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
    status_history = fix_text(m.get("status_history"))
    last_status = status_history.split(";")[-1].split(":")[0] if status_history else ""
    facts = [("Claim", fix_text(m.get("project"))), ("Claimant", _who(owner, fix_text(m.get("owner_role")))),
             ("Status", last_status or "unknown"), ("Comments", comments or "(none)")]
    threshold, finance_role = float(r["threshold_amount"]), r["approver_role"]

    amount_match = _AMOUNT.search(text)
    amount = float(amount_match.group(1).replace(",", "")) if amount_match else None
    approver_match = _APPROVED_BY.search(comments) or _SIGNED_OFF.search(comments)
    approver = approver_match.group(1) if approver_match else None
    full_name, role = lookup(approver) if approver else (None, None)
    if amount is not None:
        facts.insert(1, ("Amount", f"${amount:,.0f}"))
    who = _who(full_name, role)
    auto_approved = any(marker in comments.lower() for marker in r.get("auto_approval_markers", []))

    if auto_approved and (amount is None or amount <= threshold):
        return Finding("COMPLIANT", f"{tid} is a claim under the ${threshold:,.0f} threshold that was approved without "
                       f"Finance, which {r['policy']} allows.", [f"The record says: \"{comments}\""], [], rule, facts)
    if role == finance_role:
        return Finding("COMPLIANT", f"{tid} was approved by {who}, which meets {r['policy']} whatever the amount.",
                       [f"{r['policy']} requires {finance_role} approval for claims over ${threshold:,.0f}; {who} approved it."],
                       [], rule, facts)
    if amount is not None and amount > threshold:
        return Finding("VIOLATION", f"{tid} broke {r['policy']}: a ${amount:,.0f} expense was approved by {who} instead of "
                       f"the {finance_role}.",
                       [f"{r['policy']} requires {finance_role} approval for expense claims over ${threshold:,.0f}.",
                        f"This claim was ${amount:,.0f}.", f"It was approved by {who}" + (f": \"{comments}\"" if comments else ".")],
                       [], rule, facts, {"approver_name": full_name, "approver_role": role})
    if amount is not None:
        return Finding("COMPLIANT", f"{tid} is a ${amount:,.0f} claim, at or under ${threshold:,.0f}, so a manager could "
                       f"approve it.", [f"${amount:,.0f} ≤ ${threshold:,.0f}."], [], rule, facts)
    if approver:
        return Finding("CANNOT_TELL", f"{tid} was approved by {who}. That is only allowed if the claim was ${threshold:,.0f} "
                       f"or less, and the amount isn't recorded.",
                       [f"{r['policy']} requires {finance_role} approval above ${threshold:,.0f}.", "The task record has no amount."],
                       [], rule, facts)
    return Finding("CANNOT_TELL", f"{tid} is recorded as approved, but neither the amount nor the approver is in the "
                   f"record, so it can't be checked against {r['policy']}.",
                   [f"The record says: \"{comments}\""] if comments else [], [], rule, facts)


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
