"""Metadata extraction and normalisation (config-driven)."""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from typing import Any

from okc.common.text import parse_first_date, snake_case

# `- **Key:** value`, `**Key:** value`, `- **Key**: value`
_BULLET_KV = re.compile(r"^\s*(?:[-*+]\s+)?\*\*(?P<key>[^*]{1,60}?):?\*\*:?\s*(?P<value>.*?)\s*$")
_PAREN = re.compile(r"\([^)]*\)")
_NAME = re.compile(r"^[A-Z][a-zA-Z'\-]+(?: [A-Z][a-zA-Z'\-]+){1,2}$")


@lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Glob -> regex. `**/` matches zero or more directories, `*` stays within one segment."""
    i, out = 0, []
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def path_matches(rel_path: str, pattern: str) -> bool:
    """Case-sensitive glob match on a forward-slash relative path."""
    return bool(_glob_regex(pattern).match(rel_path.replace("\\", "/")))


def first_rule(rel_path: str, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first config rule whose `match` glob fits the path (rules are ordered)."""
    return next((rule for rule in rules if path_matches(rel_path, rule["match"])), None)


def extract_kv_lines(lines: list[str]) -> dict[str, str]:
    """Bold key/value metadata lines -> {snake_case_key: value} (first occurrence wins)."""
    out: dict[str, str] = {}
    for line in lines:
        m = _BULLET_KV.match(line)
        if not m or not m.group("value"):
            continue
        key = snake_case(m.group("key"))
        if key and len(key) <= 40:
            out.setdefault(key, m.group("value").strip())
    return out


def normalise_keys(raw: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in raw.items():
        key = aliases.get(snake_case(str(k)), snake_case(str(k)))
        out.setdefault(key, v)
    return out


def normalise_status(meta: dict[str, Any]) -> tuple[str, str] | None:
    """Map free-text approval status to approved|draft|superseded. None when there is no evidence."""
    if meta.get("superseded_by"):
        return "superseded", "metadata: superseded_by"
    raw = str(meta.get("approval_status") or meta.get("status") or "").lower()
    if not raw:
        return None
    src = f"metadata: approval_status='{meta.get('approval_status') or meta.get('status')}'"
    if "superseded" in raw or "historical" in raw or "no longer in force" in raw:
        return "superseded", src
    # Judge by the leading statement: "Approved. (See POL-04 for an unapproved draft revision.)" is approved.
    lead = re.split(r"[.;(—–]| - ", raw, maxsplit=1)[0].strip()
    if any(w in lead for w in ("draft", "not approved", "pending", "proposed")):
        return "draft", src
    if any(w in lead for w in ("approved", "in force", "current", "active")):
        return "approved", src
    if "draft" in raw or "not approved" in raw:
        return "draft", src
    return "unspecified", f"metadata: unrecognised approval_status '{raw}'"


def extract_people(*values: str | None) -> list[str]:
    """Pull person names out of owner / participants / from-to strings."""
    people: list[str] = []
    for value in values:
        if not value:
            continue
        cleaned = _PAREN.sub("", str(value))
        for part in re.split(r"[,;→]|\band\b|->|\bto\b", cleaned):
            name = re.sub(r"\s*\(.*$", "", part.strip().strip(".").strip())
            if _NAME.match(name) and name not in people:
                people.append(name)
    return people


def owner_name(value: str | None) -> str | None:
    """'Harriet Doyle, Head of Finance' -> 'Harriet Doyle'; 'Diane Okafor (Head of Sales)' -> 'Diane Okafor'."""
    if not value:
        return None
    first = _PAREN.sub("", str(value)).split(",")[0].strip()
    return first if _NAME.match(first) else str(value).strip()


def pick_date(meta: dict[str, Any], fields: list[str]) -> tuple[date | None, str | None]:
    """The first date found in the listed metadata fields, and which field it came from."""
    for f in fields:
        d = parse_first_date(meta.get(f))
        if d:
            return d, f
    return None, None
