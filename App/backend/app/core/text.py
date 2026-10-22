"""Shared text helpers: hashing, slugs, markdown stripping, tokenisation, dates."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date

_WORD_RE = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")

STOPWORDS = frozenset(
    """a an and are as at be been being but by can could did do does doing for from had has have having he her
    his i if in into is it its itself just me my no nor not of off on once only or other our out over own same
    she should so some such than that the their them then there these they this those through to too under
    until up very was we were what when where which while who whom why will with would you your yours
    also about after again all any because before below between both down during each few further here how
    more most once s t don now get got go goes went been within per via etc""".split()
)


def sha256(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def short_hash(text: str, n: int = 16) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def slugify(text: str, max_len: int = 80) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text[:max_len].rstrip("-")) or "untitled"


def snake_case(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def strip_markdown(md: str) -> str:
    """Markdown -> readable plain text. Keeps list numbering and table cells; drops markup only."""
    out_lines = []
    in_fence = False
    for line in md.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            if re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,})\s*", line):
                continue  # horizontal rule
            if re.fullmatch(r"\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*", line):
                continue  # table separator row
            line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
            line = re.sub(r"^\s*>\s?", "", line)
            line = re.sub(r"^(\s*)[-*+]\s+", r"\1", line)
            line = _LINK_RE.sub(r"\1", line)
            line = re.sub(r"(\*\*|__)(.+?)\1", r"\2", line)
            line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", line)
            line = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"\1", line)
            line = line.replace("`", "")
            if line.strip().startswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                line = " | ".join(cells)
        out_lines.append(line.rstrip())
    text = "\n".join(out_lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def stem(token: str) -> str:
    """Tiny suffix-stripping stemmer (deterministic, dependency-free)."""
    for suf in ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ied", "ers", "er", "ed", "es", "s"):
        if token.endswith(suf) and len(token) - len(suf) >= 4:
            base = token[: -len(suf)]
            if suf in ("ies", "ied"):
                base += "y"
            return base
    return token


def content_terms(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text) if t not in STOPWORDS and (len(t) > 2 or t.isdigit())]


def shingles(text: str, k: int = 3) -> set[tuple[str, ...]]:
    toks = tokenize(text)
    return {tuple(toks[i : i + k]) for i in range(max(0, len(toks) - k + 1))}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def parse_first_date(value: str | None) -> date | None:
    if not value:
        return None
    m = _ISO_DATE.search(str(value))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def all_dates(value: str) -> list[date]:
    out = []
    for m in _ISO_DATE.finditer(value):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            continue
    return out


_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[*]*[A-Z0-9])")


def split_units(text: str) -> list[tuple[int, int]]:
    """Split plain text into evidence units (list items / quoted turns / sentences).

    Returns (start, end) character offsets into `text`, so spans stay traceable.
    """
    units: list[tuple[int, int]] = []
    pos = 0
    for line in text.split("\n"):
        start_line = pos
        pos += len(line) + 1
        if not line.strip():
            continue
        # Each line is a candidate; long lines are further split into sentences.
        offset = start_line + (len(line) - len(line.lstrip()))
        body = line.strip()
        if len(body) <= 260:
            units.append((offset, offset + len(body)))
            continue
        last = 0
        for m in _SENT_SPLIT.finditer(body):
            seg = body[last : m.start()].strip()
            if seg:
                s = body.index(seg, last)
                units.append((offset + s, offset + s + len(seg)))
            last = m.end()
        seg = body[last:].strip()
        if seg:
            s = body.index(seg, last)
            units.append((offset + s, offset + s + len(seg)))
    return units
