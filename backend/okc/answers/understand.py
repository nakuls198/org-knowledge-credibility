"""Understand a question: which record it is about, whether it asks for a scan, or which claim to check.

No language model is used: on modest hardware a local model takes minutes per question and can add
details that were not in the question. Instead
  * record ids (DEAL-027, "deal 27", TASK-095, AUD-0070) are recognised directly;
  * "which deals broke the discount policy?" style questions become a scan of all such records;
  * yes/no questions are turned into the statement they ask about by simple grammar rules;
  * who/what/how questions are matched to the statement in the policies, procedures or meeting
    decisions that best answers them, so every claim checked is one the files actually make.

Record naming and the organisation's own vocabulary come from `config/answers.yaml`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

from okc.settings import load_config

_ID = re.compile(r"\b([A-Z]{2,6})[-\s]?#?(\d{2,4})\b", re.I)
_SCAN_ASK = re.compile(r"\b(which|what|list|any|how many|show|find|all)\b", re.I)
_SCAN_COMPLIANCE = re.compile(r"(violat|breach|break|broke|non-?complian|against (the )?(policy|rules?)|"
                              r"follow(ed|s)? (the )?(policy|rules?)|without (proper )?approval|compl(y|ied|iant)|"
                              r"(policy|rule) (problems?|issues?|breaches?)|unapproved|not approved)", re.I)
_DEFAULT_WIDTH = 3

_AUX = {"is", "are", "was", "were", "do", "does", "did", "can", "could", "must", "should", "will", "would",
        "may", "might", "has", "have", "had", "shall"}
_DROP_AUX = {"do", "does", "did"}
_DETS = {"the", "a", "an", "all", "every", "any", "our", "my", "their", "each", "some", "most", "new"}
_SPLIT_BEFORE = {"really", "actually", "still", "always", "never", "only", "currently", "now", "formally",
                 "usually", "normally", "typically", "in", "on", "at", "by", "within", "over", "under", "above",
                 "below", "more", "less", "tracked", "required", "approved", "allowed", "needed", "done", "a", "an"}
_STOP = {"the", "a", "an", "of", "to", "in", "on", "at", "by", "for", "and", "or", "is", "are", "was", "were", "do",
         "does", "did", "who", "what", "which", "when", "where", "why", "how", "it", "that", "this", "be", "really",
         "actually", "company", "we", "our", "us", "i", "my", "can", "should", "must", "will", "get"}
_WH = {"who", "whom", "whose", "what", "which", "when", "where", "why", "how"}


class _Vocabulary:
    """Record-reference and scan-subject patterns built from the answers config."""

    def __init__(self, cfg: dict[str, Any]):
        records = cfg.get("record_references", {})
        self.width = {prefix: int(spec.get("width", _DEFAULT_WIDTH)) for prefix, spec in records.items()}
        self.word_prefix = {w.split()[0].lower(): prefix for prefix, spec in records.items() for w in spec.get("words", [])}
        words = "|".join(re.escape(w) for spec in records.values() for w in spec.get("words", []))
        self.word_ref = re.compile(rf"\b({words})\s*(?:no\.?|number|#|id)?\s*-?\s*(\d{{1,4}})\b", re.I) if words else None
        self.scan_subjects = {name: re.compile(spec["pattern"], re.I) for name, spec in cfg.get("scan_subjects", {}).items()}
        self.stop = _STOP | {w.lower() for w in cfg.get("organisation_words", [])}


@lru_cache
def _vocabulary() -> _Vocabulary:
    return _Vocabulary(load_config("answers"))


# ---------------------------------------------------------------- record references

def record_refs(text: str, exists: Callable[[str], bool]) -> list[str]:
    """Record ids mentioned in the text that exist in the corpus, in order of mention."""
    vocab = _vocabulary()
    found: list[str] = []
    for m in _ID.finditer(text):
        prefix, num = m.group(1).upper(), m.group(2)
        for candidate in (f"{prefix}-{num}", f"{prefix}-{num.zfill(vocab.width.get(prefix, len(num)))}"):
            if candidate not in found and exists(candidate):
                found.append(candidate)
                break
    for m in (vocab.word_ref.finditer(text) if vocab.word_ref else []):
        word = m.group(1).lower().split()[0]
        prefix = vocab.word_prefix.get(word, word.upper())
        candidate = f"{prefix}-{m.group(2).zfill(vocab.width.get(prefix, _DEFAULT_WIDTH))}"
        if candidate not in found and exists(candidate):
            found.append(candidate)
    return found


# ---------------------------------------------------------------- scan questions

def scan_subject(text: str) -> str | None:
    """A configured subject ('deals', 'expenses') when the question asks which records of that kind break a policy."""
    if not (_SCAN_ASK.search(text) and _SCAN_COMPLIANCE.search(text)):
        return None
    for subject, pattern in _vocabulary().scan_subjects.items():
        if pattern.search(text):
            return subject
    return None


# ---------------------------------------------------------------- questions -> claims

@dataclass
class Rephrased:
    claim: str | None
    note: str
    source: dict | None = None  # the candidate statement used, when matched from the files
    fallback: str | None = None  # a statement to check if no passage answers a wh-question either


def _subject_end(words: list[str]) -> int:
    """Where a question's subject ends (heuristic: determiner + noun, or a run of Capitalised words)."""
    i = 1 if words and words[0].lower() in _DETS else 0
    if i < len(words):
        i += 1
    while i < len(words) and (words[i][:1].isupper() or words[i].lower() in {"of", "and", "&"}):
        i += 1
    return min(i, len(words))


def yes_no_to_statement(words: list[str]) -> str:
    """['Is', 'the', 'cap', '10', 'percent'] -> 'The cap is 10 percent.'"""
    aux, rest = words[0].lower(), words[1:]
    if aux in _DROP_AUX:
        out = rest
    elif aux in {"is", "are", "was", "were"}:
        cut = next((i for i, w in enumerate(rest) if i >= 1 and (
            w.lower() in _SPLIT_BEFORE or (w[:1].isdigit() and not rest[i - 1][:1].isupper())
            or (w.lower().endswith("ed") and len(w) > 4))), None)
        cut = cut if cut is not None else _subject_end(rest)
        out = rest[:cut] + [aux] + rest[cut:]
    else:
        cut = _subject_end(rest)
        out = rest[:cut] + [aux] + rest[cut:]
    s = " ".join(out).strip()
    return (s[:1].upper() + s[1:]).rstrip(".") + "."


def stems(text: str) -> set[str]:
    """Crude word stems: lower-case, plural 's' dropped, first five letters ('approvals' -> 'appro')."""
    stop = _vocabulary().stop
    words = re.findall(r"[a-z0-9%$]+", text.lower().replace("'s", ""))
    return {(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)[:5]
            for w in words if w not in stop and len(w) > 1}


def rephrase(text: str, best_statement: Callable[[str], dict | None]) -> Rephrased:
    """Turn a message into the claim to check. best_statement(question) finds the files' own answer."""
    text = re.sub(r"\s+", " ", text.strip())
    words = re.sub(r"[?!.]+$", "", text).split()
    if len(words) < 3:
        return Rephrased(None, "Write at least a few words about who does what.")
    first = words[0].lower()
    if first in _AUX and not text.lower().startswith(("is there", "are there")):
        return Rephrased(yes_no_to_statement(words), "Your question was turned into the statement it asks about.")
    if re.match(r"(where|why|what happened|what's happening|what is happening|how did|how come)\b", text, re.I):
        # rarely answered by a single rule sentence: answer with the passages that explain it
        return Rephrased(None, "Answered with the passages that explain it.")
    if first in _WH or text.endswith("?"):
        statement = best_statement(text)
        if statement is not None:
            return Rephrased(statement["text"], f"Answered with the statement in {statement['document_id']} "
                                                f"({statement['section_heading']}) that matches your question.", statement)
        fallback = (yes_no_to_statement(words[1:]) if first in _WH and len(words) > 3 and words[1].lower() in _AUX
                    else None)
        return Rephrased(None, "Nothing in the policies, procedures or meeting decisions answers that. "
                               "Try asking whether a specific rule or practice is true.", fallback=fallback)
    return Rephrased(text.rstrip(".") + ".", "Checked as written.")
