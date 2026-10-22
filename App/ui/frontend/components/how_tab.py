"""Tab 4: How it works, in plain language, plus live system status."""
from __future__ import annotations

from html import escape

import pandas as pd
import plotly.express as px
import streamlit as st

import api_client as api
from theme import ORANGE, STATUS_COLORS

STAGES = [
    ("Collect", "All 45 Northfield files (policies, process docs, interviews, meetings, Slack and email, CRM, tasks, audit log) "
                "are split into 743 searchable sections. Each keeps its source, date, and whether it is a draft or out of date."),
    ("Make it testable", "Your sentence becomes one claim with a who, a what and a when. In Ask, the local model rewrites your question into that shape."),
    ("Find evidence", "Two searches run together: keyword search catches exact terms like “15 percent” or “DEAL-014”; "
                      "meaning search catches the same idea in different words. Their rankings are merged."),
    ("Check each piece", "Rules and an NLI model read every excerpt and say whether it supports the claim, goes against it, or is not relevant, "
                         "with a one-line reason and the exact quote. Not-relevant excerpts are dropped."),
    ("Weigh the sources", "An approved policy counts more than a Slack message. Out-of-date and draft files count less. "
                          "Near-identical copies (like the same workaround pasted into three Slack threads) share one vote."),
    ("Score", "A fixed Python formula turns the weighted evidence into a score from 0 to 1 and a verdict. "
              "No model sets or changes the number."),
    ("Check honesty", "The app compares its scores with what turned out to be true. This only runs once at least 30 labels exist."),
    ("Explain", "A fixed template writes the plain-English explanation from the evidence: what supports the claim, "
                "what goes against it and what is missing. It cannot change the verdict or the score."),
]


def render(health: dict) -> None:
    st.markdown(
        "<div class='hero-q'><h1>How it works</h1>"
        "<p>Company files often disagree. This app shows which ones agree, which ones don't, and how much each should count.</p></div>",
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.35, 1], gap="large")
    with left:
        st.markdown("<div class='col-title'>The eight steps behind every answer</div>", unsafe_allow_html=True)
        for i, (t, s) in enumerate(STAGES, start=1):
            st.markdown(f"<div class='step'><div class='n'>{i}</div><div><div class='t'>{escape(t)}</div>"
                        f"<div class='s'>{escape(s)}</div></div></div>", unsafe_allow_html=True)
    with right:
        meta = api.meta()
        st.markdown("<div class='col-title'>What the verdicts mean</div>", unsafe_allow_html=True)
        for s in meta["statuses"]:
            c = STATUS_COLORS[s]
            st.markdown(f"<div class='card' style='border-left:4px solid {c}'><div class='t' style='color:{c}'>{escape(s)}</div>"
                        f"<div class='s' style='color:#D4D4D8'>{escape(meta['status_plain'][s])}</div></div>", unsafe_allow_html=True)
        st.markdown("<div class='col-title' style='margin-top:1rem'>How much each kind of source counts</div>", unsafe_allow_html=True)
        w = meta["authority_weights"]
        df = pd.DataFrame({"source": [meta["source_plain"].get(k, k) for k in w], "weight": list(w.values())}).sort_values("weight")
        fig = px.bar(df, x="weight", y="source", orientation="h", color_discrete_sequence=[ORANGE])
        fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#E4E4E7"),
                          margin=dict(l=0, r=10, t=10, b=10), height=330, xaxis_title="", yaxis_title="")
        fig.update_xaxes(gridcolor="#222")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.caption("Authority for rule claims, from config/credibility.yaml. Superseded files ×0.2, possibly stale procedures ×0.6, "
                   "undated files ×0.85, past their review date ×0.9.")

    # System status panel hidden in the UI; uncomment the block below to show it again.
    # st.markdown("<div class='col-title' style='margin-top:1rem'>System status</div>", unsafe_allow_html=True)
    # prof = api.profile()
    # usage = api.llm_usage()
    # calls = sum(r["n"] for r in usage["by_model"])
    # cached = sum(r["cached"] or 0 for r in usage["by_model"])
    # rows = [
    #     ("Model", ("Connected: " + ", ".join(f"{p} ({health['models'][p]})" for p in health["providers"]))
    #      if health["llm_ready"] else "Not connected. Add a free Gemini or Groq key in Settings."),
    #     ("Search", ", ".join(prof["retrieval_modes"]) + (f" · embeddings {prof['embedding_model']} (runs locally)" if prof.get("embedding_model") else "")),
    #     ("Data", f"{prof['documents']} files · {prof['chunks']} sections · assessed {prof['assessment_date']}"),
    #     ("AI calls so far", f"{calls} ({cached} answered from cache, costing nothing)"),
    # ]
    # if usage.get("last_error"):
    #     rows.append(("Last AI error", str(usage["last_error"].get("error"))[:200]))
    # st.markdown("<div class='panel'>" + "".join(
    #     f"<div style='display:grid;grid-template-columns:200px 1fr;gap:.8rem;padding:.35rem 0;border-bottom:1px solid #222'>"
    #     f"<span class='muted'>{escape(k)}</span><span>{escape(v)}</span></div>" for k, v in rows) + "</div>",
    #     unsafe_allow_html=True)
    # st.caption("Backend API docs: http://127.0.0.1:8000/docs")
