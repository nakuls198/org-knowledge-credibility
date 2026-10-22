"""Actor recognition: people, roles and business functions.

Built from the corpus itself (roster tables) plus `config/entities.yaml` aliases,
so nothing about Northfield's org chart is hard-coded.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel

ActorKind = Literal["person", "role", "function", "generic"]


class Actor(BaseModel):
    mention: str
    kind: ActorKind
    person: str | None = None
    role: str | None = None
    function: str | None = None
    start: int = 0
    end: int = 0

    def key(self) -> str:
        return self.person or self.role or self.function or self.mention.lower()


class Gazetteer:
    def __init__(self, people: dict[str, dict[str, str]], cfg: dict[str, Any]):
        """people: name -> {"role": ..., "function": ...}"""
        self.people = people
        self.cfg = cfg
        self.role_function: dict[str, str] = {}
        for p in people.values():
            if p.get("role"):
                self.role_function.setdefault(p["role"], p.get("function", ""))
        self.role_people: dict[str, list[str]] = {}
        for name, p in people.items():
            if p.get("role"):
                self.role_people.setdefault(p["role"], []).append(name)
        first_counts: dict[str, int] = {}
        for name in people:
            first_counts[name.split()[0]] = first_counts.get(name.split()[0], 0) + 1
        self.first_names = {name.split()[0]: name for name in people if first_counts[name.split()[0]] == 1}

        # (pattern, factory) ordered longest-first so "Head of Finance & People Ops" beats "Finance"
        entries: list[tuple[str, dict[str, Any], bool]] = []  # (surface, actor fields, case_sensitive)
        for name, p in people.items():
            entries.append((name, {"kind": "person", "person": name, "role": p.get("role"), "function": p.get("function")}, True))
        for first, name in self.first_names.items():
            p = people[name]
            entries.append((first, {"kind": "person", "person": name, "role": p.get("role"), "function": p.get("function")}, True))
        for role, fn in self.role_function.items():
            entries.append((role, {"kind": "role", "role": role, "function": fn}, False))
            for alias in (cfg.get("role_aliases", {}) or {}).get(role, []):
                entries.append((alias, {"kind": "role", "role": role, "function": fn}, False))
        for role, aliases in (cfg.get("role_aliases", {}) or {}).items():
            if role not in self.role_function:
                for alias in [role, *aliases]:
                    entries.append((alias, {"kind": "role", "role": role, "function": None}, False))
        for fn, aliases in (cfg.get("function_aliases", {}) or {}).items():
            entries.append((fn, {"kind": "function", "function": fn}, True))
            for alias in aliases:
                entries.append((alias[:1].upper() + alias[1:], {"kind": "function", "function": fn}, True))
        for word, spec in (cfg.get("generic_roles", {}) or {}).items():
            fields = {"kind": "generic", "function": spec.get("function")}
            entries.append((word, fields, False))
        entries.sort(key=lambda e: -len(e[0]))
        self._entries = [(re.compile(r"(?<![\w&])" + re.escape(s) + r"s?(?![\w&])", 0 if cs else re.I), f)
                         for s, f, cs in entries if s.strip()]

    # ------------------------------------------------------------------ build
    @classmethod
    def from_sections(cls, sections: Iterable[tuple[str, str]], cfg: dict[str, Any]) -> "Gazetteer":
        """sections: (plain_text, doc_type) pairs. Parses roster-style tables (`a | b | c` rows)."""
        rt = cfg.get("roster_table", {})
        name_cols = set(rt.get("name_columns", ["employee", "name"]))
        role_cols = set(rt.get("role_columns", ["role"]))
        fn_cols = set(rt.get("function_columns", ["function"]))
        people: dict[str, dict[str, str]] = {}
        for text, _doc_type in sections:
            header: list[str] | None = None
            for line in text.split("\n"):
                if " | " not in line:
                    header = None
                    continue
                cells = [c.strip() for c in line.split(" | ")]
                low = [c.lower() for c in cells]
                if header is None:
                    if any(c in name_cols for c in low) and any(c in role_cols for c in low):
                        header = low
                    continue
                if len(cells) != len(header):
                    continue
                row = dict(zip(header, cells))
                name = next((row[c] for c in header if c in name_cols), "")
                role = next((row[c] for c in header if c in role_cols), "")
                fn = next((row[c] for c in header if c in fn_cols), "")
                if re.match(r"^[A-Z][\w'\-]+ [A-Z][\w'\-]+", name):
                    people.setdefault(name, {"role": role, "function": fn})
        return cls(people, cfg)

    # ------------------------------------------------------------------- find
    def find(self, text: str) -> list[Actor]:
        taken: list[tuple[int, int]] = []
        out: list[Actor] = []
        for pat, fields in self._entries:
            for m in pat.finditer(text):
                s, e = m.span()
                if any(s < te and e > ts for ts, te in taken):
                    continue
                taken.append((s, e))
                out.append(Actor(mention=m.group(0), start=s, end=e, **fields))
        out.sort(key=lambda a: a.start)
        return out

    def speaker_of(self, line: str) -> Actor | None:
        """'Diane: go ahead...' / 'Lior (10:52): ...' -> the speaker of a chat/email turn."""
        m = re.match(r"^\s*([A-Z][a-z]+(?: [A-Z][a-z]+)?)(?: \([^)]*\))?:\s", line)
        if not m:
            return None
        token = m.group(1)
        name = token if token in self.people else self.first_names.get(token.split()[0])
        if not name:
            return None
        p = self.people[name]
        return Actor(mention=token, kind="person", person=name, role=p.get("role"), function=p.get("function"),
                     start=m.start(1), end=m.end(1))

    # ---------------------------------------------------------- compatibility
    def relation(self, claim: Actor, other: Actor) -> Literal["match", "compatible", "conflict", "unrelated"]:
        """How a claimed actor relates to an actor found in evidence."""
        if claim.kind == "generic":
            spec = (self.cfg.get("generic_roles", {}) or {}).get(claim.mention.lower().rstrip("s"), {})
            if spec.get("function"):
                return "compatible" if other.function == spec["function"] else "conflict"
            words = spec.get("matches", [])
            title = (other.role or "").lower()
            return "compatible" if any(w in title for w in words) else "unrelated"
        if claim.kind == "person":
            if other.person:
                return "match" if other.person == claim.person else "conflict"
            if other.role and claim.role:
                return "match" if other.role == claim.role else "conflict"
            return "unrelated"
        if claim.kind == "role":
            if other.role:
                return "match" if other.role == claim.role else "conflict"
            if other.kind == "function" and claim.function and other.function == claim.function:
                return "compatible"
            return "unrelated"
        if claim.kind == "function":
            if other.function:
                return "match" if other.function == claim.function else "conflict"
            return "unrelated"
        return "unrelated"
