"""Top bar: brand, live status of the evidence check, and a read-only status popover."""
from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

import client

_GREEN, _AMBER = "#22C55E", "#F59E0B"


def status_pill(h: dict[str, Any]) -> str:
    if h["checks_ready"]:
        colour, text = _GREEN, f"Model: {escape(h['judge'])}"
    else:
        colour, text = _AMBER, "Rules only (NLI model off)"
    return f"<span class='pill'><span class='dot' style='background:{colour}'></span>{text}</span>"


def status_popover(h: dict[str, Any]) -> None:
    """What the backend is running with. Changing it is a backend setting (.env), not a UI action."""
    components = h["components"]
    with st.popover("Status", icon=":material/tune:", width="stretch"):
        st.markdown("**How this app is running**")
        st.markdown(f"- **Evidence check:** {h['judge']}")
        st.markdown(f"- **Search:** {', '.join(h['retrieval_modes'])}")
        st.markdown(f"- **Local LLM (Ollama):** {'ready' if h['llm_available'] else 'off'}")
        st.markdown(f"- **Search index:** {(components.get('search') or {}).get('documents', '?')} sections")
        st.caption("Models and search run locally. Change them in the backend's .env file (see the README).")


def topbar() -> dict[str, Any]:
    try:
        h = client.health()
    except client.ApiError as e:
        st.error(str(e), icon=":material/cloud_off:")
        st.stop()
    left, mid, right = st.columns([3.2, 3.3, 1])
    left.markdown("<div class='topbar'><div class='brand'><div class='logo'>N</div>Northfield AI</div></div>",
                  unsafe_allow_html=True)
    mid.markdown(f"<div class='topbar' style='justify-content:flex-end'>{status_pill(h)}</div>", unsafe_allow_html=True)
    with right:
        status_popover(h)
    return h
