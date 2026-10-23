"""Tab 1: Check a claim. Guided: type -> answer -> why -> evidence -> feedback."""
from __future__ import annotations

from html import escape

import streamlit as st

import client
from components.format import f5
from components.result import render_result
from theme import STATUS_COLORS

EXAMPLES = {
    "Is DEAL-027 a violation?": "Is DEAL-027 a violation of policy?",
    "Which deals broke policy?": "Which deals violated the discount policy?",
    "Discount approval": "Head of Finance and People Ops must approve new-business discounts of 15 percent or more in the CRM before Closed Won.",
    "Urgent-deal workaround": "In practice, the Head of Sales sometimes approves urgent discounts herself without waiting for Finance sign-off.",
    "$700 expense": "Can my manager approve a $700 expense?",
    "Feature requests": "Feature requests are currently tracked on a Trello board.",
    "Renewal discount cap": "All renewal discounts stay within the 10 percent cap.",
}
_RECENT_SHOWN = 6
_SEARCH_LABELS = {"hybrid": "Hybrid (keyword + meaning)", "bm25": "Keyword only", "dense": "Meaning only"}
_CHECK_LABELS = {"nli": "Rules + NLI model", "heuristic": "Keyword baseline"}


def _set_claim(text: str) -> None:
    st.session_state.claim_input = text


def _recent() -> None:
    try:
        rows = client.recent_checks(12)[:_RECENT_SHOWN]
    except client.ApiError:
        return
    if not rows:
        return
    st.markdown("<div class='col-title' style='margin-top:1.4rem'>Recently checked</div>", unsafe_allow_html=True)
    cols = st.columns(3)
    for i, r in enumerate(rows):
        color = STATUS_COLORS.get(r["status"], "#9CA3AF")
        with cols[i % 3]:
            st.markdown(
                f"<div class='card'><div style='color:{color};font-weight:800'>● {escape(r['status'])}"
                f" <span class='note'>· {f5(r['score'])}</span></div>"
                f"<div class='s' style='margin-top:.35rem;color:#D4D4D8'>{escape(r['claim_text'][:120])}</div></div>",
                unsafe_allow_html=True)
            st.button("Check again", key=f"recent_{r['id']}", on_click=_set_claim, args=(r["claim_text"],),
                      width="stretch")


def _example_pills() -> None:
    def on_pick():
        picked = st.session_state.get("example_pill")
        if picked:
            st.session_state.claim_input = EXAMPLES[picked]

    st.pills("Examples", list(EXAMPLES), key="example_pill", label_visibility="collapsed", on_change=on_pick)


def _hand_checked_claims() -> None:
    gold = client.gold().get("claims", [])
    options = {f"{g['claim_id']} · {g['process']} · {g['claim_text']}": g["claim_text"] for g in gold}

    def on_pick():
        picked = st.session_state.get("gold_pick")
        if picked in options:
            st.session_state.claim_input = options[picked]

    with st.expander(f"Or pick one of the {len(gold)} claims the team has already answered by hand", icon=":material/fact_check:"):
        st.selectbox("Tested claims", ["—"] + list(options), label_visibility="collapsed", key="gold_pick", on_change=on_pick)


def _advanced_options(health: dict) -> tuple[str, str, int]:
    with st.expander("Advanced options", icon=":material/tune:"):
        a1, a2, a3 = st.columns(3)
        mode = a1.selectbox("Search", ["hybrid", "bm25", "dense"], format_func=_SEARCH_LABELS.get,
                            disabled=len(health.get("retrieval_modes", [])) < 3)
        judge = a2.selectbox("Evidence check", ["nli", "heuristic"], format_func=_CHECK_LABELS.get)
        top_k = a3.slider("Pieces of evidence to read", 5, 30, 15)
    return mode, judge, top_k


def _run_check(claim: str, mode: str, judge: str, top_k: int) -> None:
    with st.status("Checking…", expanded=False) as status:
        status.update(label="Searching the files and records, checking the rules, weighing the evidence…")
        try:
            st.session_state.last_result = client.check(claim, judge=judge, retrieval_mode=mode, top_k=top_k)
            status.update(label="Done", state="complete")
        except client.ApiError as e:
            status.update(label="Something went wrong", state="error")
            st.error(str(e))


def render(health: dict) -> None:
    st.markdown(
        "<div class='hero-q'><h1>Check your claim at Northfield</h1>"
        "<p>Ask a question or write a claim about a rule, a practice or a record such as DEAL-027. "
        "You get a clear answer, the reasons, and the files behind it.</p></div>",
        unsafe_allow_html=True)

    if "pending_claim" in st.session_state:
        st.session_state.claim_input = st.session_state.pop("pending_claim")
        st.toast("Claim loaded. Result is below in Check a claim.", icon=":material/verified:")
    st.session_state.setdefault("claim_input", "")
    claim = st.text_area("Claim", key="claim_input", height=96, label_visibility="collapsed",
                         placeholder="e.g. Finance must approve discounts of 15% or more before a deal is Closed Won")
    _example_pills()
    _hand_checked_claims()
    mode, judge, top_k = _advanced_options(health)

    go = st.button("Check credibility", type="primary", width="stretch", key="go_check", icon=":material/verified:")
    if (go or st.session_state.pop("run_now", False)) and claim.strip():
        if len(claim.strip()) < 5:
            st.warning("Write a full sentence so there is something to test.")
        else:
            _run_check(claim.strip(), mode, judge, top_k)
    elif go:
        st.warning("Type a claim first.")

    result = st.session_state.get("last_result")
    if result:
        st.markdown("<div style='height:.4rem'></div>", unsafe_allow_html=True)
        render_result(result, key=f"chk_{result.get('run_id', 'x')}")
    else:
        _recent()
