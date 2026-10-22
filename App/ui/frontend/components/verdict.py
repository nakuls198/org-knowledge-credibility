"""The credibility result: verdict first, then why, then evidence, then the full trail."""
from __future__ import annotations

import math
from html import escape
from typing import Any, Dict, List

import streamlit as st

import api_client as api
from components.files import download_button, preview_dialog
from theme import STANCE_COLORS, STATUS_COLORS

STATUSES = ["Supported", "Likely", "Conflicted", "Unlikely", "Unsupported", "Not enough evidence"]


def f5(x) -> str:
    try:
        return f"{float(x):.5f}"
    except (TypeError, ValueError):
        return "—"


def ring_svg(score: float, color: str, size: int = 190) -> str:
    r = 80
    circ = 2 * math.pi * r
    off = circ * (1 - max(0.0, min(1.0, score)))
    return (
        f"<div class='ring' style='width:{size}px;height:{size}px'>"
        f"<svg width='{size}' height='{size}' viewBox='0 0 190 190'>"
        f"<circle cx='95' cy='95' r='{r}' fill='none' stroke='#26262A' stroke-width='14'/>"
        f"<circle cx='95' cy='95' r='{r}' fill='none' stroke='{color}' stroke-width='14' stroke-linecap='round' "
        f"stroke-dasharray='{circ:.2f}' stroke-dashoffset='{off:.2f}'/></svg>"
        f"<div class='center'><div class='num'>{f5(score)}</div><div class='lbl'>Credibility</div></div></div>"
    )


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def verdict_card(result: Dict[str, Any]) -> None:
    sc = result["scored"]
    status = sc["status"]
    color = STATUS_COLORS.get(status, "#9CA3AF")
    exp = result.get("explanation") or {}
    plain = result["plain"]
    gold = result.get("gold")

    chips = [
        f"<span class='chip'>How sure: <b>{escape(sc['confidence'])}</b></span>",
        f"<span class='chip'><b>{result['counts']['support']}</b> supporting · <b>{result['counts']['contradict']}</b> against</span>",
    ]
    # Score range chip hidden in the UI; uncomment to show again.
    # if sc.get("credible_interval"):
    #     lo, hi = sc["credible_interval"]
    #     chips.append(f"<span class='chip'>Range <b>{f5(lo)}</b> – <b>{f5(hi)}</b></span>")
    if gold:
        match = "✓" if gold["expected_status"] == status else "≠"
        chips.append(
            f"<span class='chip'>Team's expected answer: <b>{escape(gold['expected_status'])}</b> {match}</span>"
        )

    # Score ring + meaning hidden in the UI (single-column card). To show again, restore the grid columns and
    # add this as the first child of the card:
    #   f"<div>{ring_svg(sc['credibility_score'], color)}<div class='meaning'>{escape(plain['score'])}</div></div>"
    st.markdown(
        f"<div class='verdict' style='--glow:{_hex_to_rgba(color, 0.18)};grid-template-columns:1fr'>"
        f"<div><div class='claim-quote'>“{escape(result['claim_text'])}”</div>"
        f"<div class='status-word' style='color:{color}'>{escape(status.upper())}</div>"
        f"<div class='headline'>{escape(exp.get('headline') or plain['status'])}</div>"
        f"<div class='chips'>{''.join(chips)}</div></div></div>",
        unsafe_allow_html=True,
    )


def why_panel(result: Dict[str, Any]) -> None:
    exp = result.get("explanation") or {}
    plain = result["plain"]
    why = "".join(f"<li>{escape(w)}</li>" for w in exp.get("why", []))
    st.markdown(
        f"<div class='panel'><h4>In plain English</h4>"
        f"<p style='margin:0 0 .7rem;line-height:1.6;font-size:1.04rem'>{escape(exp.get('summary') or plain['status'])}</p>"
        f"<ul>{why}</ul>"
        f"<p class='muted' style='margin:.7rem 0 0'><b style='color:#F4F4F5'>What would change this:</b> "
        f"{escape(exp.get('what_would_change_it', ''))}</p></div>",
        unsafe_allow_html=True,
    )


def evidence_card(e: Dict[str, Any], key: str) -> None:
    edge = STANCE_COLORS[e["stance"]]
    date = e.get("metadata", {}).get("date")
    w = float(e.get("final_weight", 0))
    flags = ""
    if e.get("metadata", {}).get("superseded"):
        flags += " <span class='tag' style='background:#3A1D1D;color:#FCA5A5'>Superseded</span>"
    if e.get("metadata", {}).get("draft"):
        flags += " <span class='tag' style='background:#3A2E12;color:#FCD34D'>Draft</span>"
    if e.get("independence_weight", 1) < 1:
        flags += " <span class='tag' style='background:#1E293B;color:#93C5FD'>Copy · shared weight</span>"
    quote = e.get("quote") or ""
    st.markdown(
        f"<div class='ev' style='--edge:{edge}'>"
        f"<div class='ev-top'><div><span class='ev-id'>{escape(e['cite_id'])}</span> "
        f"<span class='ev-src'>· {escape(e['source_label'])}{' · ' + escape(date) if date else ''}</span>{flags}</div></div>"
        f"<div class='ev-why'>{escape(e.get('rationale') or '')}</div>"
        + (f"<div class='ev-quote'>“{escape(quote)}”</div>" if quote else "")
        + f"<div class='wbar'><span style='width:{min(100, w * 100):.1f}%'></span></div>"
        f"<div class='wlbl'><span>{escape(e.get('weight_label', ''))}</span><span>weight {f5(w)}</span></div></div>",
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    if c1.button("Preview file", key=f"pv_{key}", icon=":material/visibility:", use_container_width=True):
        preview_dialog(e["doc_id"], e["chunk_id"])
    with c2:
        download_button(e["doc_id"], key=f"dl_{key}")


def evidence_columns(result: Dict[str, Any], key: str) -> None:
    ev = result["evidence"]
    sup = sorted([e for e in ev if e["stance"] == "support"], key=lambda e: e["final_weight"], reverse=True)
    con = sorted([e for e in ev if e["stance"] == "contradict"], key=lambda e: e["final_weight"], reverse=True)
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown(
            f"<div class='col-title'><span class='dot' style='background:{STANCE_COLORS['support']}'></span>"
            f"What supports it <span class='count'>({len(sup)})</span></div>", unsafe_allow_html=True)
        if not sup:
            st.markdown("<div class='note'>Nothing relevant supports this claim.</div>", unsafe_allow_html=True)
        for i, e in enumerate(sup[:6]):
            evidence_card(e, f"{key}_s{i}")
        if len(sup) > 6:
            with st.expander(f"{len(sup) - 6} more supporting"):
                for i, e in enumerate(sup[6:], start=6):
                    evidence_card(e, f"{key}_s{i}")
    with right:
        st.markdown(
            f"<div class='col-title'><span class='dot' style='background:{STANCE_COLORS['contradict']}'></span>"
            f"What goes against it <span class='count'>({len(con)})</span></div>", unsafe_allow_html=True)
        if not con:
            st.markdown("<div class='note'>Nothing relevant goes against this claim.</div>", unsafe_allow_html=True)
        for i, e in enumerate(con[:6]):
            evidence_card(e, f"{key}_c{i}")
        if len(con) > 6:
            with st.expander(f"{len(con) - 6} more against"):
                for i, e in enumerate(con[6:], start=6):
                    evidence_card(e, f"{key}_c{i}")


def trail(result: Dict[str, Any], key: str) -> None:
    hits = result["all_hits"]
    copies = result.get("merged_copies") or []
    if copies:
        groups = "; ".join(" + ".join(g) for g in copies)
        st.markdown(
            f"<div class='callout'><b>Copies merged.</b> These excerpts are near-identical, so together they count as "
            f"one source instead of several: {escape(groups)}.</div>", unsafe_allow_html=True)
    items = []
    for h in hits:
        c = STANCE_COLORS[h["stance"]]
        date = h.get("metadata", {}).get("date") or ""
        items.append(
            f"<div class='tl-item' style='--dotc:{c}'><div class='tl-head'>"
            f"<span><b>#{h['rank']} {escape(h['cite_id'])}</b> <span class='note'>· {escape(h['source_label'])} {escape(date)}</span></span>"
            f"<span style='color:{c};font-weight:700'>{escape(h['stance_label'])}</span></div>"
            f"<div class='tl-body'>{escape(h.get('rationale') or '')}</div></div>"
        )
    st.markdown(
        "<div class='note' style='margin-bottom:.6rem'>Everything search found, in rank order. "
        "“Not relevant” items are dropped before scoring, not averaged in.</div>"
        f"<div class='tl'>{''.join(items)}</div>", unsafe_allow_html=True)


def production_details(result: Dict[str, Any]) -> None:
    sc = result["scored"]
    exp = result.get("explanation") or {}
    judge = "Rules + NLI model" if result["stance_engine"] == "nli" else "Keyword baseline"
    writer = f"LLM · {exp.get('model')}" if exp.get("source") == "llm" else "Fixed template"
    t = result.get("timings_ms", {})
    rows = [
        ("Search", {"hybrid": "Hybrid: keyword + meaning", "bm25": "Keyword (BM25)", "dense": "Meaning (embeddings)"}
         .get(result["retrieval_mode"], result["retrieval_mode"]) + f" · top {result['top_k']}"),
        ("Evidence checked by", judge),
        ("Score computed by", f"Python formula ({sc['method']}, starting point {f5(sc['prior'])}). No model sets the number."),
        ("Explanation written by", writer + (f" · {exp['note']}" if exp.get("note") else "")),
        ("Support weight / against weight", f"{f5(sc['support_weight'])} / {f5(sc['contradict_weight'])}"),
        ("Score range meaning", result["plain"]["interval"]),
        ("How sure", result["plain"]["confidence"]),
        ("Calibration", result["calibration"]),
        ("Time", f"search {t.get('retrieve_ms', 0):.0f} ms · judging {t.get('reason_ms', 0):.0f} ms · writing {t.get('explain_ms', 0):.0f} ms"),
    ]
    st.markdown(
        "<div class='panel'>" + "".join(
            f"<div style='display:grid;grid-template-columns:230px 1fr;gap:.8rem;padding:.35rem 0;border-bottom:1px solid #222'>"
            f"<span class='muted'>{escape(k)}</span><span>{escape(v)}</span></div>" for k, v in rows
        ) + "</div>", unsafe_allow_html=True)


def feedback_box(result: Dict[str, Any], key: str) -> None:
    run_id = result.get("run_id")
    if not run_id:
        return
    done_key = f"fb_done_{run_id}"
    st.markdown("<div class='col-title' style='margin-top:.6rem'>Was this right?</div>", unsafe_allow_html=True)
    if st.session_state.get(done_key):
        st.success(st.session_state[done_key], icon=":material/check_circle:")
        return
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("Yes, looks right", key=f"fb_y_{key}", icon=":material/thumb_up:", use_container_width=True):
        try:
            r = api.feedback(run_id, True)
            st.session_state[done_key] = f"Thanks, saved. {r['calibration']['message']}"
        except api.ApiError as e:
            st.session_state[done_key] = f"Could not save: {e}"
        st.rerun()
    if c2.button("No, it's wrong", key=f"fb_n_{key}", icon=":material/thumb_down:", use_container_width=True):
        st.session_state[f"fb_open_{run_id}"] = True
    if st.session_state.get(f"fb_open_{run_id}"):
        with st.form(key=f"fb_form_{key}"):
            current = (result.get("scored") or {}).get("status")
            if current:
                options = [s for s in STATUSES if s != current]
            else:
                options = [v[0] for k, v in OUTCOME_STYLE.items() if k != (result.get("answer") or {}).get("outcome")]
            correct = st.selectbox("What should the answer be?", options)
            note = st.text_input("Why? (optional)")
            if st.form_submit_button("Save correction"):
                try:
                    r = api.feedback(run_id, False, correct, note or None)
                    st.session_state[done_key] = f"Correction saved. {r['calibration']['message']}"
                except api.ApiError as e:
                    st.session_state[done_key] = f"Could not save: {e}"
                st.rerun()


# ---- plain answers ------------------------------------------------------------------------------

# outcome -> (word shown, colour)
OUTCOME_STYLE = {
    "VIOLATION": ("Policy violation", "#EF4444"),
    "LIKELY_VIOLATION": ("Likely violation", "#FB7185"),
    "COMPLIANT": ("Follows policy", "#22C55E"),
    "NOT_APPLICABLE": ("Rule doesn't apply", "#9CA3AF"),
    "CANNOT_TELL": ("Can't tell", "#F59E0B"),
    "NO_RULE": ("Record", "#9CA3AF"),
    "YES": ("Yes", "#22C55E"),
    "MOSTLY": ("Mostly yes", "#2DD4BF"),
    "MIXED": ("It depends", "#F59E0B"),
    "NO": ("No", "#EF4444"),
    "UNKNOWN": ("No clear answer", "#9CA3AF"),
    "INFO": ("Answer", "#FF6A12"),
    "FOUND": ("Answer", "#FF6A12"),
    "ERROR": ("Error", "#EF4444"),
}

# evidence role -> (tag, edge colour)
ROLE_STYLE = {
    "rule": ("The rule", "#FF6A12"),
    "record": ("The record", "#93C5FD"),
    "audit": ("Audit log", "#93C5FD"),
    "mention": ("Mentions it", "#9CA3AF"),
    "practice": ("What actually happened", "#F59E0B"),
    "supports": ("Supports", "#22C55E"),
    "against": ("Goes against", "#EF4444"),
    "context": ("Source", "#9CA3AF"),
}

KIND_NOTE = {
    "record": "Checked against the policy rulebook",
    "scan": "Every record checked against the rulebook",
    "rule": "From the policy rulebook and the records",
    "claim": "Checked against the evidence in the files",
    "passage": "From the passages that best match",
    "none": "",
}


def _outcome(a: Dict[str, Any]) -> tuple:
    word, color = OUTCOME_STYLE.get(a.get("outcome"), ("Answer", "#FF6A12"))
    if a.get("kind") == "scan":
        word = "Breaches found" if a.get("outcome") == "VIOLATION" else "All clear"
    return word, color


def answer_card(result: Dict[str, Any]) -> None:
    a = result["answer"]
    word, color = _outcome(a)
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
        f"<div class='verdict' style='--glow:{_hex_to_rgba(color, 0.18)};grid-template-columns:1fr'>"
        f"<div><div class='claim-quote'>“{escape(a.get('question') or result.get('claim_text') or '')}”</div>"
        f"<div class='status-word' style='color:{color}'>{escape(word.upper())}</div>"
        f"<div class='headline'>{escape(a.get('headline') or '')}</div>"
        f"<p style='margin:0 0 .9rem;line-height:1.6;font-size:1.06rem;color:#E4E4E7'>{escape(a.get('answer') or '')}</p>"
        f"<div class='chips'>{''.join(chips)}</div></div></div>",
        unsafe_allow_html=True,
    )


def answer_why(a: Dict[str, Any]) -> None:
    reasons = a.get("reasons") or []
    facts = a.get("facts") or []
    if reasons:
        st.markdown(
            "<div class='panel'><h4>Why</h4><ul>" + "".join(f"<li>{escape(r)}</li>" for r in reasons) + "</ul></div>",
            unsafe_allow_html=True)
    if a.get("caveats"):
        st.markdown("<div class='callout'><b>Keep in mind.</b> " + " ".join(escape(c) for c in a["caveats"]) + "</div>",
                    unsafe_allow_html=True)
    if facts:
        rows = "".join(f"<div style='display:grid;grid-template-columns:170px 1fr;gap:.8rem;padding:.3rem 0;"
                       f"border-bottom:1px solid #222'><span class='muted'>{escape(k)}</span><span>{escape(v)}</span></div>"
                       for k, v in facts)
        st.markdown(f"<div class='panel'><h4>The record</h4>{rows}</div>", unsafe_allow_html=True)


def scan_items(a: Dict[str, Any], key: str) -> None:
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


def answer_evidence(a: Dict[str, Any], key: str) -> None:
    ev = a.get("evidence") or []
    if not ev:
        return
    st.markdown(f"<div class='col-title' style='margin-top:.6rem'>The evidence <span class='count'>({len(ev)})</span></div>",
                unsafe_allow_html=True)
    cols = st.columns(2, gap="large")
    for i, e in enumerate(ev):
        tag, edge = ROLE_STYLE.get(e.get("role"), ("Source", "#9CA3AF"))
        flags = ""
        if e.get("status") == "superseded":
            flags += " <span class='tag' style='background:#3A1D1D;color:#FCA5A5'>Superseded</span>"
        if e.get("status") == "draft":
            flags += " <span class='tag' style='background:#3A2E12;color:#FCD34D'>Draft</span>"
        note = e.get("note") or ""
        with cols[i % 2]:
            st.markdown(
                f"<div class='ev' style='--edge:{edge}'>"
                f"<div class='ev-top'><div><span class='ev-id'>{escape(e['doc_id'])}</span>"
                + (f"<span class='ev-id' style='color:#FF6A12'> · {escape(e['section'])}</span>" if e.get("section") else "")
                + " "
                f"<span class='ev-src'>· {escape(e.get('type') or '')}{' · ' + escape(e['date']) if e.get('date') else ''}</span>"
                f"{flags}</div><span class='tag' style='background:#232327;color:{edge}'>{escape(tag)}</span></div>"
                + (f"<div class='ev-why'>{escape(note)}</div>" if note and note != tag else "")
                + f"<div class='ev-quote'>“{escape(e.get('quote') or '')}”</div>"
                f"<div class='wlbl'><span>{escape(e.get('title') or '')}</span><span>{escape(e.get('path') or '')}</span></div>"
                f"</div>", unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            if c1.button("Preview file", key=f"apv_{key}_{i}", icon=":material/visibility:", use_container_width=True):
                preview_dialog(e["doc_id"], e.get("section_id"))
            with c2:
                download_button(e["doc_id"], key=f"adl_{key}_{i}")


def render_result(result: Dict[str, Any], key: str) -> None:
    a = result.get("answer")
    if a is None:  # older results: the full assessment view
        _full_assessment(result, key)
        feedback_box(result, key)
        return
    answer_card(result)
    answer_why(a)
    scan_items(a, key)
    answer_evidence(a, key)
    if result.get("scored"):
        with st.expander("Full details: how the evidence was weighed", icon=":material/fact_check:"):
            _full_assessment(result, key)
    feedback_box(result, key)


def _full_assessment(result: Dict[str, Any], key: str) -> None:
    for n in result.get("notices") or []:
        st.warning(n, icon=":material/info:")
    verdict_card(result)
    why_panel(result)
    evidence_columns(result, key)
    with st.expander("Full evidence trail", icon=":material/timeline:"):
        trail(result, key)
    # Hidden in the UI (data still produced by the backend); uncomment to show again.
    # with st.expander("How this result was produced", icon=":material/settings_suggest:"):
    #     production_details(result)


def compact_card(result: Dict[str, Any]) -> None:
    a = result.get("answer")
    if a is not None:
        word, color = _outcome(a)
        ids = list(dict.fromkeys(e["doc_id"] for e in (a.get("evidence") or [])))[:4]
        st.markdown(
            f"<div class='panel' style='border-left:4px solid {color}'>"
            f"<div style='display:flex;align-items:center;gap:1rem;flex-wrap:wrap'>"
            f"<span style='font-size:1.6rem;font-weight:900;color:{color};letter-spacing:-0.03em'>{escape(word.upper())}</span>"
            f"<span class='chip'>{escape(KIND_NOTE.get(a.get('kind'), '') or 'Answer')}</span></div>"
            f"<p style='font-size:1.08rem;font-weight:600;margin:.7rem 0 .4rem;line-height:1.5'>{escape(a.get('headline') or '')}</p>"
            f"<p style='margin:0 0 .5rem;line-height:1.55;color:#E4E4E7'>{escape(a.get('answer') or '')}</p>"
            f"<div class='note'>Evidence: {escape(', '.join(ids) or 'none')}</div></div>",
            unsafe_allow_html=True,
        )
        return
    sc = result["scored"]
    color = STATUS_COLORS.get(sc["status"], "#9CA3AF")
    exp = result.get("explanation") or {}
    sup = [e["cite_id"] for e in sorted(result["evidence"], key=lambda e: -e["final_weight"]) if e["stance"] == "support"][:3]
    con = [e["cite_id"] for e in sorted(result["evidence"], key=lambda e: -e["final_weight"]) if e["stance"] == "contradict"][:3]
    st.markdown(
        f"<div class='panel' style='border-left:4px solid {color}'>"
        f"<div style='display:flex;align-items:center;gap:1rem;flex-wrap:wrap'>"
        f"<span style='font-size:1.6rem;font-weight:900;color:{color};letter-spacing:-0.03em'>{escape(sc['status'].upper())}</span>"
        f"<span class='chip'>How sure: <b>{escape(sc['confidence'])}</b></span></div>"
        f"<p style='font-size:1.08rem;font-weight:600;margin:.7rem 0 .5rem;line-height:1.5'>{escape(exp.get('headline') or result['plain']['status'])}</p>"
        f"<div class='note'><span style='color:{STANCE_COLORS['support']}'>●</span> Supported by: {escape(', '.join(sup) or 'nothing')} &nbsp; "
        f"<span style='color:{STANCE_COLORS['contradict']}'>●</span> Against: {escape(', '.join(con) or 'nothing')}</div></div>",
        unsafe_allow_html=True,
    )
