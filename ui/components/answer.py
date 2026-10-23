"""Plain answers: the answer card, why, scanned records and the evidence that proves it."""
from __future__ import annotations

from html import escape

import streamlit as st

from components.files import download_button, preview_dialog
from components.format import DRAFT_TAG, SUPERSEDED_TAG, rgba
from theme import OUTCOME_STYLE, ROLE_STYLE

KIND_NOTE = {
    "record": "Checked against the policy rulebook",
    "scan": "Every record checked against the rulebook",
    "rule": "From the policy rulebook and the records",
    "claim": "Checked against the evidence in the files",
    "passage": "From the passages that best match",
    "none": "",
}


def outcome_style(a: dict) -> tuple[str, str]:
    word, color = OUTCOME_STYLE.get(a.get("outcome"), ("Answer", "#FF6A12"))
    if a.get("kind") == "scan":
        word = "Breaches found" if a.get("outcome") == "VIOLATION" else "All clear"
    return word, color


def answer_card(result: dict) -> None:
    a = result["answer"]
    word, color = outcome_style(a)
    chips = []
    if KIND_NOTE.get(a.get("kind")):
        chips.append(f"<span class='chip'>{escape(KIND_NOTE[a['kind']])}</span>")
    checked = a.get("checked_as")
    if checked and a.get("kind") == "claim" and checked.strip().rstrip(".") != (a.get("question") or "").strip().rstrip(".?"):
        chips.append(f"<span class='chip'>Checked as: <b>{escape(checked[:140])}</b></span>")
    elif checked and a.get("kind") in ("record", "rule"):
        chips.append(f"<span class='chip'>Source: <b>{escape(checked)}</b></span>")
    gold = result.get("gold")
    if gold and result.get("scored"):
        match = "✓" if gold["expected_status"] == result["scored"]["status"] else "≠"
        chips.append(f"<span class='chip'>Team's expected answer: <b>{escape(gold['expected_status'])}</b> {match}</span>")
    st.markdown(
        f"<div class='verdict' style='--glow:{rgba(color, 0.18)}'>"
        f"<div><div class='claim-quote'>“{escape(a.get('question') or result.get('claim_text') or '')}”</div>"
        f"<div class='status-word' style='color:{color}'>{escape(word.upper())}</div>"
        f"<div class='headline'>{escape(a.get('headline') or '')}</div>"
        f"<p style='margin:0 0 .9rem;line-height:1.6;font-size:1.06rem;color:#E4E4E7'>{escape(a.get('answer') or '')}</p>"
        f"<div class='chips'>{''.join(chips)}</div></div></div>",
        unsafe_allow_html=True)


def answer_why(a: dict) -> None:
    if a.get("reasons"):
        st.markdown("<div class='panel'><h4>Why</h4><ul>" + "".join(f"<li>{escape(r)}</li>" for r in a["reasons"])
                    + "</ul></div>", unsafe_allow_html=True)
    if a.get("caveats"):
        st.markdown("<div class='callout'><b>Keep in mind.</b> " + " ".join(escape(c) for c in a["caveats"]) + "</div>",
                    unsafe_allow_html=True)
    if a.get("facts"):
        rows = "".join(f"<div style='display:grid;grid-template-columns:170px 1fr;gap:.8rem;padding:.3rem 0;"
                       f"border-bottom:1px solid #222'><span class='muted'>{escape(k)}</span><span>{escape(v)}</span></div>"
                       for k, v in a["facts"])
        st.markdown(f"<div class='panel'><h4>The record</h4>{rows}</div>", unsafe_allow_html=True)


def scan_items(a: dict) -> None:
    items = a.get("items") or []
    if not items:
        return
    st.markdown(f"<div class='col-title'>Every record checked <span class='count'>({len(items)})</span></div>",
                unsafe_allow_html=True)
    cols = st.columns(3)
    for i, it in enumerate(items):
        word, color = OUTCOME_STYLE.get(it["outcome"], ("", "#9CA3AF"))
        with cols[i % 3]:
            st.markdown(
                f"<div class='card' style='border-left:4px solid {color}'><div class='t'>{escape(it['record'])} "
                f"<span style='color:{color};font-weight:700'>· {escape(word)}</span></div>"
                f"<div class='s' style='color:#D4D4D8'>{escape(it['answer'])}</div></div>", unsafe_allow_html=True)


def _evidence_block(e: dict) -> str:
    tag, edge = ROLE_STYLE.get(e.get("role"), ("Source", "#9CA3AF"))
    flags = ""
    if e.get("status") == "superseded":
        flags += " " + SUPERSEDED_TAG
    if e.get("status") == "draft":
        flags += " " + DRAFT_TAG
    note = e.get("note") or ""
    section = f"<span class='ev-id' style='color:#FF6A12'> · {escape(e['section'])}</span>" if e.get("section") else ""
    date = f" · {escape(e['date'])}" if e.get("date") else ""
    return (f"<div class='ev' style='--edge:{edge}'>"
            f"<div class='ev-top'><div><span class='ev-id'>{escape(e['doc_id'])}</span>{section} "
            f"<span class='ev-src'>· {escape(e.get('type') or '')}{date}</span>"
            f"{flags}</div><span class='tag' style='background:#232327;color:{edge}'>{escape(tag)}</span></div>"
            + (f"<div class='ev-why'>{escape(note)}</div>" if note and note != tag else "")
            + f"<div class='ev-quote'>“{escape(e.get('quote') or '')}”</div>"
            f"<div class='wlbl'><span>{escape(e.get('title') or '')}</span><span>{escape(e.get('path') or '')}</span></div></div>")


def answer_evidence(a: dict, key: str) -> None:
    evidence = a.get("evidence") or []
    if not evidence:
        return
    st.markdown(f"<div class='col-title' style='margin-top:.6rem'>The evidence <span class='count'>({len(evidence)})</span></div>",
                unsafe_allow_html=True)
    cols = st.columns(2, gap="large")
    for i, e in enumerate(evidence):
        with cols[i % 2]:
            st.markdown(_evidence_block(e), unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            if c1.button("Preview file", key=f"apv_{key}_{i}", icon=":material/visibility:", width="stretch"):
                preview_dialog(e["doc_id"], e.get("section_id"))
            with c2:
                download_button(e["doc_id"], key=f"adl_{key}_{i}")
