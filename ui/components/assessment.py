"""The evidence behind a checked claim: verdict, plain-English reasons, what supports and contradicts it,
and the full trail of everything search found."""
from __future__ import annotations

from html import escape

import streamlit as st

from components.files import download_button, preview_dialog
from components.format import COPY_TAG, DRAFT_TAG, SUPERSEDED_TAG, f5, rgba
from theme import STANCE_COLORS, STATUS_COLORS

_VISIBLE_PER_SIDE = 6


def verdict_card(result: dict) -> None:
    scored, explanation = result["scored"], result.get("explanation") or {}
    status = scored["status"]
    color = STATUS_COLORS.get(status, "#9CA3AF")
    chips = [
        f"<span class='chip'>How sure: <b>{escape(scored['confidence'])}</b></span>",
        f"<span class='chip'><b>{result['counts']['support']}</b> supporting · <b>{result['counts']['contradict']}</b> against</span>",
    ]
    gold = result.get("gold")
    if gold:
        match = "✓" if gold["expected_status"] == status else "≠"
        chips.append(f"<span class='chip'>Team's expected answer: <b>{escape(gold['expected_status'])}</b> {match}</span>")
    st.markdown(
        f"<div class='verdict' style='--glow:{rgba(color, 0.18)}'>"
        f"<div><div class='claim-quote'>“{escape(result['claim_text'])}”</div>"
        f"<div class='status-word' style='color:{color}'>{escape(status.upper())}</div>"
        f"<div class='headline'>{escape(explanation.get('headline') or result['plain']['status'])}</div>"
        f"<div class='chips'>{''.join(chips)}</div></div></div>",
        unsafe_allow_html=True)


def why_panel(result: dict) -> None:
    explanation = result.get("explanation") or {}
    reasons = "".join(f"<li>{escape(w)}</li>" for w in explanation.get("why", []))
    st.markdown(
        f"<div class='panel'><h4>In plain English</h4>"
        f"<p style='margin:0 0 .7rem;line-height:1.6;font-size:1.04rem'>{escape(explanation.get('summary') or result['plain']['status'])}</p>"
        f"<ul>{reasons}</ul>"
        f"<p class='muted' style='margin:.7rem 0 0'><b style='color:#F4F4F5'>What would change this:</b> "
        f"{escape(explanation.get('what_would_change_it', ''))}</p></div>",
        unsafe_allow_html=True)


def evidence_card(e: dict, key: str) -> None:
    edge = STANCE_COLORS[e["stance"]]
    date = e.get("metadata", {}).get("date")
    weight = float(e.get("final_weight", 0))
    flags = ""
    if e.get("metadata", {}).get("superseded"):
        flags += " " + SUPERSEDED_TAG
    if e.get("metadata", {}).get("draft"):
        flags += " " + DRAFT_TAG
    if e.get("independence_weight", 1) < 1:
        flags += " " + COPY_TAG
    quote = e.get("quote") or ""
    st.markdown(
        f"<div class='ev' style='--edge:{edge}'>"
        f"<div class='ev-top'><div><span class='ev-id'>{escape(e['cite_id'])}</span> "
        f"<span class='ev-src'>· {escape(e['source_label'])}{' · ' + escape(date) if date else ''}</span>{flags}</div></div>"
        f"<div class='ev-why'>{escape(e.get('rationale') or '')}</div>"
        + (f"<div class='ev-quote'>“{escape(quote)}”</div>" if quote else "")
        + f"<div class='wbar'><span style='width:{min(100, weight * 100):.1f}%'></span></div>"
        f"<div class='wlbl'><span>{escape(e.get('weight_label', ''))}</span><span>weight {f5(weight)}</span></div></div>",
        unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    if c1.button("Preview file", key=f"pv_{key}", icon=":material/visibility:", width="stretch"):
        preview_dialog(e["doc_id"], e["chunk_id"])
    with c2:
        download_button(e["doc_id"], key=f"dl_{key}")


def _evidence_side(title: str, stance: str, items: list[dict], key: str, empty: str, more_label: str) -> None:
    st.markdown(f"<div class='col-title'><span class='dot' style='background:{STANCE_COLORS[stance]}'></span>"
                f"{title} <span class='count'>({len(items)})</span></div>", unsafe_allow_html=True)
    if not items:
        st.markdown(f"<div class='note'>{empty}</div>", unsafe_allow_html=True)
    prefix = stance[0]
    for i, e in enumerate(items[:_VISIBLE_PER_SIDE]):
        evidence_card(e, f"{key}_{prefix}{i}")
    if len(items) > _VISIBLE_PER_SIDE:
        with st.expander(f"{len(items) - _VISIBLE_PER_SIDE} {more_label}"):
            for i, e in enumerate(items[_VISIBLE_PER_SIDE:], start=_VISIBLE_PER_SIDE):
                evidence_card(e, f"{key}_{prefix}{i}")


def evidence_columns(result: dict, key: str) -> None:
    by_weight = lambda stance: sorted([e for e in result["evidence"] if e["stance"] == stance],
                                      key=lambda e: e["final_weight"], reverse=True)
    left, right = st.columns(2, gap="large")
    with left:
        _evidence_side("What supports it", "support", by_weight("support"), key,
                       "Nothing relevant supports this claim.", "more supporting")
    with right:
        _evidence_side("What goes against it", "contradict", by_weight("contradict"), key,
                       "Nothing relevant goes against this claim.", "more against")


def trail(result: dict) -> None:
    copies = result.get("merged_copies") or []
    if copies:
        groups = "; ".join(" + ".join(g) for g in copies)
        st.markdown(f"<div class='callout'><b>Copies merged.</b> These excerpts are near-identical, so together they count as "
                    f"one source instead of several: {escape(groups)}.</div>", unsafe_allow_html=True)
    items = []
    for h in result["all_hits"]:
        c = STANCE_COLORS[h["stance"]]
        date = h.get("metadata", {}).get("date") or ""
        items.append(
            f"<div class='tl-item' style='--dotc:{c}'><div class='tl-head'>"
            f"<span><b>#{h['rank']} {escape(h['cite_id'])}</b> <span class='note'>· {escape(h['source_label'])} {escape(date)}</span></span>"
            f"<span style='color:{c};font-weight:700'>{escape(h['stance_label'])}</span></div>"
            f"<div class='tl-body'>{escape(h.get('rationale') or '')}</div></div>")
    st.markdown("<div class='note' style='margin-bottom:.6rem'>Everything search found, in rank order. "
                "“Not relevant” items are dropped before scoring, not averaged in.</div>"
                f"<div class='tl'>{''.join(items)}</div>", unsafe_allow_html=True)


def full_assessment(result: dict, key: str) -> None:
    for notice in result.get("notices") or []:
        st.warning(notice, icon=":material/info:")
    verdict_card(result)
    why_panel(result)
    evidence_columns(result, key)
    with st.expander("Full evidence trail", icon=":material/timeline:"):
        trail(result)
