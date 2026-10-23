"""Shared text helpers: hashing, slugs, markdown stripping, tokenisation, identifiers, dates."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date

# Shared document / record identifiers such as POL-01, COMMS-03, DEAL-014. The ingestion config
# (`identifiers.pattern`) may override this; everything else uses this default.
IDENTIFIER_PATTERN = r"\b([A-Z]{2,8})-(\d{2,4})\b"
IDENTIFIER_RE = re.compile(IDENTIFIER_PATTERN)

_WORD_RE = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")

STOPWORDS = frozenset(
    """a an and are as at be been being but by can could did do does doing for from had has have having he her
    his i if in into is it its itself just me my no nor not of off on once only or other our out over own same
    she should so some such than that the their them then there these they this those through to too under
    until up very was we were what when where which while who whom why will with would you your yours
    also about after again all any because before below between both down during each few further here how
    more most once s t don now get got go goes went been within per via etc""".split()
)

_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[*]*[A-Z0-9])")
_MAX_UNIT_CHARS = 260


# ---------------------------------------------------------------- hashing and ids

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


def find_identifiers(text: str, regex: re.Pattern[str] = IDENTIFIER_RE) -> list[str]:
    """Distinct identifiers in order of first mention."""
    seen: list[str] = []
    for m in regex.finditer(text):
        if m.group(0) not in seen:
            seen.append(m.group(0))
    return seen


# ---------------------------------------------------------------- markdown

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


# ---------------------------------------------------------------- tokens and similarity

def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def stem(token: str) -> str:
    """Tiny suffix-stripping stemmer (deterministic, dependency-free)."""
    for suffix in ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ied", "ers", "er", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            base = token[: -len(suffix)]
            if suffix in ("ies", "ied"):
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


# ---------------------------------------------------------------- dates

def _to_date(m: re.Match[str]) -> date | None:
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_first_date(value: str | None) -> date | None:
    if not value:
        return None
    m = _ISO_DATE.search(str(value))
    return _to_date(m) if m else None


def all_dates(value: str) -> list[date]:
    return [d for d in (_to_date(m) for m in _ISO_DATE.finditer(value)) if d is not None]


# ---------------------------------------------------------------- evidence units

def split_units(text: str) -> list[tuple[int, int]]:
    """Split plain text into evidence units (list items / quoted turns / sentences).

    Returns (start, end) character offsets into `text`, so spans stay traceable.
    """
    units: list[tuple[int, int]] = []
    pos = 0
    for line in text.split("\n"):
        line_start = pos
        pos += len(line) + 1
        if not line.strip():
            continue
        offset = line_start + (len(line) - len(line.lstrip()))
        body = line.strip()
        if len(body) <= _MAX_UNIT_CHARS:
            units.append((offset, offset + len(body)))
            continue
        # A long line is split into sentences.
        last = 0
        for m in _SENT_SPLIT.finditer(body):
            _add_unit(units, body, offset, last, m.start())
            last = m.end()
        _add_unit(units, body, offset, last, len(body))
    return units


def _add_unit(units: list[tuple[int, int]], body: str, offset: int, lo: int, hi: int) -> None:
    seg = body[lo:hi].strip()
    if seg:
        start = body.index(seg, lo)
        units.append((offset + start, offset + start + len(seg)))
