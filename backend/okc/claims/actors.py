"""Actor recognition: people, roles and business functions.

Built from the corpus itself (roster tables) plus `config/entities.yaml` aliases, so nothing about
the organisation chart is hard-coded.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel

ActorKind = Literal["person", "role", "function", "generic"]
_FULL_NAME = re.compile(r"^[A-Z][\w'\-]+ [A-Z][\w'\-]+")


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
        self.role_people: dict[str, list[str]] = {}
        for name, p in people.items():
            if p.get("role"):
                self.role_function.setdefault(p["role"], p.get("function", ""))
                self.role_people.setdefault(p["role"], []).append(name)
        first_counts: dict[str, int] = {}
        for name in people:
            first_counts[name.split()[0]] = first_counts.get(name.split()[0], 0) + 1
        self.first_names = {name.split()[0]: name for name in people if first_counts[name.split()[0]] == 1}
        # Capitalised words that are not names of tools or systems (months, department words, sentence words).
        self.non_tool_names: set[str] = set(cfg.get("non_tool_names", []))
        self._entries = self._build_patterns()

    def _build_patterns(self) -> list[tuple[re.Pattern[str], dict[str, Any]]]:
        """(pattern, actor fields), longest surface form first so 'Head of Finance & People Ops' beats 'Finance'."""
        cfg = self.cfg
        entries: list[tuple[str, dict[str, Any], bool]] = []  # (surface form, actor fields, case-sensitive)

        def person_fields(name: str) -> dict[str, Any]:
            p = self.people[name]
            return {"kind": "person", "person": name, "role": p.get("role"), "function": p.get("function")}

        for name in self.people:
            entries.append((name, person_fields(name), True))
        for first, name in self.first_names.items():
            entries.append((first, person_fields(name), True))
        role_aliases = cfg.get("role_aliases", {}) or {}
        for role, fn in self.role_function.items():
            entries.append((role, {"kind": "role", "role": role, "function": fn}, False))
            for alias in role_aliases.get(role, []):
                entries.append((alias, {"kind": "role", "role": role, "function": fn}, False))
        for role, aliases in role_aliases.items():
            if role not in self.role_function:
                for alias in [role, *aliases]:
                    entries.append((alias, {"kind": "role", "role": role, "function": None}, False))
        for fn, aliases in (cfg.get("function_aliases", {}) or {}).items():
            entries.append((fn, {"kind": "function", "function": fn}, True))
            for alias in aliases:
                entries.append((alias[:1].upper() + alias[1:], {"kind": "function", "function": fn}, True))
        for word, spec in (cfg.get("generic_roles", {}) or {}).items():
            entries.append((word, {"kind": "generic", "function": spec.get("function")}, False))
        entries.sort(key=lambda e: -len(e[0]))
        return [(re.compile(r"(?<![\w&])" + re.escape(s) + r"s?(?![\w&])", 0 if cs else re.I), f)
                for s, f, cs in entries if s.strip()]

    # ------------------------------------------------------------------ build
    @classmethod
    def from_sections(cls, sections: Iterable[tuple[str, str]], cfg: dict[str, Any]) -> "Gazetteer":
        """sections: (plain_text, doc_type) pairs. Parses roster-style tables (`a | b | c` rows)."""
        table = cfg.get("roster_table", {})
        name_cols = set(table.get("name_columns", ["employee", "name"]))
        role_cols = set(table.get("role_columns", ["role"]))
        fn_cols = set(table.get("function_columns", ["function"]))
        people: dict[str, dict[str, str]] = {}
        for text, _doc_type in sections:
            header: list[str] | None = None
            for line in text.split("\n"):
                if " | " not in line:
                    header = None
                    continue
                cells = [c.strip() for c in line.split(" | ")]
                lowered = [c.lower() for c in cells]
                if header is None:
                    if any(c in name_cols for c in lowered) and any(c in role_cols for c in lowered):
                        header = lowered
                    continue
                if len(cells) != len(header):
                    continue
                row = dict(zip(header, cells))
                name = next((row[c] for c in header if c in name_cols), "")
                role = next((row[c] for c in header if c in role_cols), "")
                function = next((row[c] for c in header if c in fn_cols), "")
                if _FULL_NAME.match(name):
                    people.setdefault(name, {"role": role, "function": function})
        return cls(people, cfg)

    # ------------------------------------------------------------------- find
    def find(self, text: str) -> list[Actor]:
        taken: list[tuple[int, int]] = []
        out: list[Actor] = []
        for pattern, fields in self._entries:
            for m in pattern.finditer(text):
                s, e = m.span()
                if any(s < te and e > ts for ts, te in taken):
                    continue
                taken.append((s, e))
                out.append(Actor(mention=m.group(0), start=s, end=e, **fields))
        out.sort(key=lambda a: a.start)
        return out

    def speaker_of(self, line: str) -> Actor | None:
        """'Diane: go ahead...' / 'Lior (10:52): ...' -> the speaker of a chat or email turn."""
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

    def lookup(self, name: str) -> tuple[str, str | None]:
        """Resolve a full name or a unique first name to (full name, role); role is None if unknown."""
        name = (name or "").strip()
        if name in self.people:
            return name, self.people[name].get("role")
        full = self.first_names.get(name.split()[0]) if name else None
        if full and (" " not in name or full.split()[-1] == name.split()[-1]):
            return full, self.people[full].get("role")
        return name, None

    # ---------------------------------------------------------- compatibility
    def relation(self, claim: Actor, other: Actor) -> Literal["match", "compatible", "conflict", "unrelated"]:
        """How an actor named in a claim relates to an actor found in evidence."""
        if claim.kind == "generic":
            spec = (self.cfg.get("generic_roles", {}) or {}).get(claim.mention.lower().rstrip("s"), {})
            if spec.get("function"):
                return "compatible" if other.function == spec["function"] else "conflict"
            title = (other.role or "").lower()
            return "compatible" if any(w in title for w in spec.get("matches", [])) else "unrelated"
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
