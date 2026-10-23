"""CSV -> one record document per row (CRM deals, tasks, audit events).

Each row keeps its exact source line span so evidence can be traced back to the file.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from okc.common.text import all_dates, parse_first_date, sha256, slugify
from okc.corpus.identifiers import IdentifierScheme
from okc.corpus.metadata import extract_people, first_rule
from okc.corpus.parsed import ParsedDocument, ParsedFile, ParsedSection, ParseIssue

_ABBREVIATIONS = {"grp": "group", "co": "company", "svcs": "services", "svc": "service", "intl": "international",
                  "ltd": "", "pty": "", "inc": "", "&": "and"}


def _entity_key(value: str) -> str:
    """Normalised company-name key, so 'Coastline HVAC Group' and 'Coastline HVAC Grp' collide."""
    tokens = re.findall(r"[a-z0-9&]+", value.lower())
    return " ".join(t for t in (_ABBREVIATIONS.get(t, t) for t in tokens) if t)


class _Fmt(dict):
    def __missing__(self, key):  # tolerate template fields a row does not have
        return ""


def render_row(row: dict[str, str]) -> str:
    """Readable `field: value` rendering (what the search index and classifiers see)."""
    lines = []
    for k, v in row.items():
        if k is None or v is None or str(v).strip() == "":
            continue
        lines.append(f"{k.replace('_', ' ')}: {str(v).strip()}")
    return "\n".join(lines)


def _row_date(rule: dict[str, Any], row: dict[str, str]) -> tuple[Any, str | None]:
    """First usable date among `date_columns`; `column:last` means the latest date inside that field."""
    for spec in rule.get("date_columns", []):
        column, _, which = spec.partition(":")
        if which == "last":
            d = (all_dates(row.get(column) or "") or [None])[-1]
        else:
            d = parse_first_date(row.get(column))
        if d:
            return d, spec
    return None, None


class CsvParser:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.ids = IdentifierScheme(cfg)

    def parse(self, rel_path: str, content: str, content_hash: str, encoding: str = "utf-8") -> ParsedFile:
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        pf = ParsedFile(rel_path=rel_path, file_kind="csv", content=content, content_hash=content_hash,
                        encoding=encoding)
        rule = first_rule(rel_path, self.cfg.get("csv_sources", []))
        if rule is None:
            pf.issues.append(ParseIssue("info", "unconfigured_csv", "CSV file has no csv_sources rule; skipped",
                                        rel_path=rel_path))
            return pf
        type_rule = first_rule(rel_path, self.cfg.get("doc_type_rules", []))
        id_col = rule["id_column"]

        offsets = [0]
        for line in content.split("\n"):
            offsets.append(offsets[-1] + len(line) + 1)

        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames or id_col not in reader.fieldnames:
            pf.issues.append(ParseIssue("error", "missing_id_column", f"Column '{id_col}' not found", rel_path=rel_path))
            return pf

        previous_line = reader.line_num  # the header
        seen: dict[str, int] = {}
        entities: dict[str, dict[str, list[str]]] = {c: {} for c in rule.get("entity_columns", [])}

        for raw_row in reader:
            start_line, end_line = previous_line + 1, reader.line_num
            previous_line = reader.line_num
            row = {k: (v or "").strip() for k, v in raw_row.items() if k is not None}

            rid = row.get(id_col) or f"{slugify(rel_path)}-row{start_line}"
            if rid in seen:
                pf.issues.append(ParseIssue("warning", "ambiguous_identity",
                                            f"Duplicate record id {rid} (lines {seen[rid]} and {start_line})",
                                            rel_path=rel_path, document_id=rid))
                rid = f"{rid}@{start_line}"
            seen[rid] = start_line

            doc = self._record_document(rel_path, rid, row, rule, type_rule, id_col, start_line, end_line, offsets)
            missing = [c for c in rule.get("required_columns", []) if not row.get(c)]
            if missing:
                pf.issues.append(ParseIssue("info", "incomplete_record", f"{rid} is missing {', '.join(missing)}",
                                            rel_path=rel_path, document_id=rid, details={"missing": missing}))
                doc.flags.append("incomplete_record")
            if doc.doc_date is None:
                doc.flags.append("undated")
            for column, groups in entities.items():
                if row.get(column):
                    groups.setdefault(_entity_key(row[column]), []).append(f"{rid}={row[column]}")
            pf.documents.append(doc)

        self._report_ambiguous_entities(pf, rel_path, entities)
        return pf

    def _record_document(self, rel_path, rid, row, rule, type_rule, id_col, start_line, end_line, offsets) -> ParsedDocument:
        text = render_row(row)
        doc_date, date_source = _row_date(rule, row)
        people = extract_people(*[row.get(c) for c in rule.get("people_columns", [])])
        title = rule.get("title_template", "{" + id_col + "}").format_map(_Fmt(row))
        section = ParsedSection(
            id=f"{rid}:record", heading=title, heading_path=[title], level=0, ordinal=0,
            text=text, plain_text=text, start_line=start_line, end_line=end_line,
            start_char=offsets[start_line - 1], end_char=offsets[end_line] - 1,
            section_role="record", identifiers=[i for i in self.ids.find_all(text) if i != rid],
            content_hash=sha256(text),
        )
        return ParsedDocument(
            id=rid, rel_path=rel_path, kind="record", title=title, identifier=row.get(id_col) or None,
            doc_type=type_rule["doc_type"] if type_rule else "record",
            doc_type_source=f"folder_rule:{type_rule['match']}" if type_rule else "csv_rule",
            status="record", status_source="structured system record",
            owner=people[0] if people else None, people=people, version_label=None,
            doc_date=doc_date, date_source=date_source, effective_date=None, review_date=None, superseded_date=None,
            meta={**row, "_csv_reference_columns": rule.get("reference_columns", [])},
            start_line=start_line, end_line=end_line, content_hash=sha256(text), sections=[section],
        )

    @staticmethod
    def _report_ambiguous_entities(pf: ParsedFile, rel_path: str, entities: dict[str, dict[str, list[str]]]) -> None:
        for column, groups in entities.items():
            for key, members in groups.items():
                distinct = {m.split("=", 1)[1] for m in members}
                if len(distinct) > 1:
                    pf.issues.append(ParseIssue(
                        "warning", "ambiguous_entity",
                        f"Possibly the same {column.replace('_', ' ')} written differently: {sorted(distinct)}",
                        rel_path=rel_path, details={"column": column, "records": members, "normalised": key}))
