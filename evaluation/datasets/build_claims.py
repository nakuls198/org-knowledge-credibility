"""Builds evaluation/datasets/claims.csv (source of truth for the evaluation claim set).

Format follows the client's `labelled_claims_sample.csv` columns, extended with
claim_type / expected_verdict / split / label_source. Labels were written from a
close reading of the corpus BEFORE the assessment engine was run on them.

label_source:
  client_sample       - copied verbatim from the client's labelled sample (4 claims)
  provisional_author  - drafted by the project author; needs independent review

Convention (follows the client sample): for a "Formal truth" claim, the expected
contradicting sources list only sources that dispute the formal rule itself;
practice deviations from a rule are labelled on separate practice claims.

Splits are assigned by a hash of the claim id (no tuning on the test split).
Run:  python evaluation/datasets/build_claims.py
"""

import csv
import hashlib
from pathlib import Path

S, C, X, I = "SUPPORTED", "CONTRADICTED", "CONTESTED", "INSUFFICIENT_EVIDENCE"
OV, DR, RD = "company-overview", "decision-rights-table", "role-descriptions"
OC, OCS = "org-chart-and-roster", "org-chart-2023-02-superseded"

# id, category, claim_type, text, reference_status, verdict, supporting, contradicting, difficulty, source, notes
CLAIMS = [
    ("E01", "Financial control", "normative", "Discounts above 15% require Finance (Head of Finance & People Ops) approval before the deal is closed.", "Formal truth", S, ["POL-01", "INT-02", "INT-03", "MTG-01"], [], "Low", "client_sample", "client C01"),
    ("E02", "Financial control", "normative", "Discounts of 10% or more on new-business deals currently require approval from the Head of Finance & People Ops.", "Outdated", C, ["POL-02", "INT-05", "COMMS-05"], ["POL-01", "MTG-01", "INT-02"], "Medium", "provisional_author", "threshold raised to 15% from 2025-03-01; POL-02 superseded"),
    ("E03", "Financial control", "normative", "The Financial Controller approves discounts above the approval threshold.", "Outdated", C, ["PROC-02"], ["POL-01", RD, "INT-03"], "Medium", "provisional_author", "PROC-02 not updated after POL-01 v3"),
    ("E04", "Financial control", "practice", "In practice, the Head of Sales sometimes approves urgent discounts herself without waiting for Finance sign-off.", "Practice truth", S, ["MTG-02", "COMMS-01", "COMMS-02", "COMMS-03", "COMMS-04", "INT-02", "INT-03", "INT-09", DR, "DEAL-014", "DEAL-041"], ["INT-04"], "Medium", "provisional_author", "COMMS-02/03/04 and INT-09 trace back to MTG-02 (dependent)"),
    ("E05", "Financial control", "practice", "Every discount over 15% is recorded in the CRM approval_record field.", "False", C, ["INT-04"], ["DEAL-014", "DEAL-027", "DEAL-041", "COMMS-01", "COMMS-08"], "Medium", "provisional_author", ""),
    ("E06", "Financial control", "factual", "The 15% discount approval threshold was agreed at a leadership meeting on 2025-02-24.", "True", S, ["MTG-01", "POL-01"], [], "Low", "provisional_author", ""),
    ("E07", "Financial control", "factual", "A 20% discount approval threshold was adopted in February 2025.", "False", C, [], ["MTG-01", "POL-01"], "Low", "provisional_author", "option 3 discussed, not adopted"),
    ("E08", "Financial control", "factual", "The Head of Sales' temporary urgent-deal approval workaround was later formally reviewed and withdrawn.", "False", C, [], ["MTG-02"], "Medium", "provisional_author", "never revisited"),
    ("E09", "Process step", "normative", "New customer onboarding follows a standard 6-week implementation process owned by the Customer Success & Delivery team.", "Formal truth", S, ["POL-03", "PROC-01", "INT-06"], [], "Low", "client_sample", "client C09"),
    ("E10", "Decision rights", "normative", "The onboarding policy assigns the Head of Customer Success & Delivery formal authority to approve onboarding timeline extensions.", "False", C, [], ["POL-03", "PROC-01", DR, "INT-06"], "Medium", "provisional_author", "authority not formally assigned"),
    ("E11", "Decision rights", "practice", "Onboarding timeline extensions are informally cleared with the Head of Customer Success & Delivery.", "Practice truth", S, ["INT-06", DR], [], "Medium", "provisional_author", ""),
    ("E12", "Process step", "normative", "Every new-business account is assigned a named Implementation Specialist within 2 business days of contract signature.", "Formal truth", S, ["POL-03", "PROC-01"], [], "Low", "provisional_author", ""),
    ("E13", "Financial control", "practice", "Travel expenses for customer onboarding visits are treated as standing pre-approved without separate sign-off.", "Practice truth", S, ["PROC-04", "INT-03", "INT-06"], [], "Medium", "provisional_author", "undocumented exception"),
    ("E14", "Process step", "normative", "Feature requests are currently tracked on a Trello board.", "Outdated", C, ["PROC-03"], ["MTG-03", "INT-10", RD, "TASK-140"], "Medium", "provisional_author", "migrated to Linear April 2025; PROC-03 stale"),
    ("E15", "Process step", "factual", "Feature request tracking was migrated from Trello to Linear in April 2025.", "True", S, ["MTG-03", "INT-10", "TASK-140"], [], "Low", "provisional_author", ""),
    ("E16", "Decision rights", "normative", "There is a documented rule for how the two Product Managers divide incoming feature requests.", "False", C, [], [RD, "INT-10", DR], "Medium", "provisional_author", ""),
    ("E17", "Decision rights", "normative", "The Head of Engineering has final authority when Product and Engineering disagree on prioritisation.", "Contested", X, [RD], [DR, "INT-10"], "High", "provisional_author", "feasibility right exists; prioritisation split undocumented"),
    ("E18", "Financial control", "normative", "Expense claims over $500 require approval from the Head of Finance & People Ops before reimbursement.", "Formal truth", S, ["POL-07", "PROC-04", RD], [], "Low", "provisional_author", ""),
    ("E19", "Financial control", "practice", "Managers sometimes approve expense claims above $500 themselves instead of routing them to the Head of Finance & People Ops.", "Practice truth", S, ["MTG-09", "COMMS-09", "INT-03", DR, "TASK-095"], [], "Medium", "provisional_author", ""),
    ("E20", "Financial control", "factual", "The June 2026 quarterly finance review confirmed that expense processes are working well.", "False", C, ["MTG-09"], ["MTG-09", "INT-03"], "High", "provisional_author", "circulated summary contradicts the minutes"),
    ("E21", "Financial control", "factual", "The expense approval policy gap was fixed after the June 2026 quarterly review.", "False", C, [], ["MTG-09", "INT-03"], "Medium", "provisional_author", ""),
    ("E22", "Process step", "normative", "Customer-reported issues are escalated from Support to Engineering via a ticket in the support system, following a documented severity-based escalation path.", "Formal truth", S, ["PROC-05", "INT-07", "INT-08"], [], "Low", "client_sample", "client C27"),
    ("E23", "Process step", "normative", "The revised (v2) support escalation policy has been formally approved.", "False", C, ["INT-07"], ["POL-04", "MTG-04", "COMMS-06", DR], "High", "provisional_author", "pilot only; Sam believes it was signed off"),
    ("E24", "Process step", "practice", "Severity 1 production issues are often escalated directly to an engineer via Slack before a ticket is raised.", "Practice truth", S, ["PROC-05", "INT-07", "INT-08", "INT-10", "COMMS-07", "MTG-07"], [], "Medium", "provisional_author", ""),
    ("E25", "Process step", "practice", "Support reps close escalated tickets themselves, without checking with the Support Lead, once the customer confirms the fix.", "Contested", X, ["INT-07"], ["INT-08", "PROC-05"], "High", "provisional_author", "Sam and Ines disagree"),
    ("E26", "Process step", "factual", "The informal support escalation pilot was formally reviewed at the end of the quarter.", "False", C, [], ["MTG-04"], "Medium", "provisional_author", ""),
    ("E27", "Process step", "normative", "All new hires complete a documented onboarding checklist within their first two weeks, owned by People Ops.", "Formal truth", S, ["POL-08", "COMMS-12"], [], "Low", "client_sample", "client C32"),
    ("E28", "Decision rights", "normative", "The People Ops Lead is the sole approver of a new hire's probation completion.", "Formal truth", S, ["POL-08", DR, RD], [], "Low", "provisional_author", ""),
    ("E29", "Process step", "normative", "The new-hire onboarding policy includes specific provisions for remote employees.", "False", C, [], ["POL-08"], "Low", "provisional_author", ""),
    ("E30", "Key-person risk", "factual", "Ana Kowalski is the only engineer with working production knowledge of the CRM-billing integration.", "True", S, [RD, "INT-10", "MTG-07", "TASK-102", "TASK-130", "TASK-145"], [], "Low", "provisional_author", ""),
    ("E31", "Key-person risk", "factual", "Documentation of the billing integration was completed after the February 2026 incident.", "False", C, [], ["INT-10", "MTG-07", "TASK-145"], "Medium", "provisional_author", ""),
    ("E32", "Decision rights", "normative", "Production deployments touching customer billing require sign-off from the Head of Engineering.", "Formal truth", S, [RD, DR], [], "Medium", "provisional_author", "one emergency fix without sign-off record (AUD-0111) is a practice caveat"),
    ("E33", "Process step", "normative", "An invoice is generated automatically once a deal is Closed Won and its onboarding project is marked complete.", "Formal truth", S, ["PROC-06"], [], "Low", "provisional_author", ""),
    ("E34", "Process step", "normative", "If an invoice is overdue by 45 days, the Financial Controller escalates to the Head of Finance & People Ops for a decision.", "Formal truth", S, ["PROC-06"], [], "Low", "provisional_author", ""),
    ("E35", "Financial control", "normative", "Renewal discounts are capped at 10% of the account's current contracted rate.", "Formal truth", S, ["POL-06", "MTG-05", DR], [], "Medium", "provisional_author", "practice exceptions exist (DEAL-063, MTG-10)"),
    ("E36", "Decision rights", "normative", "There is a documented tie-breaker when Sales and Customer Success disagree on renewal terms.", "False", C, [], ["PROC-07", DR, "MTG-05", RD], "Medium", "provisional_author", ""),
    ("E37", "Renewals", "factual", "The Ridgeway account renewal discount disagreement was resolved with an agreed discount.", "False", C, [], ["MTG-05", "DEAL-064", "INT-02"], "Medium", "provisional_author", ""),
    ("E38", "Renewals", "factual", "Ashgrove Trades was offered a 12% discount in exchange for a two-year renewal.", "True", S, ["MTG-10", "TASK-146"], [], "Low", "provisional_author", ""),
    ("E39", "Renewals", "normative", "A discount of 18% for prepaid multi-year renewals is a documented standard exception to the renewal discount cap.", "False", C, ["COMMS-10"], ["COMMS-10", "POL-06", "DEAL-063"], "High", "provisional_author", "Diane calls it standard; nothing written down"),
    ("E40", "Strategy", "factual", "Northfield has decided to permanently abandon expansion into the commercial cleaning vertical.", "False", C, [], ["MTG-06", "INT-01"], "Medium", "provisional_author", "parked, not killed"),
    ("E41", "Strategy", "factual", "Northfield has signed a new venture funding round.", "False", C, [], ["INT-01", OV], "Medium", "provisional_author", ""),
    ("E42", "People", "factual", "Wei Zhang left Northfield on 2026-04-30.", "True", S, [OC, "MTG-08", "COMMS-14", "TASK-142"], [], "Low", "provisional_author", ""),
    ("E43", "Key-person risk", "factual", "Complete billing-integration testing notes were handed over before Wei Zhang's departure.", "False", C, [], ["COMMS-14", "MTG-08", "TASK-142"], "Medium", "provisional_author", ""),
    ("E44", "People", "normative", "The Head of Support currently reports to the COO.", "Outdated", C, [OCS], [OC], "Medium", "provisional_author", "only true in the superseded 2023 org chart"),
    ("E45", "HR", "normative", "All leave requests require manager approval.", "Insufficient", I, [], [], "Low", "provisional_author", "no leave policy in corpus"),
    ("E46", "Data handling", "normative", "Customer data must never be exported to a personal device.", "False", C, [], ["POL-05"], "Low", "provisional_author", "export allowed if deleted within 24h"),
    ("E47", "Decision rights", "normative", "The QA Lead has formal authority to block a release for a data-quality defect.", "False", C, [], [DR, RD], "Medium", "provisional_author", ""),
    ("E48", "Organisation", "factual", "Northfield had 29 employees as of 31 July 2026.", "True", S, [OV], [], "Low", "provisional_author", ""),
    ("E49", "Data handling", "factual", "The data handling and export policy review is up to date.", "False", C, [], ["POL-05"], "Medium", "provisional_author", "review overdue"),
    ("E50", "Process step", "normative", "Severity 1 support escalations are expected to be resolved within the same business day.", "Formal truth", S, ["PROC-05"], [], "Low", "provisional_author", ""),
    ("E51", "Operations", "factual", "The support team provides 24/7 on-call coverage for all customers.", "Insufficient", I, [], [], "Low", "provisional_author", "no evidence either way"),
    ("E52", "Sales", "factual", "Northfield offers new customers a 30-day free trial.", "Insufficient", I, [], [], "Low", "provisional_author", "no evidence either way"),
]


def split_of(cid: str) -> str:
    return "test" if int(hashlib.sha1(cid.encode()).hexdigest(), 16) % 10 < 4 else "dev"


def main() -> None:
    out = Path(__file__).with_name("claims.csv")
    cols = ["claim_id", "split", "label_source", "claim_category", "claim_type", "reference_claim", "reference_status",
            "expected_verdict", "expected_supporting_sources", "expected_contradictory_sources", "difficulty_level", "notes"]
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for cid, cat, ctype, text, status, verdict, sup, con, diff, src, notes in CLAIMS:
            w.writerow([cid, split_of(cid), src, cat, ctype, text, status, verdict, "; ".join(sup), "; ".join(con), diff, notes])
    print(f"wrote {len(CLAIMS)} claims -> {out}")


if __name__ == "__main__":
    main()
