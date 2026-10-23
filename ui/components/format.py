"""Small display formatters shared by the components."""
from __future__ import annotations


def f5(x) -> str:
    """A score or weight to five decimals ('—' when missing)."""
    try:
        return f"{float(x):.5f}"
    except (TypeError, ValueError):
        return "—"


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def tag(text: str, background: str, colour: str) -> str:
    return f"<span class='tag' style='background:{background};color:{colour}'>{text}</span>"


SUPERSEDED_TAG = tag("Superseded", "#3A1D1D", "#FCA5A5")
DRAFT_TAG = tag("Draft", "#3A2E12", "#FCD34D")
COPY_TAG = tag("Copy · shared weight", "#1E293B", "#93C5FD")
