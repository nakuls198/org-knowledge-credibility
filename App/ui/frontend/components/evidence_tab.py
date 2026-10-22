"""Tab 3: Evidence. Source files (preview/download), tested claims, accuracy check."""
from __future__ import annotations

from html import escape

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import api_client as api
from components.files import download_button, preview_dialog
from components.verdict import f5, render_result
from theme import ORANGE, STATUS_COLORS

PLOT = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#E4E4E7", family="Inter"),
            margin=dict(l=10, r=10, t=40, b=10), legend=dict(bgcolor="rgba(0,0,0,0)"))


def _style(fig):
    fig.update_layout(**PLOT)
    fig.update_xaxes(gridcolor="#222", zeroline=False)
    fig.update_yaxes(gridcolor="#222", zeroline=False)
    return fig


def _kpis(items) -> None:
    st.markdown("<div class='kpis'>" + "".join(
        f"<div class='kpi'><div class='v'>{escape(str(v))}</div><div class='l'>{escape(l)}</div></div>" for v, l in items
    ) + "</div>", unsafe_allow_html=True)


# ---- source files -----------------------------------------------------------

@st.dialog("Claims found in this file", width="large")
def _claims_dialog(doc_id: str) -> None:
    with st.spinner("Reading the file and pulling out testable claims…"):
        try:
            r = api.extract_claims(doc_id)
        except api.ApiError as e:
            st.error(str(e))
            return
    st.caption(f"Extracted by {r['model']} ({r['provider']})" + (" · long file, first part read" if r.get("truncated") else ""))
    if not r["claims"]:
        st.info("No testable claims found in this file.")
    for i, c in enumerate(r["claims"]):
        st.markdown(
            f"<div class='card'><div class='t'>{escape(c['claim'])}</div>"
            f"<div class='s'>{escape(c.get('role') or '')} · {escape(c.get('layer') or '')}</div>"
            + (f"<div class='ev-quote'>“{escape(c['quote'])}”</div>" if c.get("quote") else "")
            + "</div>", unsafe_allow_html=True)
        if st.button("Check this claim", key=f"xc_{doc_id}_{i}"):
            st.session_state.pending_claim = c["claim"]
            st.session_state.run_now = True
            st.rerun()


def _files(health: dict) -> None:
    prof = api.profile()
    _kpis([(prof["documents"], "Source files"), (prof["chunks"], "Searchable sections"),
           (prof["people"], "People on the roster"), (prof["gold_claims"], "Hand-checked claims")])

    docs = api.documents()
    types = sorted({d["source_label"] for d in docs})
    c1, c2 = st.columns([2, 1])
    q = c1.text_input("Search files", placeholder="Search by id or file name, e.g. POL-01, crm, interview",
                      label_visibility="collapsed")
    sel = c2.multiselect("Type", types, placeholder="All types", label_visibility="collapsed")
    view = [d for d in docs if (not sel or d["source_label"] in sel)
            and (not q or q.lower() in d["doc_id"].lower() or q.lower() in (d["title"] or "").lower())]
    st.caption(f"{len(view)} of {len(docs)} files · originals from data/raw/phase1 and data/raw/phase2")

    cols = st.columns(3)
    for i, d in enumerate(view):
        flags = ""
        if d["superseded"]:
            flags += " <span class='tag' style='background:#3A1D1D;color:#FCA5A5'>Superseded</span>"
        if d["draft"]:
            flags += " <span class='tag' style='background:#3A2E12;color:#FCD34D'>Draft</span>"
        dates = ""
        if d.get("first_date"):
            dates = d["first_date"] if d["first_date"] == d.get("last_date") else f"{d['first_date']} → {d['last_date']}"
        with cols[i % 3]:
            st.markdown(
                f"<div class='card'><div class='t'>{escape(d['doc_id'])}{flags}</div>"
                f"<div class='s'>{escape(d['source_label'])} · {d['n_chunks']} sections · {escape(d['phase'])}</div>"
                f"<div class='s' style='margin-top:.2rem'>{escape(d['title'])}</div>"
                f"<div class='s'>{escape(dates)}</div></div>", unsafe_allow_html=True)
            b1, b2 = st.columns(2)
            if b1.button("Preview", key=f"fpv_{d['doc_id']}", icon=":material/visibility:", use_container_width=True):
                preview_dialog(d["doc_id"])
            with b2:
                download_button(d["doc_id"], key=f"fdl_{d['doc_id']}")
            if health.get("llm_ready") and st.button("Find claims in this file", key=f"fx_{d['doc_id']}",
                                                      icon=":material/auto_awesome:", use_container_width=True):
                _claims_dialog(d["doc_id"])

    with st.expander("People on the roster", icon=":material/groups:"):
        st.dataframe(pd.DataFrame(api.roster()), use_container_width=True, hide_index=True, height=360)


# ---- tested claims ------------------------------------------------------------

@st.dialog("Result", width="large")
def _result_dialog(claim_text: str, claim_id: str) -> None:
    with st.spinner("Checking…"):
        try:
            r = api.check({"claim": claim_text, "claim_id": claim_id})
        except api.ApiError as e:
            st.error(str(e))
            return
    render_result(r, key=f"gold_{claim_id}")


def _gold() -> None:
    gold = api.gold()["claims"]
    try:
        latest = {}
        for r in api.runs(200):
            latest.setdefault(" ".join(r["claim_text"].lower().split()), r)
    except api.ApiError:
        latest = {}
    st.markdown(
        "<div class='note' style='margin-bottom:.8rem'>Claims the team answered by hand. "
        "Each card shows the team's answer and the app's most recent answer, so you can see where they agree.</div>",
        unsafe_allow_html=True)
    layers = ["All"] + sorted({g["layer"] for g in gold})
    layer = st.segmented_control("Layer", layers, default="All", key="gold_layer", label_visibility="collapsed")
    items = [g for g in gold if layer in (None, "All") or g["layer"] == layer]
    cols = st.columns(3)
    for i, g in enumerate(items):
        exp_c = STATUS_COLORS.get(g["expected_status"], "#9CA3AF")
        last = latest.get(" ".join(g["claim_text"].lower().split()))
        last_html = "<span class='note'>Not checked yet</span>"
        if last:
            lc = STATUS_COLORS.get(last["status"], "#9CA3AF")
            mark = "✓" if last["status"] == g["expected_status"] else "≠"
            last_html = f"<span style='color:{lc};font-weight:700'>App: {escape(last['status'])} {mark}</span> <span class='note'>{f5(last['score'])}</span>"
        with cols[i % 3]:
            st.markdown(
                f"<div class='card'><div class='s'>{escape(g['claim_id'])} · {escape(g['process'])} · {escape(g['layer'])}</div>"
                f"<div class='t' style='margin:.35rem 0 .5rem;font-weight:600;line-height:1.45'>{escape(g['claim_text'])}</div>"
                f"<div><span style='color:{exp_c};font-weight:700'>Team: {escape(g['expected_status'])}</span></div>"
                f"<div style='margin-top:.2rem'>{last_html}</div></div>", unsafe_allow_html=True)
            if st.button("Check now", key=f"gchk_{g['claim_id']}", use_container_width=True, icon=":material/verified:"):
                _result_dialog(g["claim_text"], g["claim_id"])


# ---- accuracy check -------------------------------------------------------------

def _eval_results(job: dict) -> None:
    res = job.get("result") or {}
    summaries = res.get("summaries", [])
    if not summaries:
        return
    st.markdown(f"<div class='col-title'>Results on {summaries[0]['n']} hand-checked claims · top {res.get('top_k')} evidence</div>",
                unsafe_allow_html=True)
    cols = st.columns(len(summaries))
    best = max(summaries, key=lambda s: (s["status_match"] or 0))
    for col, s in zip(cols, summaries):
        star = " · best" if s is best else ""
        col.markdown(
            f"<div class='card' style='{'border-color:' + ORANGE if s is best else ''}'>"
            f"<div class='t'>{escape(s['config'])}{star}</div>"
            f"<div class='s'>Evidence check used: {escape(', '.join(s['engine_used']))}</div>"
            f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:.5rem;margin-top:.6rem'>"
            f"<div><div class='kpi' style='padding:.6rem'><div class='v' style='font-size:1.25rem'>{f5(s['recall_at_k'])}</div><div class='l'>Files found</div></div></div>"
            f"<div><div class='kpi' style='padding:.6rem'><div class='v' style='font-size:1.25rem'>{f5(s['status_match'])}</div><div class='l'>Exact verdict</div></div></div>"
            f"<div><div class='kpi' style='padding:.6rem'><div class='v' style='font-size:1.25rem'>{f5(s['lean_match'])}</div><div class='l'>Right direction</div></div></div>"
            f"</div></div>", unsafe_allow_html=True)

    df = pd.DataFrame(summaries)
    long = df.melt(id_vars="config", value_vars=["recall_at_k", "status_match", "lean_match"], var_name="metric", value_name="value")
    long["metric"] = long["metric"].map({"recall_at_k": "Expected files found", "status_match": "Exact verdict match",
                                         "lean_match": "Right direction"})
    fig = px.bar(long, x="metric", y="value", color="config", barmode="group",
                 color_discrete_sequence=["#52525B", "#A1A1AA", ORANGE, "#2DD4BF"])
    fig.update_layout(title="Before vs after", yaxis_range=[0, 1], legend_title_text="")
    st.plotly_chart(_style(fig), use_container_width=True, config={"displayModeBar": False})

    rows = pd.DataFrame(res.get("rows", []))
    if not rows.empty:
        with st.expander("Per-claim detail", icon=":material/table:"):
            show = rows[["config", "claim_id", "layer", "expected_status", "actual_status", "score", "recall", "status_match"]].copy()
            show["score"] = show["score"].map(f5)
            show["recall"] = show["recall"].map(lambda x: "—" if pd.isna(x) else f5(x))
            st.dataframe(show, use_container_width=True, hide_index=True, height=420)


def _calibration() -> None:
    try:
        cal = api.calibration()
    except api.ApiError as e:
        st.error(str(e))
        return
    n, need = cal["n_labels"], cal["needed"]
    st.markdown("<div class='col-title' style='margin-top:1rem'>Calibration: does 0.8 mean right 4 times in 5?</div>",
                unsafe_allow_html=True)
    st.progress(min(1.0, n / need), text=cal["message"])
    st.caption("Labels come from the team's hand-checked claims plus every “Was this right?” answer saved in the app.")
    if cal.get("reliability") and n:
        rel = pd.DataFrame(cal["reliability"])
        rel = rel[rel["n"] > 0]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", name="Perfect", line=dict(color="#52525B", dash="dot")))
        fig.add_trace(go.Scatter(x=rel["avg_predicted"], y=rel["avg_actual"], mode="lines+markers", name="This app",
                                 line=dict(color=ORANGE, width=3), marker=dict(size=10 + rel["n"])))
        fig.update_layout(title=("Reliability" if cal["ready"] else "Reliability (preview, not enough labels yet)"),
                          xaxis_title="Score the app gave", yaxis_title="Share actually true",
                          xaxis_range=[0, 1], yaxis_range=[0, 1])
        c1, c2 = st.columns([2, 1])
        c1.plotly_chart(_style(fig), use_container_width=True, config={"displayModeBar": False})
        c2.markdown(
            f"<div class='panel'><h4>{'Calibration' if cal['ready'] else 'Preview only'}</h4>"
            f"<p>Brier score <b>{f5(cal.get('preview_brier'))}</b><br><span class='muted'>0 is perfect; 0.25 is coin-flipping.</span></p>"
            f"<p>Calibration error <b>{f5(cal.get('preview_ece'))}</b><br><span class='muted'>Average gap between score and reality.</span></p></div>",
            unsafe_allow_html=True)


def _accuracy(health: dict) -> None:
    st.markdown(
        "<div class='note' style='margin-bottom:.8rem'>Runs every hand-checked claim through each setup (keyword search with the "
        "keyword baseline, hybrid search with the keyword baseline, hybrid search with the rules + NLI model) and compares the app's answers to "
        "the team's. This is the before/after evidence that the NLI model earns its place.</div>",
        unsafe_allow_html=True)
    if not health.get("llm_ready"):
        st.info("Start the NLI model to include the rules + NLI setup. Until then only the two keyword-baseline setups run.")
    if st.session_state.get("eval_failed"):
        st.error(f"The last evaluation failed: {st.session_state.pop('eval_failed')}")
    c1, c2 = st.columns([1, 3])
    top_k = c1.slider("Evidence per claim", 5, 30, 15, key="eval_k")
    if c2.button("Run accuracy check", type="primary", key="eval_go", icon=":material/science:"):
        try:
            st.session_state.eval_job = api.start_eval(top_k)["job_id"]
        except api.ApiError as e:
            st.error(str(e))

    if st.session_state.get("eval_job"):
        _job_progress()
    else:
        latest = api.eval_latest()
        if latest:
            st.caption("Showing the most recent finished run.")
            _eval_results(latest)
    _calibration()


@st.fragment(run_every=2)
def _job_progress() -> None:
    jid = st.session_state.get("eval_job")
    if not jid:
        return
    job = api.eval_job(jid)
    if job["status"] in {"queued", "running"}:
        st.progress(job["progress"] / max(1, job["total"]),
                    text=f"{job['progress']} of {job['total']} · {job.get('message') or ''}")
        return
    st.session_state.eval_job = None
    if job["status"] == "failed":
        st.session_state.eval_failed = job.get("message")
    st.rerun()


def render(health: dict) -> None:
    # "Accuracy check" is hidden in the UI; add it back to the list and uncomment the branch below to restore.
    view = st.segmented_control("Section", ["Source files", "Tested claims"],
                                default="Source files", key="ev_view", label_visibility="collapsed")
    if view == "Tested claims":
        _gold()
    # elif view == "Accuracy check":
    #     _accuracy(health)
    else:
        _files(health)
