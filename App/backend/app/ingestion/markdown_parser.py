"""Markdown -> documents -> sections, preserving source line/char offsets.

Design notes
* Headings are found with markdown-it (so `#` inside code fences is ignored) and
  sections follow heading structure - policy clauses, process steps and meeting
  decisions are never cut mid-way by fixed-size chunking.
* Sections longer than `max_section_chars` get secondary chunks on paragraph /
  list-item boundaries; each chunk keeps its parent section id and offsets.
* Metadata comes from YAML front matter and/or `- **Key:** value` header lines.
  Every inferred attribute records where it came from.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from markdown_it import MarkdownIt

from app.core.text import parse_first_date, sha256, slugify, strip_markdown
from app.ingestion.metadata import (
    extract_kv_lines,
    extract_people,
    first_rule,
    normalise_keys,
    normalise_status,
    owner_name,
    pick_date,
)
from app.ingestion.parsed import ParsedDocument, ParsedFile, ParsedLink, ParsedSection, ParseIssue

_MD = MarkdownIt("commonmark")
_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")


class IdentifierScheme:
    def __init__(self, cfg: dict[str, Any]):
        ident = cfg.get("identifiers", {})
        self.regex = re.compile(ident.get("pattern", r"\b([A-Z]{2,8})-(\d{2,4})\b"))
        self.prefixes: dict[str, dict[str, Any]] = ident.get("prefixes", {})

    def find_all(self, text: str) -> list[str]:
        seen: list[str] = []
        for m in self.regex.finditer(text):
            ident = m.group(0)
            if ident not in seen:
                seen.append(ident)
        return seen

    def leading(self, text: str) -> str | None:
        """Identifier at the start of a title / filename with a configured prefix."""
        m = self.regex.search(text[:40])
        if m and m.group(1) in self.prefixes and text.find(m.group(0)) <= 2:
            return m.group(0)
        return None

    def prefix_info(self, ident: str) -> dict[str, Any] | None:
        return self.prefixes.get(ident.split("-")[0])


def _line_offsets(content: str) -> list[int]:
    offs, pos = [], 0
    for line in content.split("\n"):
        offs.append(pos)
        pos += len(line) + 1
    offs.append(pos)
    return offs


def _headings(content: str) -> list[tuple[int, int, str]]:
    """(line_index, level, text) for every ATX/setext heading."""
    tokens = _MD.parse(content)
    out = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map:
            inline = tokens[i + 1].content if i + 1 < len(tokens) else ""
            out.append((tok.map[0], int(tok.tag[1]), strip_markdown(inline).strip()))
    return out


def _frontmatter(lines: list[str]) -> tuple[dict[str, Any], int, str | None]:
    """Returns (data, number_of_lines_consumed, error)."""
    if not lines or lines[0].strip() != "---":
        return {}, 0, None
    for j in range(1, min(len(lines), 200)):
        if lines[j].strip() in ("---", "..."):
            try:
                data = yaml.safe_load("\n".join(lines[1:j])) or {}
                if not isinstance(data, dict):
                    return {}, j + 1, "front matter is not a mapping"
                return data, j + 1, None
            except yaml.YAMLError as exc:
                return {}, j + 1, f"invalid YAML front matter: {exc}"
    return {}, 0, "unterminated front matter block"


def _chunk(text: str, max_chars: int) -> list[tuple[int, int]]:
    """Split on blank lines, then list items, never mid-clause when avoidable."""
    if len(text) <= max_chars:
        return [(0, len(text))]
    blocks: list[tuple[int, int]] = []
    pos = 0
    for para in re.split(r"(\n\s*\n|\n(?=\s*(?:\d+\.|[-*+])\s))", text):
        if not para:
            continue
        if para.strip():
            blocks.append((pos, pos + len(para)))
        pos += len(para)
    chunks: list[tuple[int, int]] = []
    cur_s, cur_e = None, None
    for s, e in blocks:
        if cur_s is None:
            cur_s, cur_e = s, e
        elif e - cur_s <= max_chars:
            cur_e = e
        else:
            chunks.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    if cur_s is not None:
        chunks.append((cur_s, cur_e))
    # hard-split any single block that is still too long (rare: giant paragraphs)
    out: list[tuple[int, int]] = []
    for s, e in chunks:
        while e - s > max_chars:
            cut = text.rfind(" ", s, s + max_chars)
            cut = cut if cut > s else s + max_chars
            out.append((s, cut))
            s = cut
        out.append((s, e))
    return out


def _section_role(heading: str, cfg: dict[str, Any]) -> str:
    h = heading.lower()
    for rule in cfg.get("section_roles", []):
        if rule["contains"] in h:
            return rule["role"]
    return cfg.get("default_section_role", "content")


class MarkdownParser:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.ids = IdentifierScheme(cfg)
        self.max_chars = int(cfg.get("max_section_chars", 1800))

    # ------------------------------------------------------------------ public
    def parse(self, rel_path: str, content: str, content_hash: str, encoding: str = "utf-8") -> ParsedFile:
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        pf = ParsedFile(rel_path=rel_path, file_kind="markdown", content=content, content_hash=content_hash, encoding=encoding)
        lines = content.split("\n")
        offsets = _line_offsets(content)

        fm, fm_lines, fm_err = _frontmatter(lines)
        if fm_err:
            pf.issues.append(ParseIssue("warning", "invalid_front_matter", fm_err, rel_path=rel_path))
        # Blank the front matter (keeps line numbers stable for markdown-it).
        body_for_md = "\n".join([""] * fm_lines + lines[fm_lines:])
        headings = [h for h in _headings(body_for_md) if h[0] >= fm_lines]

        split_rule = first_rule(rel_path, self.cfg.get("split_documents", []))
        ranges: list[tuple[int, int, str, tuple[int, int, str] | None]] = []  # (start, end, kind, split heading)
        if split_rule:
            lvl = int(split_rule.get("heading_level", 2))
            split_hs = [h for h in headings if h[1] == lvl and self.ids.leading(h[2])]
            if split_hs:
                ranges.append((fm_lines, split_hs[0][0], "container", None))
                for i, h in enumerate(split_hs):
                    end = split_hs[i + 1][0] if i + 1 < len(split_hs) else len(lines)
                    ranges.append((h[0], end, "thread", h))
        if not ranges:
            ranges.append((fm_lines, len(lines), "file", None))  # front matter is metadata, not a section

        used_ids: set[str] = set()
        for start, end, kind, split_h in ranges:
            doc = self._build_document(
                rel_path, lines, offsets, start, end, kind, split_h, headings, fm if kind != "thread" else {}, fm_lines, pf
            )
            if doc is None:
                continue
            if doc.id in used_ids:  # same identifier twice inside one file
                pf.issues.append(ParseIssue("warning", "ambiguous_identity",
                                            f"Identifier {doc.id} appears more than once in {rel_path}",
                                            rel_path=rel_path, document_id=doc.id))
                doc.id = f"{doc.id}@{doc.start_line}"
                for s in doc.sections:
                    s.id = doc.id + s.id[s.id.index(":"):]
            used_ids.add(doc.id)
            pf.documents.append(doc)
        return pf

    # ---------------------------------------------------------------- internal
    def _build_document(self, rel_path, lines, offsets, start, end, kind, split_h, headings, fm, fm_lines, pf):
        cfg = self.cfg
        doc_lines = lines[start:end]
        raw = "\n".join(doc_lines)
        if not raw.strip():
            return None
        local_heads = [h for h in headings if start <= h[0] < end]
        stem = rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

        # Title + identifier
        if kind == "thread":
            title = split_h[2]
            identifier = self.ids.leading(title)
        else:
            h1 = next((h for h in local_heads if h[1] == 1), None)
            title = h1[2] if h1 else stem.replace("_", " ")
            identifier = self.ids.leading(stem) or self.ids.leading(title)
        if kind == "container":
            identifier = None
        doc_id = identifier or slugify(stem)

        # Metadata: front matter + bold key/value lines in the header region
        header_end = next((h[0] for h in local_heads if h[0] > start), end) if kind != "thread" else end
        kv_raw, _ = extract_kv_lines(lines[start:header_end])
        aliases = cfg.get("metadata_aliases", {})
        meta = normalise_keys({**kv_raw, **(fm or {})}, aliases)

        # doc_type: explicit metadata > folder rule > identifier prefix > default
        if meta.get("type") or meta.get("doc_type"):
            doc_type, dt_src = str(meta.get("doc_type") or meta.get("type")), "metadata"
        else:
            rule = first_rule(rel_path, cfg.get("doc_type_rules", []))
            pinfo = self.ids.prefix_info(identifier) if identifier else None
            if kind == "thread" and pinfo:
                doc_type, dt_src = pinfo["doc_type"], f"identifier_prefix:{identifier.split('-')[0]}"
            elif rule:
                doc_type, dt_src = rule["doc_type"], f"folder_rule:{rule['match']}"
            elif pinfo:
                doc_type, dt_src = pinfo["doc_type"], f"identifier_prefix:{identifier.split('-')[0]}"
            else:
                doc_type, dt_src = cfg.get("default_doc_type", "document"), "default"
        if kind == "container":
            doc_type = f"{doc_type}_index"

        # status: metadata > path rule > unspecified (conflicts are reported)
        status, status_src = "unspecified", "no status information"
        meta_status = normalise_status(meta)
        path_rule = first_rule(rel_path, cfg.get("status_rules", []))
        if meta_status:
            status, status_src = meta_status
        if path_rule:
            if not meta_status or meta_status[0] in ("unspecified",):
                status, status_src = path_rule["status"], f"path_rule: {path_rule['reason']}"
            elif meta_status[0] != path_rule["status"] and not (
                meta_status[0] == "superseded" and path_rule["status"] == "superseded"
            ):
                pf.issues.append(ParseIssue(
                    "warning", "status_conflict",
                    f"Metadata status '{meta_status[0]}' disagrees with path convention '{path_rule['status']}'",
                    rel_path=rel_path, document_id=doc_id,
                    details={"metadata": meta_status[1], "path_rule": path_rule["reason"]}))

        doc_date, date_src = pick_date(meta, cfg.get("date_fields", []))
        people = extract_people(owner_name(meta.get("owner")), meta.get("participants"), owner_name(meta.get("author")),
                                meta.get("recipients"))
        if doc_type == "interview" and not people and ":" in title:
            people = extract_people(title.split(":", 1)[1])

        doc = ParsedDocument(
            id=doc_id, rel_path=rel_path, kind=kind, title=title, identifier=identifier,
            doc_type=doc_type, doc_type_source=dt_src, status=status, status_source=status_src,
            owner=owner_name(meta.get("owner") or meta.get("author")),
            people=people, version_label=(str(meta["version"]) if meta.get("version") else None),
            doc_date=doc_date, date_source=date_src,
            effective_date=parse_first_date(meta.get("effective_date")),
            review_date=parse_first_date(meta.get("review_date")),
            superseded_date=parse_first_date(meta.get("superseded_date")),
            meta={k: (v if isinstance(v, (str, int, float, bool, list, dict)) or v is None else str(v)) for k, v in meta.items()},
            start_line=start + 1, end_line=end, content_hash=sha256(raw),
        )
        doc.sections = self._sections(doc, lines, offsets, start, end, kind, split_h, local_heads)
        for s in doc.sections:
            raw_s = "\n".join(lines[s.start_line - 1 : s.end_line])
            for m in _LINK.finditer(raw_s):
                line_no = s.start_line + raw_s[: m.start()].count("\n")
                doc.links.append(ParsedLink(text=m.group(1), href=m.group(2), section_id=s.id, line=line_no))
        if not doc.sections:
            pf.issues.append(ParseIssue("info", "empty_document", f"{doc_id} has no text content", rel_path=rel_path, document_id=doc_id))
        return doc

    def _sections(self, doc, lines, offsets, start, end, kind, split_h, local_heads) -> list[ParsedSection]:
        out: list[ParsedSection] = []
        used: dict[str, int] = {}

        def new_id(slug: str) -> str:
            n = used.get(slug, 0) + 1
            used[slug] = n
            return f"{doc.id}:{slug}" + (f"-{n}" if n > 1 else "")

        # Section boundaries: [heading_line, next_heading_line)
        if kind == "thread":
            bounds = [(start, end, split_h)]
        else:
            bounds = []
            first_h = local_heads[0][0] if local_heads else end
            if first_h > start and "\n".join(lines[start:first_h]).strip():
                bounds.append((start, first_h, None))
            for i, h in enumerate(local_heads):
                nxt = local_heads[i + 1][0] if i + 1 < len(local_heads) else end
                bounds.append((h[0], nxt, h))

        stack: list[tuple[int, str]] = []
        ordinal = 0
        for b_start, b_end, h in bounds:
            if h is None:
                heading, level, slug = doc.title, 0, "preamble"
            else:
                heading, level = h[2], h[1]
                slug = "thread" if kind == "thread" else ("header" if level == 1 else slugify(heading))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading))
            path = [t for _, t in stack] if h is not None else [doc.title]
            body_start = b_start + 1 if (h is not None and kind != "thread") else b_start
            # Header sections keep the heading line itself out of the body but keep metadata lines.
            body_lines = lines[body_start:b_end]
            # trim trailing blank lines (keeps end_line precise)
            while body_lines and not body_lines[-1].strip():
                body_lines.pop()
            body = "\n".join(body_lines)
            is_header = slug == "header"
            if not body.strip() and not is_header:
                continue
            raw_text = "\n".join(lines[b_start : b_start + (body_start - b_start) + len(body_lines)])
            plain = strip_markdown(raw_text if is_header else body)
            if not plain.strip():
                continue
            sid = new_id(slug)
            s_line = b_start + 1
            e_line = max(s_line, body_start + len(body_lines))
            role = "header" if is_header and kind != "thread" else _section_role(heading, self.cfg)
            if kind == "thread":
                role = "communication"
            sec = ParsedSection(
                id=sid, heading=heading, heading_path=path, level=level, ordinal=ordinal,
                text=raw_text, plain_text=plain, start_line=s_line, end_line=e_line,
                start_char=offsets[b_start], end_char=offsets[e_line] - 1 if e_line < len(offsets) else offsets[-1],
                section_role=role, identifiers=[i for i in self.ids.find_all(plain) if i != doc.id],
                content_hash=sha256(heading + "\n" + raw_text),
            )
            out.append(sec)
            ordinal += 1
            # Secondary chunks for long sections (parent keeps the full text)
            pieces = _chunk(plain, self.max_chars)
            if len(pieces) > 1:
                for k, (cs, ce) in enumerate(pieces, start=1):
                    ctext = plain[cs:ce].strip()
                    out.append(ParsedSection(
                        id=f"{sid}.c{k}", heading=heading, heading_path=path, level=level, ordinal=ordinal,
                        text=ctext, plain_text=ctext, start_line=s_line, end_line=e_line,
                        start_char=sec.start_char, end_char=sec.end_char, section_role=role,
                        parent_section_id=sid, identifiers=[i for i in self.ids.find_all(ctext) if i != doc.id],
                        content_hash=sha256(ctext),
                    ))
                    ordinal += 1
        return out
