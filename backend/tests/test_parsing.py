"""Markdown / CSV parsing, metadata extraction, stable ids and hashing."""

from okc.common.text import split_units, strip_markdown
from okc.corpus.csv_records import CsvParser
from okc.corpus.markdown import MarkdownParser
from okc.corpus.metadata import normalise_status, path_matches

POLICY = """# POL-77 — Travel Policy

- **Document owner:** Alex Doe, Head of Finance
- **Version:** 3.0
- **Approval status:** Approved
- **Effective date:** 2025-03-01
- **Supersedes:** POL-76 (v2)

## Purpose

To keep travel spend sensible.

## Formal requirements

1. Flights over $500 require approval from the Head of Finance.
2. Hotels must be booked through the portal.

```
# not a heading inside a code fence
```
"""


def parse(text: str, path: str = "02_policies/POL-77_travel.md", cfg=None):
    from okc.settings import load_config
    from okc.common.text import sha256

    return MarkdownParser(cfg or load_config("ingestion")).parse(path, text, sha256(text))


def test_metadata_and_identity():
    pf = parse(POLICY)
    assert len(pf.documents) == 1
    d = pf.documents[0]
    assert d.id == "POL-77" and d.identifier == "POL-77"
    assert d.doc_type == "policy" and d.doc_type_source.startswith("folder_rule")
    assert d.status == "approved" and d.version_label == "3.0"
    assert str(d.effective_date) == "2025-03-01" and d.date_source == "effective_date"
    assert d.owner == "Alex Doe" and d.people == ["Alex Doe"]
    assert d.meta["supersedes"].startswith("POL-76")


def test_sections_follow_headings_with_line_spans():
    d = parse(POLICY).documents[0]
    ids = [s.id for s in d.sections]
    assert ids == ["POL-77:header", "POL-77:purpose", "POL-77:formal-requirements"]  # code-fence '#' ignored
    req = d.sections[2]
    assert req.section_role == "requirement"
    assert req.heading_path == ["POL-77 — Travel Policy", "Formal requirements"]
    lines = POLICY.split("\n")
    assert lines[req.start_line - 1].startswith("## Formal requirements")
    assert "Flights over $500" in req.plain_text and "**" not in req.plain_text


def test_ids_and_hashes_are_stable_and_change_with_content():
    a, b = parse(POLICY).documents[0], parse(POLICY).documents[0]
    assert [s.id for s in a.sections] == [s.id for s in b.sections]
    assert [s.content_hash for s in a.sections] == [s.content_hash for s in b.sections]
    c = parse(POLICY.replace("$500", "$600")).documents[0]
    assert c.sections[2].content_hash != a.sections[2].content_hash
    assert c.sections[1].content_hash == a.sections[1].content_hash  # untouched section keeps its hash


def test_yaml_front_matter_overrides_type():
    text = "---\ntitle: X\ntype: guideline\nowner: Sam Roe\n---\n# Guide\n\n## Body\n\nText here.\n"
    d = parse(text, "misc/guide.md").documents[0]
    assert d.doc_type == "guideline" and d.doc_type_source == "metadata"
    assert d.sections[0].start_line == 6  # line numbers count the front matter


def test_invalid_front_matter_reported():
    pf = parse("---\n: : bad\n---\n# T\n\nx\n", "misc/bad.md")
    assert any(i.issue_type == "invalid_front_matter" for i in pf.issues)


def test_long_section_is_chunked_with_parent():
    from okc.settings import load_config

    cfg = {**load_config("ingestion"), "max_section_chars": 120}
    body = "\n".join(f"{i}. Step number {i} explains a part of the procedure in some detail." for i in range(1, 9))
    d = parse(f"# PROC-55 — Long\n\n## Steps\n\n{body}\n", "03_process_docs/PROC-55_long.md", cfg).documents[0]
    parent = next(s for s in d.sections if s.id == "PROC-55:steps")
    chunks = [s for s in d.sections if s.parent_section_id == parent.id]
    assert len(chunks) >= 2
    assert all(len(c.plain_text) <= 120 for c in chunks)
    assert all(c.plain_text.split(".")[0].strip().isdigit() for c in chunks)  # split on list items, not mid-clause


def test_thread_split_and_status_rules(ingest_cfg):
    log = """# Log

---

## COMMS-90 — Email

**From:** Ann Lee (Head of Ops)
**Timestamp:** 2025-01-02, 10:00
**Thread relationship:** Follow-up to MTG-90.

> **Ann:** Approved.

---

## COMMS-91 — Slack

**Timestamp:** 2025-01-03, 11:00

> **Bo:** ok
"""
    pf = parse(log, "06_communications/communications_log.md", ingest_cfg)
    ids = [d.id for d in pf.documents]
    assert ids == ["communications-log", "COMMS-90", "COMMS-91"]
    t = pf.documents[1]
    assert t.kind == "thread" and t.doc_type == "communication" and str(t.doc_date) == "2025-01-02"
    assert t.meta["thread_relationship"].startswith("Follow-up")
    sup = parse("# POL-60 — Old [SUPERSEDED]\n\n## Body\n\nOld rule.\n", "02_policies/superseded/POL-60_old_SUPERSEDED.md")
    assert sup.documents[0].status == "superseded"


def test_status_normalisation_reads_leading_statement():
    assert normalise_status({"approval_status": "Approved. (See POL-04 for an unapproved draft revision.)"})[0] == "approved"
    assert normalise_status({"approval_status": "DRAFT — not approved; pilot proposed"})[0] == "draft"
    assert normalise_status({"approval_status": "Approved (historical — no longer in force)"})[0] == "superseded"
    assert normalise_status({"superseded_by": "POL-01"})[0] == "superseded"
    assert normalise_status({}) is None


def test_glob_matching():
    assert path_matches("02_policies/POL-1.md", "**/02_policies/**")
    assert path_matches("a/b/02_policies/x/POL-1.md", "**/02_policies/**")
    assert path_matches("README.md", "**/README.md")
    assert not path_matches("02_policies_old/x.md", "**/02_policies/**")


def test_csv_records_keep_line_spans_and_flag_quality(ingest_cfg):
    csv_text = ("deal_id,customer_name,ae_owner,stage,stage_history,discount_pct,approval_record,approval_role,close_date,notes\n"
                "DEAL-001,Coastline HVAC Group,Ann Lee,Closed Won,Closed Won:2025-02-01,20,Recorded in CRM,Bo Ray,2025-02-01,\n"
                "DEAL-002,Coastline HVAC Grp,,Closed Won,Closed Won:2025-03-01,5,,,2025-03-01,\"multi\nline note\"\n")
    from okc.common.text import sha256

    pf = CsvParser(ingest_cfg).parse("07_crm/crm_deals.csv", csv_text, sha256(csv_text))
    assert [d.id for d in pf.documents] == ["DEAL-001", "DEAL-002"]
    d2 = pf.documents[1]
    assert (d2.start_line, d2.end_line) == (3, 4)  # multi-line quoted cell spans two lines
    assert "discount pct: 5" in d2.sections[0].plain_text
    kinds = {i.issue_type for i in pf.issues}
    assert {"ambiguous_entity", "incomplete_record"} <= kinds


def test_strip_markdown_and_units():
    md = "> **Diane:** Go ahead — [see](x.md) the `field`.\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    plain = strip_markdown(md)
    assert "**" not in plain and "see" in plain and "x.md" not in plain and "1 | 2" in plain and "---" not in plain
    text = "First line.\nSecond line is here."
    units = split_units(text)
    assert [text[s:e] for s, e in units] == ["First line.", "Second line is here."]
