"""Tab 2: Ask in plain language. Questions are answered by the same /answer endpoint; answers are result cards."""
from __future__ import annotations

from html import escape

import streamlit as st

import client
from components.result import compact_card, render_result

SUGGESTIONS = [
    "Who actually approves big discounts at Northfield?",
    "Is DEAL-027 a violation of policy?",
    "Do support reps escalate Sev 1 issues through tickets?",
]


def _send(text: str) -> None:
    st.session_state.ask_queue = text


def _assistant_message(out: dict) -> dict:
    result = out["result"]
    a = result.get("answer") or {}
    if a.get("kind") == "claim" and a.get("checked_as"):
        lead = f"I checked this as: **“{escape(a['checked_as'])}”**"
        if out.get("note"):
            lead += f"  \n<span class='note'>{escape(out['note'])}</span>"
    else:
        lead = escape(a.get("note") or "Here is what the files say.")
    return {"role": "assistant", "text": lead, "result": result}


def _answer(text: str) -> dict:
    try:
        return _assistant_message(client.ask(text))
    except client.ApiError as e:
        return {"role": "assistant", "text": f"Something went wrong: {e}"}


def _show(messages: list[dict]) -> None:
    for i, m in enumerate(messages):
        with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else ":material/verified:"):
            if m.get("text"):
                st.markdown(m["text"], unsafe_allow_html=m["role"] == "assistant")
            if m.get("result"):
                compact_card(m["result"])
                with st.expander("Open full result", icon=":material/open_in_full:"):
                    render_result(m["result"], key=f"ask_{i}")


def render(health: dict) -> None:
    st.markdown(
        "<div class='hero-q'><h1>Ask how Northfield really works</h1>"
        "<p>Ask in your own words: about a rule, what actually happens, or a record such as DEAL-027. "
        "You get a clear answer and the files that prove it.</p></div>",
        unsafe_allow_html=True)

    messages = st.session_state.setdefault("ask_messages", [])
    if not messages:
        cols = st.columns(3)
        for i, s in enumerate(SUGGESTIONS):
            cols[i].button(s, key=f"sugg_{i}", on_click=_send, args=(s,), width="stretch")
    _show(messages)

    text = st.chat_input("Ask about approvals, onboarding, escalations, renewals…") or st.session_state.pop("ask_queue", None)
    if text:
        messages.append({"role": "user", "text": text})
        with st.spinner("Turning your question into a claim and checking it…"):
            messages.append(_answer(text))
        st.rerun()

    if messages and st.button("Clear conversation", key="ask_clear", icon=":material/delete_sweep:"):
        st.session_state.ask_messages = []
        st.rerun()
