"""HTTP client for the org-knowledge-credibility FastAPI backend.

The Streamlit UI (app.py, theme.py, components/) is the Northfield UI unchanged. This module is
the only adapted file: it calls this project's /api/v1 endpoints and returns the shapes those
components expect. Nothing here scores or judges evidence; verdicts, weights and evidence all
come from the backend.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import streamlit as st

API_URL = os.getenv("API_URL", "http://127.0.0.1:8001").rstrip("/")
FEEDBACK_FILE = Path(os.getenv("FEEDBACK_FILE", Path(__file__).resolve().parent.parent / "feedback.jsonl"))
CALIBRATION_LABELS_NEEDED = 30


class ApiError(RuntimeError):
    pass


def _req(method: str, path: str, timeout: float = 30, **kw) -> Any:
    try:
        r = httpx.request(method, f"{API_URL}/api/v1{path}", timeout=timeout, **kw)
    except httpx.HTTPError as e:
        raise ApiError(f"Backend not reachable at {API_URL} ({type(e).__name__}). Start it with: docker compose up -d") from e
    if r.status_code >= 400:
        try:
            err = r.json().get("error") or {}
            details = err.get("details")
            if err.get("code") == "validation_error" and isinstance(details, list):
                detail = "; ".join(str(d.get("msg")) for d in details)
            else:
                detail = err.get("message") or r.text
        except ValueError:
            detail = r.text
        raise ApiError(f"{r.status_code}: {detail}")
    return r.json() if r.content else None


# ---- vocabulary: backend verdicts / labels -> UI wording ------------------------------------

STATUS = {
    "SUPPORTED": "Supported",
    "PARTIALLY_SUPPORTED": "Likely",
    "CONTESTED": "Conflicted",
    "CONTRADICTED": "Unsupported",
    "INSUFFICIENT_EVIDENCE": "Not enough evidence",
}

STATUS_PLAIN = {
    "Supported": "The files back this up: current, authoritative, independent sources agree and nothing serious goes against it.",
    "Likely": "Probably true, but the support comes from weaker sources or covers only part of the claim.",
    "Conflicted": "Real evidence on both sides, for example the policy says one thing and practice shows another. A person should decide.",
    "Unsupported": "The weight of the evidence goes against the claim.",
    "Not enough evidence": "The files say too little about this to tell either way.",
}

HEADLINE = {
    "Supported": "The files back this up.",
    "Likely": "The files partly back this up.",
    "Conflicted": "The files point both ways.",
    "Unsupported": "The files say otherwise.",
    "Not enough evidence": "The files don't say enough to tell.",
}

SOURCE_LABEL = {
    "policy": "Policy",
    "procedure": "Process doc",
    "meeting_note": "Meeting notes",
    "interview": "Interview",
    "communication": "Slack / email",
    "communication_index": "Message index",
    "org_reference": "Org reference",
    "crm_record": "CRM record",
    "task_record": "Task record",
    "audit_event": "Audit log",
}

# Evidence scope -> plain name (used for the authority chart on How it works).
SOURCE_PLAIN = {
    "formal": "Approved policy / process",
    "decision": "Meeting decision",
    "testimony": "First-hand account",
    "practice": "System record (CRM, tasks, audit)",
    "discussion": "Slack / email discussion",
    "history": "Past version",
    "proposal": "Draft or proposal",
    "belief": "Second-hand belief",
}

# Authority for claims about a rule (config/credibility.yaml -> authority.normative).
AUTHORITY_NORMATIVE = {"formal": 1.0, "decision": 0.8, "testimony": 0.35, "practice": 0.3, "discussion": 0.3,
                       "history": 0.3, "proposal": 0.25, "belief": 0.15}

STANCE = {"SUPPORTS": "support", "CONTRADICTS": "contradict"}
STANCE_LABEL = {"SUPPORTS": "Supports", "CONTRADICTS": "Goes against", "AMBIGUOUS": "Unclear", "NEUTRAL": "Not relevant"}

RETRIEVAL = {"hybrid": "hybrid", "bm25": "bm25", "dense": "semantic"}
# "Rules + NLI model" (the recommended check) or "Keyword baseline" (rules alone).
JUDGE = {"nli": "rules+nli", "heuristic": "rules"}


def _label(doc_type: Optional[str]) -> str:
    return SOURCE_LABEL.get(doc_type or "", (doc_type or "file").replace("_", " ").capitalize())


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _confidence(interval: List[float]) -> str:
    w = interval[1] - interval[0]
    return "High" if w < 0.25 else "Medium" if w < 0.45 else "Low"


def _weight_label(w: float) -> str:
    if w >= 0.6:
        return "Counts a lot"
    if w >= 0.3:
        return "Counts moderately"
    if w >= 0.1:
        return "Counts a little"
    return "Barely counts"


# ---- system ---------------------------------------------------------------------------------

def health() -> Dict[str, Any]:
    h = _req("GET", "/health", timeout=5)
    c = h.get("components") or {}
    nli = c.get("nli") or {}
    llm = c.get("llm") or {}
    judge = "rules + NLI" if nli.get("enabled") else "rules"
    if nli.get("model"):
        judge = f"{nli['model'].split('/')[-1]} + rules"
    modes = ["hybrid", "bm25"] + (["dense"] if (c.get("embeddings") or {}).get("enabled") else [])
    return {
        "status": h.get("status"),
        "llm_ready": bool(nli.get("enabled") or llm.get("available")),
        "providers": ["local"],
        "models": {"local": judge, "gemini": "not used (runs locally)", "groq": "not used (runs locally)"},
        "keys": {"gemini": False, "groq": False},
        "gemini_models": [],
        "retrieval_modes": modes,
        "ollama_model": llm.get("model") if llm.get("available") else None,
        "components": c,
    }


@st.cache_data(ttl=600, show_spinner=False)
def meta() -> Dict[str, Any]:
    statuses = list(STATUS.values())
    return {
        "statuses": statuses,
        "status_plain": STATUS_PLAIN,
        "authority_weights": AUTHORITY_NORMATIVE,
        "source_plain": SOURCE_PLAIN,
    }


@st.cache_data(ttl=600, show_spinner=False)
def gold() -> Dict[str, Any]:
    items = _req("GET", "/evaluation/claims").get("items", [])
    layer = {"normative": "Rule", "practice": "Practice", "factual": "Fact"}
    return {"claims": [{
        "claim_id": g["claim_id"],
        "process": g.get("claim_category") or "",
        "layer": layer.get(g.get("claim_type") or "", (g.get("claim_type") or "Other").capitalize()),
        "claim_text": g["reference_claim"],
        "expected_status": STATUS.get(g.get("expected_verdict") or "", "Not enough evidence"),
    } for g in items]}


@st.cache_data(ttl=120, show_spinner=False)
def documents() -> list:
    items = _req("GET", "/documents", params={"limit": 500}).get("items", [])
    out = []
    for d in items:
        date = d.get("doc_date")
        out.append({
            "doc_id": d["id"],
            "title": d["title"],
            "source_label": _label(d.get("doc_type")),
            "n_chunks": d.get("section_count", 0),
            "phase": (d.get("rel_path") or "").split("/")[0],
            "superseded": d.get("status") == "superseded",
            "draft": d.get("status") == "draft",
            "first_date": date,
            "last_date": date,
        })
    return out


@st.cache_data(ttl=120, show_spinner=False)
def profile() -> Dict[str, Any]:
    ov = _req("GET", "/overview")
    facets = _req("GET", "/search/facets")
    return {
        "documents": len(documents()),
        "chunks": ov["corpus"]["sections_total"],
        "people": len(facets.get("people") or {}),
        "gold_claims": len(gold()["claims"]),
    }


@st.cache_data(ttl=600, show_spinner=False)
def roster() -> list:
    people = _req("GET", "/search/facets").get("people") or {}
    return [{"Name": n, "Files mentioning them": c} for n, c in sorted(people.items(), key=lambda kv: (-kv[1], kv[0]))]


# ---- source files ---------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner=False)
def preview(doc_id: str, chunk_id: Optional[str] = None) -> Dict[str, Any]:
    d = _req("GET", f"/documents/{doc_id}")
    src = _req("GET", f"/documents/{doc_id}/source")
    is_csv = src.get("file_kind") == "csv"
    out: Dict[str, Any] = {
        "doc_id": d["id"],
        "source_label": _label(d.get("doc_type")),
        "title": Path(src["rel_path"]).name,
        "path": src["rel_path"],
        "superseded": d.get("status") == "superseded",
        "draft": d.get("status") == "draft",
        "format": "csv" if is_csv else "md",
        "text": src.get("content") or "",
    }
    row_index = None
    if is_csv:
        out["rows"] = list(csv.DictReader(io.StringIO(out["text"])))
        if d.get("kind") == "record" and src.get("start_line"):
            row_index = int(src["start_line"]) - 2  # line 1 is the header
    if chunk_id:
        try:
            s = _req("GET", f"/sections/{chunk_id}")
            out["focus"] = {"cite_id": d["id"], "heading": s.get("heading"), "text": s.get("text") or "", "row_index": row_index}
        except ApiError:
            pass
    elif row_index is not None:
        out["focus"] = {"cite_id": d["id"], "heading": d.get("title"), "text": "", "row_index": row_index}
    return out


@st.cache_data(ttl=600, show_spinner=False)
def raw_bytes(doc_id: str) -> bytes:
    src = _req("GET", f"/documents/{doc_id}/source")
    return (src.get("content") or "").encode("utf-8")


# ---- checking a claim -----------------------------------------------------------------------

def _evidence_row(e: Dict[str, Any], sizes: Counter) -> Dict[str, Any]:
    doc = e.get("document") or {}
    label = e["label"]
    w = float((e.get("features") or {}).get("weight") or 0.0)
    size = sizes.get(e.get("cluster_id"), 1) if e.get("cluster_id") else 1
    quote = (e.get("span_text") or "").strip()
    if doc.get("kind") == "record":
        quote = ""  # records are shown through Preview file; the rationale already names the fields
    return {
        "rank": e["rank"],
        "cite_id": e["document_id"],
        "doc_id": e["document_id"],
        "chunk_id": e["section_id"],
        "source_label": _label(doc.get("doc_type")) + (f" · {e['heading']}" if e.get("heading") and e["heading"] != doc.get("title") else ""),
        "metadata": {"date": doc.get("doc_date"), "superseded": doc.get("status") == "superseded", "draft": doc.get("status") == "draft"},
        "stance": STANCE.get(label, "unrelated"),
        "stance_label": STANCE_LABEL.get(label, label.title()),
        "rationale": e.get("rationale") or "",
        "quote": quote[:320] + ("…" if len(quote) > 320 else ""),
        "final_weight": w,
        "independence_weight": 1.0 / size if size > 1 else 1.0,
        "weight_label": _weight_label(w),
        "contribution": e.get("contribution"),
    }


def _gold_match(claim_text: str, claim_id: Optional[str]) -> Optional[Dict[str, Any]]:
    try:
        claims = gold()["claims"]
    except ApiError:
        return None
    for g in claims:
        if (claim_id and g["claim_id"] == claim_id) or _norm(g["claim_text"]) == _norm(claim_text):
            return {"claim_id": g["claim_id"], "expected_status": g["expected_status"]}
    return None


def _result(a: Dict[str, Any], payload: Dict[str, Any], engine: str, timings: Dict[str, float]) -> Dict[str, Any]:
    r = a["result"]
    ex = a.get("explanation") or {}
    status = STATUS.get(a.get("verdict") or r.get("verdict"), "Not enough evidence")
    sizes = Counter(e["cluster_id"] for e in a["evidence"] if e.get("cluster_id"))
    rows = [_evidence_row(e, sizes) for e in sorted(a["evidence"], key=lambda e: e["rank"])]
    decisive = [x for x in rows if x["stance"] in ("support", "contradict")]
    copies = []
    for c in r.get("clusters") or []:
        docs = list(dict.fromkeys(c.get("documents") or []))
        n = len(c.get("members") or [])
        if n > 1 and docs:
            copies.append(docs if len(docs) > 1 else [f"{docs[0]} ×{n}"])
    interval = r.get("interval") or [0.0, 1.0]
    change = " ".join((ex.get("missing_information") or [])[:1] + (ex.get("next_steps") or [])[:1])
    notices = list(a.get("retrieval", {}).get("classifier_notes") or [])
    if a.get("needs_review"):
        notices.append("A person should decide: " + " ".join(r.get("review_reasons") or []))
    return {
        "run_id": a["id"],
        "claim_text": a.get("claim_text") or payload["claim"],
        "stance_engine": engine,
        "retrieval_mode": payload.get("retrieval_mode") or "hybrid",
        "top_k": payload.get("top_k") or 15,
        "notices": notices,
        "scored": {
            "status": status,
            "confidence": _confidence(interval),
            "credibility_score": r["score"],
            "credible_interval": interval,
            "support_weight": r.get("support_mass", 0.0),
            "contradict_weight": r.get("contradict_mass", 0.0),
            "method": "evidence weights, Beta(0.25, 0.25)",
            "prior": 0.5,
        },
        "plain": {
            "status": HEADLINE[status],
            "score": f"{r['score']:.2f} on a 0 to 1 scale: a weighted summary of the evidence, not a probability.",
            "interval": f"Between {interval[0]:.2f} and {interval[1]:.2f} given how much evidence there is.",
            "confidence": f"{_confidence(interval)}: based on how narrow the range is.",
        },
        "explanation": {
            "headline": HEADLINE[status],
            "summary": ex.get("summary") or HEADLINE[status],
            "why": (ex.get("reasons") or [])[:6],
            "what_would_change_it": change or "More independent evidence on either side.",
            "source": "template",
        },
        "counts": {"support": sum(x["stance"] == "support" for x in rows), "contradict": sum(x["stance"] == "contradict" for x in rows)},
        "evidence": decisive,
        "all_hits": rows,
        "merged_copies": copies,
        "gold": _gold_match(payload["claim"], payload.get("claim_id")),
        "llm": {},
        "timings_ms": timings,
        "calibration": calibration().get("message", ""),
    }


def check(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Answer a claim or question in plain language (backend POST /answer).

    The backend decides how: a named record (DEAL-027) is checked against the policy rulebook, a rule
    question is answered from the policies and the records, anything else is checked as a claim. For
    claims the full assessment is attached too, for the "full details" view.
    """
    text = (payload.get("claim") or "").strip()
    engine = payload.get("stance_engine") or "llm"
    t0 = time.perf_counter()
    body: Dict[str, Any] = {"question": text, "classifier_mode": JUDGE.get(engine, "rules+nli")}
    if payload.get("retrieval_mode") and payload["retrieval_mode"] != "hybrid":
        body["retrieval_mode"] = RETRIEVAL.get(payload["retrieval_mode"], "hybrid")
    if payload.get("top_k") and int(payload["top_k"]) != 15:
        body["max_evidence"] = max(4, min(60, int(payload["top_k"])))
    ans = _req("POST", "/answer", json=body, timeout=600)
    timings = {"total_ms": (time.perf_counter() - t0) * 1000}
    if ans.get("assessment_id"):
        a = _req("GET", f"/assessments/{ans['assessment_id']}")
        res = _result(a, {**payload, "claim": ans.get("checked_as") or text}, engine, timings)
    else:
        res = {"run_id": f"{ans.get('kind')}:{ans.get('checked_as') or text}", "claim_text": text, "notices": [],
               "timings_ms": timings}
    res["answer"] = ans
    res["question"] = text
    return res


def runs(limit: int = 8) -> list:
    items = _req("GET", "/claims", params={"limit": max(1, min(200, limit))}).get("items", [])
    return [{
        "id": c["id"],
        "origin": "ui",
        "status": STATUS.get(c["latest_verdict"], "Not enough evidence"),
        "score": c.get("latest_score"),
        "claim_text": c["text"],
    } for c in items if c.get("latest_verdict")]


# ---- Ask: any question, answered in plain language by the same /answer endpoint ----------------

def ask(message: str) -> Dict[str, Any]:
    res = check({"claim": message, "stance_engine": "llm"})
    a = res["answer"]
    return {"normalised": {"claim": a.get("checked_as"), "note": a.get("note")}, "result": res}


# ---- feedback and calibration (stored locally next to the UI) ------------------------------

def _feedback_rows() -> List[Dict[str, Any]]:
    if not FEEDBACK_FILE.exists():
        return []
    rows = []
    for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def calibration() -> Dict[str, Any]:
    n = len(_feedback_rows())
    try:
        n += len(gold()["claims"])
    except ApiError:
        pass
    ready = n >= CALIBRATION_LABELS_NEEDED
    msg = (f"{n} labels available; calibration can be measured." if ready else
           f"{n} of {CALIBRATION_LABELS_NEEDED} labels collected before calibration is measured.")
    return {"n_labels": n, "needed": CALIBRATION_LABELS_NEEDED, "ready": ready, "message": msg,
            "reliability": [], "preview_brier": None, "preview_ece": None}


def feedback(run_id: str, is_correct: bool, correct_status: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    row = {"run_id": run_id, "is_correct": is_correct, "correct_status": correct_status, "note": note,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return {"saved": True, "calibration": calibration()}


# ---- claims found in a file -----------------------------------------------------------------

def extract_claims(doc_id: str) -> Dict[str, Any]:
    items = _req("GET", "/claims/candidates", params={"limit": 500}).get("items", [])
    mine = [c for c in items if c["document_id"] == doc_id]
    return {
        "model": "rule-based extractor",
        "provider": "local",
        "truncated": False,
        "claims": [{"claim": c["text"], "role": c.get("section_heading"),
                    "layer": "meeting decision" if c.get("kind") == "decision" else "written rule",
                    "quote": c.get("caution")} for c in mine],
    }


# ---- accuracy check (run from the backend CLI in this project) ------------------------------

def start_eval(top_k: int, configs: Optional[list] = None) -> Dict[str, Any]:
    raise ApiError("Run the evaluation from the backend: python -m app.cli evaluate")


def eval_job(job_id: str) -> Dict[str, Any]:
    return {"status": "failed", "progress": 0, "total": 0, "message": "Run python -m app.cli evaluate in the backend."}


def eval_latest() -> Optional[Dict[str, Any]]:
    return None


def llm_usage() -> Dict[str, Any]:
    return {"by_model": [], "last_error": None}


# ---- settings (no cloud keys: everything runs locally) -------------------------------------

def ping(provider: str) -> Dict[str, Any]:
    return {"ok": False, "error": "Not used: this app's models run locally."}


def save_keys(**values) -> Dict[str, Any]:
    raise ApiError("No API keys are needed: the NLI model and gemma4 run locally on this computer.")
