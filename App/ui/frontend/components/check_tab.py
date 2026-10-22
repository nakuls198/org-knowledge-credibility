"""Tab 1: Check a claim. Guided: type → verdict → why → evidence → feedback."""
from __future__ import annotations

from html import escape

import streamlit as st

import api_client as api
from components.verdict import f5, render_result
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


def _set_claim(text: str) -> None:
    st.session_state.claim_input = text


def _recent() -> None:
    try:
        rows = [r for r in api.runs(12) if r["origin"] != "eval"][:6]
    except api.ApiError:
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
                unsafe_allow_html=True,
            )
            st.button("Check again", key=f"recent_{r['id']}", on_click=_set_claim, args=(r["claim_text"],),
                      use_container_width=True)


def render(health: dict) -> None:
    st.markdown(
        "<div class='hero-q'><h1>Check your claim at Northfield</h1>"
        "<p>Ask a question or write a claim about a rule, a practice or a record such as DEAL-027. "
        "You get a clear answer, the reasons, and the files behind it.</p></div>",
        unsafe_allow_html=True,
    )

    if "pending_claim" in st.session_state:
        st.session_state.claim_input = st.session_state.pop("pending_claim")
        st.toast("Claim loaded. Result is below in Check a claim.", icon=":material/verified:")
    st.session_state.setdefault("claim_input", "")
    claim = st.text_area(
        "Claim", key="claim_input", height=96, label_visibility="collapsed",
        placeholder="e.g. Finance must approve discounts of 15% or more before a deal is Closed Won",
    )

    def _on_pill():
        p = st.session_state.get("example_pill")
        if p:
            st.session_state.claim_input = EXAMPLES[p]

    st.pills("Examples", list(EXAMPLES), key="example_pill", label_visibility="collapsed", on_change=_on_pill)

    gold = api.gold().get("claims", [])
    options = {f"{g['claim_id']} · {g['process']} · {g['claim_text']}": g["claim_text"] for g in gold}

    def _on_gold():
        c = st.session_state.get("gold_pick")
        if c and c in options:
            st.session_state.claim_input = options[c]

    with st.expander(f"Or pick one of the {len(gold)} claims the team has already answered by hand", icon=":material/fact_check:"):
        st.selectbox("Tested claims", ["—"] + list(options), label_visibility="collapsed", key="gold_pick",
                     on_change=_on_gold)

    with st.expander("Advanced options", icon=":material/tune:"):
        a1, a2, a3 = st.columns(3)
        mode = a1.selectbox("Search", ["hybrid", "bm25", "dense"],
                            format_func={"hybrid": "Hybrid (keyword + meaning)", "bm25": "Keyword only", "dense": "Meaning only"}.get,
                            disabled=len(health.get("retrieval_modes", [])) < 3)
        engine = a2.selectbox("Evidence check", ["nli", "heuristic"],
                              format_func={"nli": "Rules + NLI model", "heuristic": "Keyword baseline"}.get)
        top_k = a3.slider("Pieces of evidence to read", 5, 30, 15)
        b1, b2 = st.columns(2)
        method = b1.selectbox("Scoring formula", ["bayesian", "weighted_checklist"],
                              format_func={"bayesian": "Bayesian update", "weighted_checklist": "Weighted checklist"}.get)
        prior = b2.slider("Starting belief before evidence", 0.1, 0.9, 0.5, 0.05)

    go = st.button("Check credibility", type="primary", use_container_width=True, key="go_check",
                   icon=":material/verified:")
    if (go or st.session_state.pop("run_now", False)) and claim.strip():
        if len(claim.strip()) < 5:
            st.warning("Write a full sentence so there is something to test.")
        else:
            with st.status("Checking…", expanded=False) as s:
                s.update(label="Searching the files and records, checking the rules, weighing the evidence…")
                try:
                    st.session_state.last_result = api.check({
                        "claim": claim.strip(), "top_k": top_k, "retrieval_mode": mode,
                        "stance_engine": engine, "method": method, "prior": prior,
                    })
                    s.update(label="Done", state="complete")
                except api.ApiError as e:
                    s.update(label="Something went wrong", state="error")
                    st.error(str(e))
    elif go:
        st.warning("Type a claim first.")

    result = st.session_state.get("last_result")
    if result:
        st.markdown("<div style='height:.4rem'></div>", unsafe_allow_html=True)
        render_result(result, key=f"chk_{result.get('run_id', 'x')}")
    else:
        _recent()
