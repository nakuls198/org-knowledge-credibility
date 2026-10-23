"""A checked claim or question as the user sees it: the plain answer, the full details, and feedback."""
from __future__ import annotations

from html import escape

import streamlit as st

import client
from components.answer import KIND_NOTE, answer_card, answer_evidence, answer_why, outcome_style, scan_items
from components.assessment import full_assessment
from theme import OUTCOME_STYLE


def feedback_box(result: dict, key: str) -> None:
    run_id = result.get("run_id")
    if not run_id:
        return
    done_key = f"fb_done_{run_id}"
    st.markdown("<div class='col-title' style='margin-top:.6rem'>Was this right?</div>", unsafe_allow_html=True)
    if st.session_state.get(done_key):
        st.success(st.session_state[done_key], icon=":material/check_circle:")
        return
    yes, no, _ = st.columns([1, 1, 3])
    if yes.button("Yes, looks right", key=f"fb_y_{key}", icon=":material/thumb_up:", width="stretch"):
        _save(done_key, run_id, True, "Thanks, saved. ")
    if no.button("No, it's wrong", key=f"fb_n_{key}", icon=":material/thumb_down:", width="stretch"):
        st.session_state[f"fb_open_{run_id}"] = True
    if st.session_state.get(f"fb_open_{run_id}"):
        with st.form(key=f"fb_form_{key}"):
            current = (result.get("scored") or {}).get("status")
            if current:
                options = [s for s in client.STATUS.values() if s != current]
            else:
                outcome = (result.get("answer") or {}).get("outcome")
                options = [word for name, (word, _) in OUTCOME_STYLE.items() if name != outcome]
            correct = st.selectbox("What should the answer be?", options)
            note = st.text_input("Why? (optional)")
            if st.form_submit_button("Save correction"):
                _save(done_key, run_id, False, "Correction saved. ", correct, note or None)


def _save(done_key: str, run_id: str, is_correct: bool, prefix: str, correct: str | None = None, note: str | None = None) -> None:
    try:
        saved = client.save_feedback(run_id, is_correct, correct, note)
        st.session_state[done_key] = f"{prefix}{saved['calibration']['message']}"
    except client.ApiError as e:
        st.session_state[done_key] = f"Could not save: {e}"
    st.rerun()


def render_result(result: dict, key: str) -> None:
    answer = result["answer"]
    answer_card(result)
    answer_why(answer)
    scan_items(answer)
    answer_evidence(answer, key)
    if result.get("scored"):
        with st.expander("Full details: how the evidence was weighed", icon=":material/fact_check:"):
            full_assessment(result, key)
    feedback_box(result, key)


def compact_card(result: dict) -> None:
    a = result["answer"]
    word, color = outcome_style(a)
    doc_ids = list(dict.fromkeys(e["doc_id"] for e in (a.get("evidence") or [])))[:4]
    st.markdown(
        f"<div class='panel' style='border-left:4px solid {color}'>"
        f"<div style='display:flex;align-items:center;gap:1rem;flex-wrap:wrap'>"
        f"<span style='font-size:1.6rem;font-weight:900;color:{color};letter-spacing:-0.03em'>{escape(word.upper())}</span>"
        f"<span class='chip'>{escape(KIND_NOTE.get(a.get('kind'), '') or 'Answer')}</span></div>"
        f"<p style='font-size:1.08rem;font-weight:600;margin:.7rem 0 .4rem;line-height:1.5'>{escape(a.get('headline') or '')}</p>"
        f"<p style='margin:0 0 .5rem;line-height:1.55;color:#E4E4E7'>{escape(a.get('answer') or '')}</p>"
        f"<div class='note'>Evidence: {escape(', '.join(doc_ids) or 'none')}</div></div>",
        unsafe_allow_html=True)

