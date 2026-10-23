"""HTTP client for the backend API, and the shaping of its answers for the UI.

Nothing here scores or judges evidence: verdicts, weights and evidence all come from the backend.
This module only calls `/api/v1` and turns the responses into the plain-language structures the
components render.

A "result" (returned by `check`) is a dict with:
  question, claim_text, run_id, notices, answer            the plain answer from POST /answer
  and, when the answer came from an assessment:
  scored{status, confidence}, plain{status}, explanation{headline, summary, why, what_would_change_it},
  counts{support, contradict}, evidence (decisive items), all_hits (everything searched),
  merged_copies, gold (the team's expected answer for a hand-checked claim)
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import streamlit as st

API_URL = os.getenv("API_URL", "http://127.0.0.1:8001").rstrip("/")
FEEDBACK_FILE = Path(os.getenv("FEEDBACK_FILE", Path(__file__).resolve().parent / "feedback.jsonl"))
CALIBRATION_LABELS_NEEDED = 30
_CHECK_TIMEOUT_S = 600  # a local model can take minutes on a CPU


class ApiError(RuntimeError):
    pass


def _request(method: str, path: str, timeout: float = 30, **kw) -> Any:
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

STANCE = {"SUPPORTS": "support", "CONTRADICTS": "contradict"}
STANCE_LABEL = {"SUPPORTS": "Supports", "CONTRADICTS": "Goes against", "AMBIGUOUS": "Unclear", "NEUTRAL": "Not relevant"}

RETRIEVAL_MODE = {"hybrid": "hybrid", "bm25": "bm25", "dense": "semantic"}  # UI choice -> API value
JUDGE_MODE = {"nli": "rules+nli", "heuristic": "rules"}  # UI choice -> API classifier mode


def _label(doc_type: str | None) -> str:
    return SOURCE_LABEL.get(doc_type or "", (doc_type or "file").replace("_", " ").capitalize())


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _confidence(interval: list[float]) -> str:
    width = interval[1] - interval[0]
    return "High" if width < 0.25 else "Medium" if width < 0.45 else "Low"


def _weight_label(w: float) -> str:
    if w >= 0.6:
        return "Counts a lot"
    if w >= 0.3:
        return "Counts moderately"
    if w >= 0.1:
        return "Counts a little"
    return "Barely counts"


# ---- system ---------------------------------------------------------------------------------

def health() -> dict[str, Any]:
    h = _request("GET", "/health", timeout=5)
    c = h.get("components") or {}
    nli, llm = c.get("nli") or {}, c.get("llm") or {}
    judge = f"{nli['model'].split('/')[-1]} + rules" if nli.get("model") else "keyword rules only"
    modes = ["hybrid", "bm25"] + (["dense"] if (c.get("embeddings") or {}).get("enabled") else [])
    return {
        "status": h.get("status"),
        "judge": judge,
        "nli_enabled": bool(nli.get("enabled")),
        "llm_available": bool(llm.get("available")),
        "checks_ready": bool(nli.get("enabled") or llm.get("available")),
        "retrieval_modes": modes,
        "components": c,
    }


@st.cache_data(ttl=600, show_spinner=False)
def credibility_config() -> dict[str, Any]:
    return _request("GET", "/config/credibility")


def verdict_meanings() -> dict[str, Any]:
    return {"statuses": list(STATUS.values()), "status_plain": STATUS_PLAIN}


def authority_weights() -> dict[str, float]:
    """How much each kind of source counts for a claim about a rule (config/credibility.yaml)."""
    return credibility_config()["authority"]["normative"]


@st.cache_data(ttl=600, show_spinner=False)
def gold() -> dict[str, Any]:
    items = _request("GET", "/evaluation/claims").get("items", [])
    layer = {"normative": "Rule", "practice": "Practice", "factual": "Fact"}
    return {"claims": [{
        "claim_id": g["claim_id"],
        "process": g.get("claim_category") or "",
        "layer": layer.get(g.get("claim_type") or "", (g.get("claim_type") or "Other").capitalize()),
        "claim_text": g["reference_claim"],
        "expected_status": STATUS.get(g.get("expected_verdict") or "", "Not enough evidence"),
    } for g in items]}


@st.cache_data(ttl=120, show_spinner=False)
def documents() -> list[dict[str, Any]]:
    items = _request("GET", "/documents", params={"limit": 500}).get("items", [])
    return [{
        "doc_id": d["id"],
        "title": d["title"],
        "source_label": _label(d.get("doc_type")),
        "n_chunks": d.get("section_count", 0),
        "phase": (d.get("rel_path") or "").split("/")[0],
        "superseded": d.get("status") == "superseded",
        "draft": d.get("status") == "draft",
        "date": d.get("doc_date"),
    } for d in items]


@st.cache_data(ttl=120, show_spinner=False)
def corpus_profile() -> dict[str, Any]:
    overview = _request("GET", "/overview")
    return {
        "documents": len(documents()),
        "source_files": overview["corpus"].get("source_files", 0),
        "sections": overview["corpus"]["sections_total"],
        "people": len(_request("GET", "/search/facets").get("people") or {}),
        "gold_claims": len(gold()["claims"]),
    }


@st.cache_data(ttl=600, show_spinner=False)
def roster() -> list[dict[str, Any]]:
    people = _request("GET", "/search/facets").get("people") or {}
    return [{"Name": n, "Files mentioning them": c} for n, c in sorted(people.items(), key=lambda kv: (-kv[1], kv[0]))]


# ---- source files ---------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner=False)
def preview(doc_id: str, chunk_id: str | None = None) -> dict[str, Any]:
    d = _request("GET", f"/documents/{doc_id}")
    src = _request("GET", f"/documents/{doc_id}/source")
    is_csv = src.get("file_kind") == "csv"
    out: dict[str, Any] = {
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
            s = _request("GET", f"/sections/{chunk_id}")
            out["focus"] = {"cite_id": d["id"], "heading": s.get("heading"), "text": s.get("text") or "", "row_index": row_index}
        except ApiError:
            pass
    elif row_index is not None:
        out["focus"] = {"cite_id": d["id"], "heading": d.get("title"), "text": "", "row_index": row_index}
    return out


@st.cache_data(ttl=600, show_spinner=False)
def raw_bytes(doc_id: str) -> bytes:
    return (_request("GET", f"/documents/{doc_id}/source").get("content") or "").encode("utf-8")


def extract_claims(doc_id: str) -> dict[str, Any]:
    """Candidate claims the backend finds in one file (rule-based; proposals, not facts)."""
    items = _request("GET", "/claims/candidates", params={"limit": 500}).get("items", [])
    return {
        "model": "rule-based extractor",
        "provider": "local",
        "claims": [{"claim": c["text"], "role": c.get("section_heading"),
                    "layer": "meeting decision" if c.get("kind") == "decision" else "written rule",
                    "quote": c.get("caution")} for c in items if c["document_id"] == doc_id],
    }


# ---- checking a claim -----------------------------------------------------------------------

def _evidence_row(e: dict[str, Any], cluster_sizes: Counter) -> dict[str, Any]:
    doc = e.get("document") or {}
    label = e["label"]
    weight = float((e.get("features") or {}).get("weight") or 0.0)
    size = cluster_sizes.get(e.get("cluster_id"), 1) if e.get("cluster_id") else 1
    quote = "" if doc.get("kind") == "record" else (e.get("span_text") or "").strip()  # records are shown via Preview file
    heading = f" · {e['heading']}" if e.get("heading") and e["heading"] != doc.get("title") else ""
    return {
        "rank": e["rank"],
        "cite_id": e["document_id"],
        "doc_id": e["document_id"],
        "chunk_id": e["section_id"],
        "source_label": _label(doc.get("doc_type")) + heading,
        "metadata": {"date": doc.get("doc_date"), "superseded": doc.get("status") == "superseded", "draft": doc.get("status") == "draft"},
        "stance": STANCE.get(label, "unrelated"),
        "stance_label": STANCE_LABEL.get(label, label.title()),
        "rationale": e.get("rationale") or "",
        "quote": quote[:320] + ("…" if len(quote) > 320 else ""),
        "final_weight": weight,
        "independence_weight": 1.0 / size if size > 1 else 1.0,
        "weight_label": _weight_label(weight),
    }


def _gold_match(claim_text: str, claim_id: str | None) -> dict[str, Any] | None:
    try:
        claims = gold()["claims"]
    except ApiError:
        return None
    for g in claims:
        if (claim_id and g["claim_id"] == claim_id) or _norm(g["claim_text"]) == _norm(claim_text):
            return {"claim_id": g["claim_id"], "expected_status": g["expected_status"]}
    return None


def _merged_copies(clusters: list[dict[str, Any]]) -> list[list[str]]:
    copies = []
    for c in clusters:
        docs = list(dict.fromkeys(c.get("documents") or []))
        n = len(c.get("members") or [])
        if n > 1 and docs:
            copies.append(docs if len(docs) > 1 else [f"{docs[0]} ×{n}"])
    return copies


def _assessment_result(a: dict[str, Any], claim: str, claim_id: str | None) -> dict[str, Any]:
    r, ex = a["result"], a.get("explanation") or {}
    status = STATUS.get(a.get("verdict") or r.get("verdict"), "Not enough evidence")
    cluster_sizes = Counter(e["cluster_id"] for e in a["evidence"] if e.get("cluster_id"))
    rows = [_evidence_row(e, cluster_sizes) for e in sorted(a["evidence"], key=lambda e: e["rank"])]
    notices = list(a.get("retrieval", {}).get("classifier_notes") or [])
    if a.get("needs_review"):
        notices.append("A person should decide: " + " ".join(r.get("review_reasons") or []))
    change = " ".join((ex.get("missing_information") or [])[:1] + (ex.get("next_steps") or [])[:1])
    return {
        "run_id": a["id"],
        "claim_text": a.get("claim_text") or claim,
        "notices": notices,
        "scored": {"status": status, "confidence": _confidence(r.get("interval") or [0.0, 1.0])},
        "plain": {"status": HEADLINE[status]},
        "explanation": {"headline": HEADLINE[status], "summary": ex.get("summary") or HEADLINE[status],
                        "why": (ex.get("reasons") or [])[:6],
                        "what_would_change_it": change or "More independent evidence on either side."},
        "counts": {"support": sum(x["stance"] == "support" for x in rows), "contradict": sum(x["stance"] == "contradict" for x in rows)},
        "evidence": [x for x in rows if x["stance"] in ("support", "contradict")],
        "all_hits": rows,
        "merged_copies": _merged_copies(r.get("clusters") or []),
        "gold": _gold_match(claim, claim_id),
    }


def check(question: str, *, judge: str = "nli", retrieval_mode: str = "hybrid", top_k: int = 15,
          claim_id: str | None = None) -> dict[str, Any]:
    """Answer a claim or question in plain language (backend POST /answer).

    The backend decides how: a named record (DEAL-027) is checked against the policy rulebook, a rule
    question is answered from the policies and the records, anything else is checked as a claim. For
    claims the full assessment is attached too, for the "full details" view.
    """
    text = (question or "").strip()
    body: dict[str, Any] = {"question": text, "classifier_mode": JUDGE_MODE.get(judge, "rules+nli")}
    if retrieval_mode and retrieval_mode != "hybrid":
        body["retrieval_mode"] = RETRIEVAL_MODE.get(retrieval_mode, "hybrid")
    if top_k and int(top_k) != 15:
        body["max_evidence"] = max(4, min(60, int(top_k)))
    started = time.perf_counter()
    answer = _request("POST", "/answer", json=body, timeout=_CHECK_TIMEOUT_S)
    if answer.get("assessment_id"):
        assessment = _request("GET", f"/assessments/{answer['assessment_id']}")
        result = _assessment_result(assessment, answer.get("checked_as") or text, claim_id)
    else:
        result = {"run_id": f"{answer.get('kind')}:{answer.get('checked_as') or text}", "claim_text": text, "notices": []}
    result.update(answer=answer, question=text, seconds=round(time.perf_counter() - started, 1))
    return result


def recent_checks(limit: int = 8) -> list[dict[str, Any]]:
    items = _request("GET", "/claims", params={"limit": max(1, min(200, limit))}).get("items", [])
    return [{"id": c["id"], "status": STATUS.get(c["latest_verdict"], "Not enough evidence"),
             "score": c.get("latest_score"), "claim_text": c["text"]} for c in items if c.get("latest_verdict")]


def ask(message: str) -> dict[str, Any]:
    """Any question, answered in plain language by the same /answer endpoint."""
    result = check(message)
    return {"note": result["answer"].get("note"), "result": result}


# ---- feedback and calibration (stored locally next to the UI) -------------------------------

def _feedback_rows() -> list[dict[str, Any]]:
    if not FEEDBACK_FILE.exists():
        return []
    rows = []
    for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def calibration() -> dict[str, Any]:
    n = len(_feedback_rows())
    try:
        n += len(gold()["claims"])
    except ApiError:
        pass
    ready = n >= CALIBRATION_LABELS_NEEDED
    message = (f"{n} labels available; calibration can be measured." if ready else
               f"{n} of {CALIBRATION_LABELS_NEEDED} labels collected before calibration is measured.")
    return {"n_labels": n, "needed": CALIBRATION_LABELS_NEEDED, "ready": ready, "message": message}


def save_feedback(run_id: str, is_correct: bool, correct_status: str | None = None, note: str | None = None) -> dict[str, Any]:
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    row = {"run_id": run_id, "is_correct": is_correct, "correct_status": correct_status, "note": note,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return {"saved": True, "calibration": calibration()}
