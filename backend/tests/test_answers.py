"""Plain answers: record compliance checks, question understanding, rule questions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from okc.answers import compliance as cp
from okc.answers.understand import record_refs, rephrase, scan_subject, yes_no_to_statement
from okc.answers.rules_qa import answer_rule_question, topic_of
from okc.settings import load_config

ROLES = {"Harriet Doyle": "Head of Finance & People Ops", "Diane Okafor": "Head of Sales",
         "Lior Ben-David": "Head of Engineering", "Priya Anand": "Account Executive"}


def lookup(name: str):
    name = (name or "").strip()
    if name in ROLES:
        return name, ROLES[name]
    for full in ROLES:
        if full.split()[0] == name:
            return full, ROLES[full]
    return name, None


@pytest.fixture(scope="module")
def rb():
    return load_config("compliance")


def deal(**kw):
    base = {"deal_id": "DEAL-900", "customer_name": "Acme", "stage": "Closed Won", "discount_pct": "20",
            "close_date": "2025-10-15", "approval_record": "", "approval_role": "", "reason_lost_won": "", "notes": ""}
    return {**base, **kw}


# ---------------------------------------------------------------- deals

def test_unrecorded_approval_is_a_likely_violation(rb):
    f = cp.check_deal(deal(approval_record="None found"), rb, lookup)
    assert f.outcome == "LIKELY_VIOLATION" and f.rule["policy"] == "POL-01"
    assert "no approval is recorded" in f.answer


def test_finance_approval_before_close_complies(rb):
    f = cp.check_deal(deal(approval_record="Recorded in CRM", approval_role="Harriet Doyle"), rb, lookup,
                      [{"action": "Approved discount", "actor_name": "Harriet Doyle", "timestamp": "2025-10-01 10:00"}])
    assert f.outcome == "COMPLIANT"
    assert "1 Oct 2025" in f.answer


def test_finance_approval_after_close_is_a_violation(rb):
    f = cp.check_deal(deal(approval_record="Recorded in CRM", approval_role="Harriet Doyle"), rb, lookup,
                      [{"action": "Approved discount", "actor_name": "Harriet Doyle", "timestamp": "2025-10-20 10:00"}])
    assert f.outcome == "VIOLATION" and "after it closed" in f.answer


def test_wrong_approver_is_a_violation(rb):
    f = cp.check_deal(deal(discount_pct="18", approval_record="Recorded outside CRM (Slack)",
                           approval_role="Diane Okafor (Slack, 2025-07-02)"), rb, lookup)
    assert f.outcome == "VIOLATION"
    assert f.extra["approver_role"] == "Head of Sales"


def test_below_threshold_complies_and_threshold_follows_the_policy_version(rb):
    assert cp.check_deal(deal(discount_pct="12"), rb, lookup).outcome == "COMPLIANT"  # POL-01: 15%
    old = cp.check_deal(deal(discount_pct="12", close_date="2024-05-01"), rb, lookup)  # POL-02 era: 10%
    assert old.outcome == "LIKELY_VIOLATION" and old.rule["policy"] == "POL-02"


def test_renewal_over_cap_breaks_pol06_even_with_approval(rb):
    f = cp.check_deal(deal(discount_pct="18", reason_lost_won="Renewal - satisfied customer",
                           approval_record="Recorded in CRM", approval_role="Harriet Doyle"), rb, lookup)
    assert f.outcome == "VIOLATION" and f.rule["policy"] == "POL-06"
    assert "an 18% discount" in f.answer


def test_impossible_discount_uses_the_corrected_value_from_notes(rb):
    f = cp.check_deal(deal(discount_pct="150", notes="Typo? Should be 15, not 150",
                           approval_record="Recorded in CRM", approval_role="Harriet Doyle"), rb, lookup)
    assert f.outcome == "COMPLIANT" and any("150%" in c for c in f.caveats)


def test_lost_and_open_deals_are_not_applicable(rb):
    assert cp.check_deal(deal(stage="Closed Lost"), rb, lookup).outcome == "NOT_APPLICABLE"
    open_ = cp.check_deal(deal(stage="Negotiation", close_date=""), rb, lookup)
    assert open_.outcome == "NOT_APPLICABLE" and "still open" in open_.answer


def test_missing_discount_cannot_be_checked(rb):
    assert cp.check_deal(deal(discount_pct=""), rb, lookup).outcome == "CANNOT_TELL"


# ---------------------------------------------------------------- expenses

def expense(comments: str, project: str = "Expense Claim - Colm Brennan"):
    return {"task_id": "TASK-900", "project": project, "comments": comments, "owner_name": "Colm Brennan",
            "owner_role": "Engineer", "status_history": "Done:2025-11-05", "process_or_capability": "Expense Approval"}


def test_expense_over_limit_signed_off_by_wrong_person(rb):
    f = cp.check_expense(expense("$680 conference costs — Lior signed off directly rather than routing to Finance."),
                         rb, lookup)
    assert f.outcome == "VIOLATION" and "$680" in f.answer and "Lior Ben-David" in f.answer


def test_expense_approved_by_finance_or_under_threshold_complies(rb):
    assert cp.check_expense(expense("Approved by Harriet Doyle after clarification."), rb, lookup).outcome == "COMPLIANT"
    assert cp.check_expense(expense("Minor claim under threshold, auto-approved."), rb, lookup).outcome == "COMPLIANT"


def test_expense_without_amount_or_approver_cannot_be_checked(rb):
    assert cp.check_expense(expense("Routine expense claim, approved within normal process."), rb, lookup).outcome \
        == "CANNOT_TELL"


def test_text_repair():
    assert cp.fix_text("costs â€” Lior") == "costs — Lior"


# ---------------------------------------------------------------- understanding questions

def test_record_references():
    ids = {"DEAL-027", "DEAL-014", "TASK-095"}
    assert record_refs("Is DEAL-027 a violation of policy?", ids.__contains__) == ["DEAL-027"]
    assert record_refs("is deal 14 ok? and task 95?", ids.__contains__) == ["DEAL-014", "TASK-095"]
    assert record_refs("Is POL-01 approved?", ids.__contains__) == []


def test_scan_questions():
    assert scan_subject("Which deals violated the discount policy?") == "deals"
    assert scan_subject("Any expense claims that broke policy?") == "expenses"
    assert scan_subject("Who approves big discounts?") is None


@pytest.mark.parametrize("words,expected", [
    ("Do support reps escalate Sev 1 issues through tickets", "Support reps escalate Sev 1 issues through tickets."),
    ("Is the onboarding timeline really six weeks", "The onboarding timeline is really six weeks."),
    ("Are renewal discounts capped at 10 percent", "Renewal discounts are capped at 10 percent."),
    ("Can the Head of Sales approve discounts above 15%", "The Head of Sales can approve discounts above 15%."),
])
def test_yes_no_questions_become_statements(words, expected):
    assert yes_no_to_statement(words.split()) == expected


def test_wh_questions_use_the_files_statement_or_passages():
    stmt = {"text": "Probation period is 3 months from start date.", "document_id": "POL-08",
            "section_id": "POL-08:formal-requirements", "section_heading": "Formal requirements"}
    rp = rephrase("How long is the probation period?", lambda q: stmt)
    assert rp.claim == stmt["text"] and rp.source == stmt
    assert rephrase("Where are feature requests tracked?", lambda q: stmt).claim is None  # -> passage answer
    assert rephrase("hi there", lambda q: None).claim is None


# ---------------------------------------------------------------- rule questions

class FakeGz:
    role_people = {"Head of Finance & People Ops": ["Harriet Doyle"]}

    def find(self, text: str):
        out = []
        if "Head of Sales" in text:
            out.append(SimpleNamespace(kind="role", person=None, role="Head of Sales"))
        if "manager" in text:
            out.append(SimpleNamespace(kind="generic", person=None, role=None))
        return out


def test_rule_question_topics():
    assert topic_of("Can my manager approve a $700 expense?") == "expense"
    assert topic_of("Is a 12% renewal discount allowed?") == "renewal"
    assert topic_of("Are renewal discounts ever above 10%?") == "renewal"
    assert topic_of("Who approves big discounts?") == "discount"
    assert topic_of("How long is probation?") is None


def test_permission_answers(rb):
    gz = FakeGz()
    viol = [("DEAL-014", cp.check_deal(deal(deal_id="DEAL-014", discount_pct="18", approval_record="Recorded outside CRM (Slack)",
                                            approval_role="Diane Okafor"), rb, lookup))]
    a = answer_rule_question("Can the Head of Sales approve discounts above 15%?", "discount", rb, gz, {"deals": viol})
    assert a.outcome == "NO" and "Harriet Doyle" in a.answer and "DEAL-014" in a.practice_records
    a = answer_rule_question("can i get approval for 700$ expense from my manager?", "expense", rb, gz, {})
    assert a.outcome == "NO" and "$700" in a.answer
    a = answer_rule_question("Is a 12% renewal discount allowed?", "renewal", rb, gz, {})
    assert a.outcome == "NO"
    a = answer_rule_question("Is a 12% discount ok without Finance?", "discount", rb, gz, {})
    assert a.outcome == "YES"


def test_practice_answers_come_from_the_records(rb):
    gz = FakeGz()
    deals = [("DEAL-014", cp.check_deal(deal(deal_id="DEAL-014", discount_pct="18", approval_record="Recorded outside CRM (Slack)",
                                             approval_role="Diane Okafor"), rb, lookup)),
             ("DEAL-001", cp.check_deal(deal(deal_id="DEAL-001", approval_record="Recorded in CRM",
                                             approval_role="Harriet Doyle"), rb, lookup))]
    a = answer_rule_question("In practice, the Head of Sales sometimes approves discounts herself.", "discount", rb, gz,
                             {"deals": deals})
    assert a.outcome == "YES" and "DEAL-014" in a.answer
    a = answer_rule_question("Who actually approves big discounts?", "discount", rb, gz, {"deals": deals})
    assert a.outcome == "INFO" and "1 of the 2" in a.answer


# ---------------------------------------------------------------- replacement signal

def test_replacement_signal():
    from okc.evidence.signals import SignalExtractor

    gz = SimpleNamespace(people={"Grace Whitfield": {}}, role_function={"Product Manager": ""},
                         non_tool_names=set(load_config("entities")["non_tool_names"]))
    signals = SignalExtractor(load_config("reasoning"), gz)
    claim = SimpleNamespace(normalized="Feature requests are currently tracked on a Trello board.")
    assert signals.replacement(claim, "Migrate fully to Linear. Trello board archived (read-only) after that.")
    assert signals.replacement(claim, "Re-entered open Trello cards into Linear; board archived after this.")
    assert signals.replacement(claim, "Requester adds a card to the Feature Requests Trello board.") is None
    moved = SimpleNamespace(normalized="Feature requests moved from Trello to Linear.")
    assert signals.replacement(moved, "Re-entered open Trello cards into Linear.") is None  # the claim already says so
