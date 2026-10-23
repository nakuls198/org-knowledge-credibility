"""Tab 3: Evidence. Source files (preview / download) and the claims the team answered by hand."""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

import client
from components.files import download_button, preview_dialog
from components.format import DRAFT_TAG, SUPERSEDED_TAG, f5
from components.result import render_result
from theme import STATUS_COLORS

_FILES_PER_ROW = 3


def _kpis(items) -> None:
    st.markdown("<div class='kpis'>" + "".join(
        f"<div class='kpi'><div class='v'>{escape(str(v))}</div><div class='l'>{escape(label)}</div></div>" for v, label in items
    ) + "</div>", unsafe_allow_html=True)


# ---- source files -----------------------------------------------------------

@st.dialog("Claims found in this file", width="large")
def _claims_dialog(doc_id: str) -> None:
    with st.spinner("Reading the file and pulling out testable claims…"):
        try:
            found = client.extract_claims(doc_id)
        except client.ApiError as e:
            st.error(str(e))
            return
    st.caption(f"Extracted by {found['model']} ({found['provider']})")
    if not found["claims"]:
        st.info("No testable claims found in this file.")
    for i, c in enumerate(found["claims"]):
        st.markdown(
            f"<div class='card'><div class='t'>{escape(c['claim'])}</div>"
            f"<div class='s'>{escape(c.get('role') or '')} · {escape(c.get('layer') or '')}</div>"
            + (f"<div class='ev-quote'>“{escape(c['quote'])}”</div>" if c.get("quote") else "")
            + "</div>", unsafe_allow_html=True)
        if st.button("Check this claim", key=f"xc_{doc_id}_{i}"):
            st.session_state.pending_claim = c["claim"]
            st.session_state.run_now = True
            st.rerun()


def _file_card(d: dict, health: dict) -> None:
    flags = (" " + SUPERSEDED_TAG if d["superseded"] else "") + (" " + DRAFT_TAG if d["draft"] else "")
    st.markdown(
        f"<div class='card'><div class='t'>{escape(d['doc_id'])}{flags}</div>"
        f"<div class='s'>{escape(d['source_label'])} · {d['n_chunks']} sections · {escape(d['phase'])}</div>"
        f"<div class='s' style='margin-top:.2rem'>{escape(d['title'])}</div>"
        f"<div class='s'>{escape(d['date'] or '')}</div></div>", unsafe_allow_html=True)
    b1, b2 = st.columns(2)
    if b1.button("Preview", key=f"fpv_{d['doc_id']}", icon=":material/visibility:", width="stretch"):
        preview_dialog(d["doc_id"])
    with b2:
        download_button(d["doc_id"], key=f"fdl_{d['doc_id']}")
    if health.get("checks_ready") and st.button("Find claims in this file", key=f"fx_{d['doc_id']}",
                                                 icon=":material/auto_awesome:", width="stretch"):
        _claims_dialog(d["doc_id"])


def _files(health: dict) -> None:
    profile = client.corpus_profile()
    _kpis([(profile["documents"], "Source files"), (profile["sections"], "Searchable sections"),
           (profile["people"], "People on the roster"), (profile["gold_claims"], "Hand-checked claims")])

    docs = client.documents()
    types = sorted({d["source_label"] for d in docs})
    c1, c2 = st.columns([2, 1])
    query = c1.text_input("Search files", placeholder="Search by id or file name, e.g. POL-01, crm, interview",
                          label_visibility="collapsed")
    selected = c2.multiselect("Type", types, placeholder="All types", label_visibility="collapsed")
    shown = [d for d in docs if (not selected or d["source_label"] in selected)
             and (not query or query.lower() in d["doc_id"].lower() or query.lower() in (d["title"] or "").lower())]
    st.caption(f"{len(shown)} of {len(docs)} files · originals from the dataset folder")

    cols = st.columns(_FILES_PER_ROW)
    for i, d in enumerate(shown):
        with cols[i % _FILES_PER_ROW]:
            _file_card(d, health)

    with st.expander("People on the roster", icon=":material/groups:"):
        st.dataframe(pd.DataFrame(client.roster()), width="stretch", hide_index=True, height=360)


# ---- tested claims ------------------------------------------------------------

@st.dialog("Result", width="large")
def _result_dialog(claim_text: str, claim_id: str) -> None:
    with st.spinner("Checking…"):
        try:
            result = client.check(claim_text, claim_id=claim_id)
        except client.ApiError as e:
            st.error(str(e))
            return
    render_result(result, key=f"gold_{claim_id}")


def _latest_checks() -> dict[str, dict]:
    """The app's most recent answer for each claim text (to compare with the team's)."""
    try:
        latest: dict[str, dict] = {}
        for r in client.recent_checks(200):
            latest.setdefault(" ".join(r["claim_text"].lower().split()), r)
        return latest
    except client.ApiError:
        return {}


def _tested_claim_card(g: dict, latest: dict[str, dict]) -> None:
    expected_color = STATUS_COLORS.get(g["expected_status"], "#9CA3AF")
    last = latest.get(" ".join(g["claim_text"].lower().split()))
    last_html = "<span class='note'>Not checked yet</span>"
    if last:
        color = STATUS_COLORS.get(last["status"], "#9CA3AF")
        mark = "✓" if last["status"] == g["expected_status"] else "≠"
        last_html = (f"<span style='color:{color};font-weight:700'>App: {escape(last['status'])} {mark}</span> "
                     f"<span class='note'>{f5(last['score'])}</span>")
    st.markdown(
        f"<div class='card'><div class='s'>{escape(g['claim_id'])} · {escape(g['process'])} · {escape(g['layer'])}</div>"
        f"<div class='t' style='margin:.35rem 0 .5rem;font-weight:600;line-height:1.45'>{escape(g['claim_text'])}</div>"
        f"<div><span style='color:{expected_color};font-weight:700'>Team: {escape(g['expected_status'])}</span></div>"
        f"<div style='margin-top:.2rem'>{last_html}</div></div>", unsafe_allow_html=True)
    if st.button("Check now", key=f"gchk_{g['claim_id']}", width="stretch", icon=":material/verified:"):
        _result_dialog(g["claim_text"], g["claim_id"])


def _gold() -> None:
    claims = client.gold()["claims"]
    st.markdown("<div class='note' style='margin-bottom:.8rem'>Claims the team answered by hand. "
                "Each card shows the team's answer and the app's most recent answer, so you can see where they agree.</div>",
                unsafe_allow_html=True)
    layers = ["All"] + sorted({g["layer"] for g in claims})
    layer = st.segmented_control("Layer", layers, default="All", key="gold_layer", label_visibility="collapsed")
    latest = _latest_checks()
    cols = st.columns(3)
    for i, g in enumerate(g for g in claims if layer in (None, "All") or g["layer"] == layer):
        with cols[i % 3]:
            _tested_claim_card(g, latest)


def render(health: dict) -> None:
    view = st.segmented_control("Section", ["Source files", "Tested claims"], default="Source files", key="ev_view",
                                label_visibility="collapsed")
    if view == "Tested claims":
        _gold()
    else:
        _files(health)
