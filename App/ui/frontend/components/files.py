"""Raw source file preview (dialog) and download, with the cited section highlighted."""
from __future__ import annotations

from html import escape
from typing import Optional

import pandas as pd
import streamlit as st

import api_client as api


def download_button(doc_id: str, key: str, label: str = "Download", use_container_width: bool = True) -> None:
    try:
        data = api.raw_bytes(doc_id)
        meta = api.preview(doc_id)
    except api.ApiError as e:
        st.caption(f"Download unavailable: {e}")
        return
    mime = "text/csv" if meta["format"] == "csv" else "text/markdown"
    st.download_button(
        label, data=data, file_name=meta["title"], mime=mime, key=key,
        use_container_width=use_container_width, on_click="ignore", icon=":material/download:",
    )


@st.dialog("Source file", width="large")
def preview_dialog(doc_id: str, chunk_id: Optional[str] = None) -> None:
    try:
        p = api.preview(doc_id, chunk_id)
    except api.ApiError as e:
        st.error(str(e))
        return

    tags = []
    if p.get("superseded"):
        tags.append("<span class='tag' style='background:#3A1D1D;color:#FCA5A5'>Superseded</span>")
    if p.get("draft"):
        tags.append("<span class='tag' style='background:#3A2E12;color:#FCD34D'>Draft · not approved</span>")
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:.6rem;flex-wrap:wrap'>"
        f"<span style='font-size:1.4rem;font-weight:800'>{escape(p['doc_id'])}</span>"
        f"<span class='pill'>{escape(p['source_label'])}</span>{''.join(tags)}</div>"
        f"<div class='note' style='margin:.3rem 0 .6rem'>{escape(p['title'])} · <code>{escape(p['path'])}</code></div>",
        unsafe_allow_html=True,
    )

    focus = p.get("focus")
    if focus:
        st.markdown(
            f"<div class='focusbox'><div class='note' style='margin-bottom:.3rem'>Cited section · "
            f"<b style='color:#FF6A12'>{escape(focus['cite_id'])}</b>"
            f"{' · ' + escape(focus['heading']) if focus.get('heading') else ''}</div>"
            f"<div style='white-space:pre-wrap;font-size:.92rem;line-height:1.5'>{escape(focus['text'][:2500])}</div></div>",
            unsafe_allow_html=True,
        )

    if p["format"] == "csv" and p.get("rows"):
        df = pd.DataFrame(p["rows"])
        st.caption(f"{len(df)} rows · {len(df.columns)} columns" + (" · cited row highlighted" if focus else ""))
        if focus and focus.get("row_index") is not None:
            idx = int(focus["row_index"])

            def hl(row):
                color = "background-color: rgba(255,106,18,.28)" if row.name == idx else ""
                return [color] * len(row)

            st.dataframe(df.style.apply(hl, axis=1), use_container_width=True, height=420)
        else:
            st.dataframe(df, use_container_width=True, height=420)
    else:
        st.caption("Full document")
        with st.container(height=440, border=True):
            st.markdown(p["text"])

    download_button(doc_id, key=f"dlg_dl_{doc_id}_{chunk_id}", label=f"Download {p['title']}")
