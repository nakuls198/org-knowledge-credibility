"""Tab 2: Ask in plain language. Questions become testable claims; answers are result cards."""
from __future__ import annotations

from html import escape

import streamlit as st

import api_client as api
from components.verdict import compact_card, render_result

SUGGESTIONS = [
    "Who actually approves big discounts at Northfield?",
    "Is DEAL-027 a violation of policy?",
    "Do support reps escalate Sev 1 issues through tickets?",
]


def _send(text: str) -> None:
    st.session_state.ask_queue = text


def render(health: dict) -> None:
    st.markdown(
        "<div class='hero-q'><h1>Ask how Northfield really works</h1>"
        "<p>Ask in your own words: about a rule, what actually happens, or a record such as DEAL-027. You get a clear answer and the files that prove it.</p></div>",
        unsafe_allow_html=True,
    )

    msgs = st.session_state.setdefault("ask_messages", [])
    if not msgs:
        cols = st.columns(3)
        for i, s in enumerate(SUGGESTIONS):
            cols[i].button(s, key=f"sugg_{i}", on_click=_send, args=(s,), use_container_width=True)

    for i, m in enumerate(msgs):
        with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else ":material/verified:"):
            if m.get("text"):
                st.markdown(m["text"], unsafe_allow_html=m["role"] == "assistant")
            r = m.get("result")
            if r:
                compact_card(r)
                with st.expander("Open full result", icon=":material/open_in_full:"):
                    render_result(r, key=f"ask_{i}")

    prompt = st.chat_input("Ask about approvals, onboarding, escalations, renewals…")
    text = prompt or st.session_state.pop("ask_queue", None)
    if text:
        msgs.append({"role": "user", "text": text})
        with st.spinner("Turning your question into a claim and checking it…"):
            try:
                out = api.ask(text)
            except api.ApiError as e:
                msgs.append({"role": "assistant", "text": f"Something went wrong: {e}"})
                st.rerun()
        n = out.get("normalised")
        res = out.get("result")
        if res is None:
            note = (n or {}).get("note") or "That doesn't look like a claim about how Northfield works."
            msgs.append({"role": "assistant", "text": f"{note} Try asking who does what, or whether a rule is followed."})
        else:
            a = res.get("answer") or {}
            if a.get("kind") == "claim" and a.get("checked_as"):
                lead = f"I checked this as: **“{escape(a['checked_as'])}”**"
            else:
                lead = escape(a.get("note") or "Here is what the files say.")
            if n and n.get("note") and a.get("kind") == "claim":
                lead += f"  \n<span class='note'>{escape(n['note'])}</span>"
            msgs.append({"role": "assistant", "text": lead, "result": res})
        st.rerun()

    if msgs:
        if st.button("Clear conversation", key="ask_clear", icon=":material/delete_sweep:"):
            st.session_state.ask_messages = []
            st.rerun()
