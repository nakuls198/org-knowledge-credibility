"""Tab 4: How it works, in plain language."""
from __future__ import annotations

from html import escape

import pandas as pd
import plotly.express as px
import streamlit as st

import client
from theme import ORANGE, STATUS_COLORS


def _steps(files: int, sections: int) -> list[tuple[str, str]]:
    return [
        ("Collect", f"All {files} Northfield files (policies, process docs, interviews, meetings, Slack and email, CRM, tasks, audit log) "
                    f"are split into {sections} searchable sections. Each keeps its source, date, and whether it is a draft or out of date."),
        ("Make it testable", "Your sentence becomes one claim with a who, a what and a when. In Ask, the same checks turn your question into that shape."),
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


def _factors_caption(temporal: dict) -> str:
    return (f"Authority for rule claims, from config/credibility.yaml. Superseded files ×{temporal['superseded_current_claim']:g}, "
            f"possibly stale procedures ×{temporal['potentially_stale']:g}, undated files ×{temporal['undated']:g}, "
            f"past their review date ×{temporal['review_overdue']:g}.")


def _authority_chart() -> None:
    weights = client.authority_weights()
    df = pd.DataFrame({"source": [client.SOURCE_PLAIN.get(k, k) for k in weights], "weight": list(weights.values())}).sort_values("weight")
    fig = px.bar(df, x="weight", y="source", orientation="h", color_discrete_sequence=[ORANGE])
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#E4E4E7"),
                      margin=dict(l=0, r=10, t=10, b=10), height=330, xaxis_title="", yaxis_title="")
    fig.update_xaxes(gridcolor="#222")
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption(_factors_caption(client.credibility_config()["temporal"]))


def render(health: dict) -> None:
    st.markdown(
        "<div class='hero-q'><h1>How it works</h1>"
        "<p>Company files often disagree. This app shows which ones agree, which ones don't, and how much each should count.</p></div>",
        unsafe_allow_html=True)

    profile = client.corpus_profile()
    left, right = st.columns([1.35, 1], gap="large")
    with left:
        st.markdown("<div class='col-title'>The eight steps behind every answer</div>", unsafe_allow_html=True)
        for i, (title, text) in enumerate(_steps(profile["source_files"], profile["sections"]), start=1):
            st.markdown(f"<div class='step'><div class='n'>{i}</div><div><div class='t'>{escape(title)}</div>"
                        f"<div class='s'>{escape(text)}</div></div></div>", unsafe_allow_html=True)
    with right:
        meanings = client.verdict_meanings()
        st.markdown("<div class='col-title'>What the verdicts mean</div>", unsafe_allow_html=True)
        for status in meanings["statuses"]:
            c = STATUS_COLORS[status]
            st.markdown(f"<div class='card' style='border-left:4px solid {c}'><div class='t' style='color:{c}'>{escape(status)}</div>"
                        f"<div class='s' style='color:#D4D4D8'>{escape(meanings['status_plain'][status])}</div></div>",
                        unsafe_allow_html=True)
        st.markdown("<div class='col-title' style='margin-top:1rem'>How much each kind of source counts</div>", unsafe_allow_html=True)
        _authority_chart()
