"""
Northfield Credibility: Streamlit frontend.

Run (after the backend is up):  streamlit run frontend/app.py
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Northfield AI", page_icon="◆", layout="wide", initial_sidebar_state="collapsed")

from components import ask_tab, check_tab, evidence_tab, how_tab  # noqa: E402
from components.header import topbar  # noqa: E402
from theme import CSS  # noqa: E402

st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)

health = topbar()

t_check, t_ask, t_evidence, t_how = st.tabs([
    ":material/verified: Check a claim",
    ":material/forum: Ask",
    ":material/folder_open: Evidence",
    ":material/lightbulb: How it works",
])
with t_check:
    check_tab.render(health)
with t_ask:
    ask_tab.render(health)
with t_evidence:
    evidence_tab.render(health)
with t_how:
    how_tab.render(health)
