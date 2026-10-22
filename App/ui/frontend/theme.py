"""Visual theme: near-black canvas, vivid orange accent, Chrome-style header tabs."""

ORANGE = "#FF6A12"

STATUS_COLORS = {
    "Supported": "#22C55E",
    "Likely": "#2DD4BF",
    "Conflicted": "#F59E0B",
    "Unlikely": "#FB7185",
    "Unsupported": "#EF4444",
    "Not enough evidence": "#9CA3AF",
}

STANCE_COLORS = {"support": "#22C55E", "contradict": "#EF4444", "unrelated": "#6B7280"}

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root { --orange:#FF6A12; --bg:#0B0B0C; --panel:#141416; --panel2:#1B1B1E; --line:#26262A; --muted:#9A9AA2; --text:#F4F4F5; }

html, body, .stApp, [data-testid="stAppViewContainer"] { background: var(--bg) !important; color: var(--text); font-family: 'Inter', sans-serif; }
[data-testid="stSidebar"], [data-testid="collapsedControl"], [data-testid="stSidebarCollapsedControl"] { display: none !important; }
.stApp header { background: transparent !important; height: 0 !important; }
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none !important; }
.block-container { padding-top: 0.9rem !important; padding-bottom: 3rem !important; max-width: 1240px; }

h1, h2, h3, h4 { font-family: 'Inter', sans-serif !important; letter-spacing: -0.02em; color: var(--text) !important; }
p, li, label, .stMarkdown { font-size: 1rem; }

/* ---------- top bar ---------- */
.topbar { display:flex; align-items:center; justify-content:space-between; padding: 0.2rem 0 0.6rem; }
.brand { display:flex; align-items:center; gap:.6rem; font-weight:800; font-size:1.15rem; letter-spacing:-0.02em; }
.brand .logo { width:30px; height:30px; border-radius:9px; background: var(--orange); display:flex; align-items:center; justify-content:center; color:#0B0B0C; font-weight:900; }
.brand small { color: var(--muted); font-weight:500; font-size:.85rem; margin-left:.35rem; }
.pill { display:inline-flex; align-items:center; gap:.4rem; padding:.28rem .7rem; border-radius:999px; font-size:.82rem; font-weight:600; border:1px solid var(--line); background: var(--panel); color: var(--text); }
.dot { width:8px; height:8px; border-radius:50%; display:inline-block; }

/* ---------- Chrome-style tabs ---------- */
.stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid var(--line); padding-left: 2px; }
.stTabs [data-baseweb="tab"] {
  height: 46px; padding: 0 22px; border-radius: 12px 12px 0 0; background: #121214;
  border: 1px solid var(--line); border-bottom: none; color: var(--muted); font-weight: 600; font-size: .98rem;
}
.stTabs [data-baseweb="tab"]:hover { color: var(--text); background: #18181B; }
.stTabs [aria-selected="true"] { background: var(--panel2) !important; color: var(--text) !important; box-shadow: inset 0 3px 0 var(--orange); }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display: none; }
.stTabs [data-baseweb="tab-panel"] { padding-top: 1.4rem; }

/* ---------- inputs & buttons ---------- */
.stTextArea textarea, .stTextInput input, div[data-baseweb="select"] > div {
  background: var(--panel) !important; color: var(--text) !important; border-radius: 14px !important; border: 1px solid var(--line) !important; font-size: 1.02rem !important;
}
.stTextArea textarea:focus, .stTextInput input:focus { border-color: var(--orange) !important; box-shadow: 0 0 0 1px var(--orange) !important; }
.stButton > button, .stDownloadButton > button, [data-testid="stPopover"] button {
  border-radius: 12px !important; border: 1px solid var(--line) !important; background: var(--panel) !important; color: var(--text) !important; font-weight: 600 !important;
}
.stButton > button:hover, .stDownloadButton > button:hover { border-color: var(--orange) !important; color: var(--orange) !important; }
.stButton > button[kind="primary"], .stFormSubmitButton > button { background: var(--orange) !important; color: #0B0B0C !important; border: none !important; font-weight: 700 !important; }
.stButton > button[kind="primary"]:hover { filter: brightness(1.08); color:#0B0B0C !important; }
[data-testid="stExpander"] { border: 1px solid var(--line) !important; border-radius: 14px !important; background: var(--panel) !important; }
[data-testid="stExpander"] summary { font-weight: 600; }
[data-testid="stMetric"] { background: var(--panel); border:1px solid var(--line); border-radius:14px; padding: .8rem 1rem; }
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }

/* ---------- hero ---------- */
.hero-q { text-align:center; margin: .8rem 0 1.1rem; }
.hero-q h1 { font-size: clamp(1.9rem, 3.4vw, 2.7rem); font-weight: 800; margin: 0 0 .35rem; }
.hero-q p { color: var(--muted); font-size: 1.05rem; margin: 0; }

/* ---------- verdict card ---------- */
.verdict { display:grid; grid-template-columns: 210px 1fr; gap: 1.6rem; align-items:center; background: var(--panel); border:1px solid var(--line); border-radius: 22px; padding: 1.5rem 1.7rem; margin: .6rem 0 1rem; position: relative; overflow:hidden; }
.verdict::before { content:""; position:absolute; inset:0; background: radial-gradient(500px 220px at 0% 0%, var(--glow), transparent 60%); pointer-events:none; }
@media (max-width: 860px) { .verdict { grid-template-columns: 1fr; } }
.ring { position:relative; width:190px; height:190px; }
.ring svg { transform: rotate(-90deg); }
.ring .center { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; }
.ring .num { font-size: 1.85rem; font-weight: 800; font-variant-numeric: tabular-nums; letter-spacing:-0.03em; }
.ring .lbl { color: var(--muted); font-size: .8rem; text-transform: uppercase; letter-spacing: .12em; margin-top:.15rem; }
.claim-quote { color: var(--muted); font-size: .98rem; margin-bottom: .55rem; font-style: italic; }
.status-word { font-size: clamp(2.1rem, 4.4vw, 3.3rem); font-weight: 900; letter-spacing: -0.04em; line-height: 1; margin: 0 0 .7rem; }
.headline { font-size: 1.28rem; font-weight: 600; line-height: 1.45; margin: 0 0 .8rem; color: var(--text); }
.chips { display:flex; flex-wrap:wrap; gap:.45rem; }
.chip { padding:.32rem .75rem; border-radius:999px; font-size:.84rem; font-weight:600; border:1px solid var(--line); background: var(--panel2); color: var(--text); }
.chip b { color: var(--orange); }

.meaning { color: var(--muted); font-size: .9rem; text-align:center; margin-top:.5rem; line-height:1.35; }

/* ---------- evidence cards ---------- */
.col-title { display:flex; align-items:center; gap:.55rem; font-size: 1.08rem; font-weight: 700; margin: .4rem 0 .6rem; }
.col-title .count { color: var(--muted); font-weight:500; font-size:.92rem; }
.ev { background: var(--panel); border:1px solid var(--line); border-left: 4px solid var(--edge); border-radius: 14px; padding: .85rem 1rem .75rem; margin-bottom: .45rem; }
.ev-top { display:flex; justify-content:space-between; align-items:center; gap:.6rem; margin-bottom:.35rem; }
.ev-id { font-weight: 800; font-size: 1rem; }
.ev-src { color: var(--muted); font-size: .84rem; font-weight:500; }
.ev-why { font-size: .98rem; line-height: 1.5; margin: .15rem 0 .4rem; }
.ev-quote { color: #C9C9CF; font-size: .9rem; border-left: 2px solid var(--line); padding-left: .65rem; margin: .3rem 0 .45rem; font-style: italic; }
.wbar { height: 6px; background: #232327; border-radius: 6px; overflow:hidden; margin-top:.3rem; }
.wbar > span { display:block; height: 100%; background: var(--orange); border-radius: 6px; }
.wlbl { display:flex; justify-content:space-between; color: var(--muted); font-size: .78rem; margin-top:.25rem; }
.tag { font-size:.72rem; font-weight:700; padding:.12rem .5rem; border-radius:6px; letter-spacing:.03em; text-transform:uppercase; }

.panel { background: var(--panel); border:1px solid var(--line); border-radius: 18px; padding: 1.1rem 1.25rem; margin: .4rem 0 .9rem; }
.panel h4 { margin: 0 0 .55rem; font-size: 1.02rem; }
.panel ul { margin: 0; padding-left: 1.15rem; }
.panel li { margin-bottom: .4rem; line-height: 1.5; }
.panel .muted { color: var(--muted); }
.callout { background: linear-gradient(90deg, rgba(255,106,18,.14), rgba(255,106,18,.03)); border:1px solid rgba(255,106,18,.35); border-radius: 16px; padding: .95rem 1.15rem; margin: .5rem 0 1rem; }
.callout b { color: var(--orange); }
.note { color: var(--muted); font-size: .88rem; }

/* ---------- timeline ---------- */
.tl { position: relative; padding-left: 1.4rem; }
.tl::before { content:""; position:absolute; left: .42rem; top: .3rem; bottom: .3rem; width: 2px; background: var(--line); }
.tl-item { position: relative; margin-bottom: .55rem; background: var(--panel2); border:1px solid var(--line); border-radius: 12px; padding: .6rem .85rem; }
.tl-item::before { content:""; position:absolute; left: -1.22rem; top: .9rem; width: 10px; height: 10px; border-radius: 50%; background: var(--dotc); box-shadow: 0 0 0 3px var(--bg); }
.tl-head { display:flex; justify-content:space-between; gap:.5rem; font-size:.9rem; }
.tl-body { color:#C9C9CF; font-size:.88rem; margin-top:.25rem; line-height:1.45; }

/* ---------- cards grid ---------- */
.kpis { display:grid; grid-template-columns: repeat(4, 1fr); gap: .75rem; margin: .3rem 0 1rem; }
@media (max-width: 860px) { .kpis { grid-template-columns: repeat(2, 1fr); } }
.kpi { background: var(--panel); border:1px solid var(--line); border-radius: 16px; padding: 1rem 1.1rem; }
.kpi .v { font-size: 1.9rem; font-weight: 800; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
.kpi .l { color: var(--muted); font-size: .88rem; margin-top: .1rem; }
.card { background: var(--panel); border:1px solid var(--line); border-radius: 16px; padding: .95rem 1.05rem; margin-bottom: .55rem; min-height: 100%; }
.card .t { font-weight: 700; margin-bottom: .3rem; }
.card .s { color: var(--muted); font-size: .86rem; }

.step { display:flex; gap: .9rem; align-items:flex-start; background: var(--panel); border:1px solid var(--line); border-radius: 16px; padding: .95rem 1.1rem; margin-bottom: .55rem; }
.step .n { flex: 0 0 34px; height: 34px; border-radius: 10px; background: rgba(255,106,18,.15); color: var(--orange); font-weight: 800; display:flex; align-items:center; justify-content:center; }
.step .t { font-weight: 700; margin-bottom: .15rem; }
.step .s { color: #C9C9CF; font-size: .95rem; line-height: 1.5; }

mark.focus { background: rgba(255,106,18,.22); color: inherit; border-radius: 4px; padding: 0 .15rem; }
.focusbox { border: 1px solid rgba(255,106,18,.55); background: rgba(255,106,18,.08); border-radius: 12px; padding: .7rem .9rem; margin: .4rem 0 .8rem; }
"""
