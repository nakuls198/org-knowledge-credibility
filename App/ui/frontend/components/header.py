"""Top bar: brand, live AI status, and a Settings popover for API keys."""
from __future__ import annotations

from html import escape
from typing import Any, Dict

import streamlit as st

import api_client as api


def ai_status_pill(h: Dict[str, Any]) -> str:
    if h.get("llm_ready"):
        gem = h.get("gemini_models") or []
        free_gem = [m["model"] for m in gem if m["available"]]
        has_groq = "groq" in h["providers"]
        if gem and not free_gem and not has_groq:
            wait = min(m["resumes_in_min"] for m in gem)
            return ("<span class='pill'><span class='dot' style='background:#F59E0B'></span>"
                    f"Model resting · free daily quota used, back in ~{wait // 60}h · add a Groq key for backup</span>")
        model = free_gem[0] if free_gem else h["models"].get(h["providers"][0], "")
        backup = " + Groq backup" if has_groq and free_gem else ""
        return f"<span class='pill'><span class='dot' style='background:#22C55E'></span>Model: {escape(model)}{backup}</span>"
    return "<span class='pill'><span class='dot' style='background:#F59E0B'></span>Model not connected</span>"


def settings_popover(h: Dict[str, Any]) -> None:
    with st.popover("Settings", icon=":material/tune:", use_container_width=True):
        st.markdown("**AI provider keys** (free tiers)")
        st.caption(
            "Gemini: get a free key at aistudio.google.com/apikey. "
            "Groq (backup): console.groq.com/keys. Keys are saved to the local .env file only."
        )
        gk = st.text_input("Gemini API key", type="password",
                           placeholder="saved" if h["keys"]["gemini"] else "paste key", key="set_gemini")
        qk = st.text_input("Groq API key (backup)", type="password",
                           placeholder="saved" if h["keys"]["groq"] else "paste key", key="set_groq")
        gm = st.text_input("Gemini model", value=h["models"]["gemini"], key="set_gm")
        qm = st.text_input("Groq model", value=h["models"]["groq"], key="set_qm")
        if st.button("Save", type="primary", use_container_width=True, key="set_save"):
            try:
                r = api.save_keys(gemini_api_key=gk, groq_api_key=qk, gemini_model=gm, groq_model=qm)
                st.success(f"Saved {', '.join(r['saved'])}. Connected providers: {', '.join(r['providers']) or 'none'}")
            except api.ApiError as e:
                st.error(str(e))
        c1, c2 = st.columns(2)
        for col, prov in ((c1, "gemini"), (c2, "groq")):
            if col.button(f"Test {prov.title()}", use_container_width=True, key=f"ping_{prov}",
                          disabled=not h["keys"][prov]):
                with st.spinner("Calling…"):
                    r = api.ping(prov)
                if r.get("ok"):
                    col.success(f"{r['model']} · {r['latency_ms']:.0f} ms")
                else:
                    col.error(r.get("error", "failed")[:300])


def topbar() -> Dict[str, Any]:
    try:
        h = api.health()
    except api.ApiError as e:
        st.error(str(e), icon=":material/cloud_off:")
        st.stop()
    left, mid, right = st.columns([3.2, 3.3, 1])
    left.markdown(
        "<div class='topbar'><div class='brand'><div class='logo'>N</div>Northfield AI</div></div>", unsafe_allow_html=True)
    mid.markdown(f"<div class='topbar' style='justify-content:flex-end'>{ai_status_pill(h)}</div>", unsafe_allow_html=True)
    with right:
        settings_popover(h)
    return h
