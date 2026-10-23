"""Raw source file preview (dialog) and download, with the cited section highlighted."""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

import client

_TAG_SUPERSEDED = "<span class='tag' style='background:#3A1D1D;color:#FCA5A5'>Superseded</span>"
_TAG_DRAFT = "<span class='tag' style='background:#3A2E12;color:#FCD34D'>Draft · not approved</span>"
_HIGHLIGHT = "background-color: rgba(255,106,18,.28)"
_FOCUS_CHARS = 2500


def download_button(doc_id: str, key: str, label: str = "Download") -> None:
    try:
        data = client.raw_bytes(doc_id)
        meta = client.preview(doc_id)
    except client.ApiError as e:
        st.caption(f"Download unavailable: {e}")
        return
    mime = "text/csv" if meta["format"] == "csv" else "text/markdown"
    st.download_button(label, data=data, file_name=meta["title"], mime=mime, key=key,
                       width="stretch", on_click="ignore", icon=":material/download:")


def _header(p: dict) -> None:
    tags = ("" + (_TAG_SUPERSEDED if p.get("superseded") else "") + (_TAG_DRAFT if p.get("draft") else ""))
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:.6rem;flex-wrap:wrap'>"
        f"<span style='font-size:1.4rem;font-weight:800'>{escape(p['doc_id'])}</span>"
        f"<span class='pill'>{escape(p['source_label'])}</span>{tags}</div>"
        f"<div class='note' style='margin:.3rem 0 .6rem'>{escape(p['title'])} · <code>{escape(p['path'])}</code></div>",
        unsafe_allow_html=True)


def _cited_section(focus: dict) -> None:
    heading = f" · {escape(focus['heading'])}" if focus.get("heading") else ""
    st.markdown(
        f"<div class='focusbox'><div class='note' style='margin-bottom:.3rem'>Cited section · "
        f"<b style='color:#FF6A12'>{escape(focus['cite_id'])}</b>{heading}</div>"
        f"<div style='white-space:pre-wrap;font-size:.92rem;line-height:1.5'>{escape(focus['text'][:_FOCUS_CHARS])}</div></div>",
        unsafe_allow_html=True)


def _csv_table(rows: list[dict], focus: dict | None) -> None:
    df = pd.DataFrame(rows)
    st.caption(f"{len(df)} rows · {len(df.columns)} columns" + (" · cited row highlighted" if focus else ""))
    if focus and focus.get("row_index") is not None:
        cited = int(focus["row_index"])
        st.dataframe(df.style.apply(lambda row: [_HIGHLIGHT if row.name == cited else ""] * len(row), axis=1),
                     width="stretch", height=420)
    else:
        st.dataframe(df, width="stretch", height=420)


@st.dialog("Source file", width="large")
def preview_dialog(doc_id: str, chunk_id: str | None = None) -> None:
    try:
        p = client.preview(doc_id, chunk_id)
    except client.ApiError as e:
        st.error(str(e))
        return
    _header(p)
    focus = p.get("focus")
    if focus:
        _cited_section(focus)
    if p["format"] == "csv" and p.get("rows"):
        _csv_table(p["rows"], focus)
    else:
        st.caption("Full document")
        with st.container(height=440, border=True):
            st.markdown(p["text"])
    download_button(doc_id, key=f"dlg_dl_{doc_id}_{chunk_id}", label=f"Download {p['title']}")
