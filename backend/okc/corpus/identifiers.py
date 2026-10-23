"""Shared identifiers (POL-01, COMMS-03, DEAL-014 ...) as configured in `config/ingestion.yaml`."""

from __future__ import annotations

import re
from typing import Any

from okc.common.text import IDENTIFIER_PATTERN, find_identifiers


class IdentifierScheme:
    def __init__(self, cfg: dict[str, Any]):
        ident = cfg.get("identifiers", {})
        self.regex = re.compile(ident.get("pattern", IDENTIFIER_PATTERN))
        self.prefixes: dict[str, dict[str, Any]] = ident.get("prefixes", {})

    def find_all(self, text: str) -> list[str]:
        return find_identifiers(text, self.regex)

    def leading(self, text: str) -> str | None:
        """Identifier at the start of a title / filename with a configured prefix."""
        m = self.regex.search(text[:40])
        if m and m.group(1) in self.prefixes and text.find(m.group(0)) <= 2:
            return m.group(0)
        return None

    def prefix_info(self, ident: str) -> dict[str, Any] | None:
        return self.prefixes.get(ident.split("-")[0])
