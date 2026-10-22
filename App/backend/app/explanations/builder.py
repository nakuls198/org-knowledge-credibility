"""User-facing explanations generated ONLY from recorded evidence and engine outputs.

The template explanation is the authoritative one. An optional LLM narrative may
paraphrase it, but it is validated: it may only cite evidence ids [E#] that exist and
document ids that appear in the evidence; otherwise it is discarded.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.claims.analysis import ClaimAnalysis
from app.credibility.engine import CredibilityResult, EvidenceItem

log = logging.getLogger(__name__)
TEMPLATE_VERSION = "explain-template-v1"
NARRATIVE_PROMPT_VERSION = "narrative-v1"

_VERDICT_TEXT = {
    "SUPPORTED": "The available evidence supports this claim.",
    "PARTIALLY_SUPPORTED": "The claim is only partly supported: the support is weak, indirect or covers only part of the claim.",
    "CONTESTED": "The evidence conflicts and does not settle the claim.",
    "CONTRADICTED": "The available evidence contradicts this claim.",
    "INSUFFICIENT_EVIDENCE": "There is not enough relevant evidence to assess this claim.",
}


def _ref(i: int, it: EvidenceItem) -> dict[str, Any]:
    return {"evidence": f"E{i + 1}", "document_id": it.ctx.document_id, "section_id": it.ctx.section_id,
            "scope": it.judgement.scope, "weight": round(it.weight, 3), "why": it.judgement.rationale}


def build_explanation(analysis: ClaimAnalysis, items: list[EvidenceItem], result: CredibilityResult) -> dict[str, Any]:
    sup = sorted([(i, it) for i, it in enumerate(items) if it.judgement.label == "SUPPORTS"], key=lambda x: -x[1].weight)
    con = sorted([(i, it) for i, it in enumerate(items) if it.judgement.label == "CONTRADICTS"], key=lambda x: -x[1].weight)
    amb = [(i, it) for i, it in enumerate(items) if it.judgement.label == "AMBIGUOUS"]

    reasons: list[str] = []
    if sup:
        top = sup[0][1]
        reasons.append(f"Strongest support: E{sup[0][0] + 1} from {top.ctx.document_id} ({top.judgement.scope}, "
                       f"weight {top.weight:.2f}). {result.independent_support} independent support lineage(s) in total.")
    if con:
        top = con[0][1]
        reasons.append(f"Strongest contradiction: E{con[0][0] + 1} from {top.ctx.document_id} ({top.judgement.scope}, "
                       f"weight {top.weight:.2f}). {result.independent_contradiction} independent contradicting lineage(s).")
    if not sup and not con:
        reasons.append("No retrieved passage clearly supports or contradicts the claim.")
    reasons.append(f"Weighted support {result.support_mass:.2f} vs contradiction {result.contradict_mass:.2f} gives a heuristic "
                   f"credibility score of {result.score:.2f} (evidence-weight interval {result.interval[0]:.2f}-{result.interval[1]:.2f}).")
    if analysis.claim_type == "normative":
        reasons.append("This is a claim about a rule, so documented policies/procedures and recorded decisions carry the most "
                       "weight; observed practice is reported separately as a caveat.")
    elif analysis.claim_type == "practice":
        reasons.append("This is a claim about practice, so records and first-hand accounts carry the most weight; a written "
                       "rule says what should happen, not what does.")
    for g in result.governance:
        reasons.append(f"Conflict between {', '.join(g.supporting)} and {', '.join(g.contradicting)}: {g.explanation}")

    summary = _VERDICT_TEXT.get(result.verdict, result.verdict)
    if sup:
        summary += f" Support comes mainly from {', '.join(dict.fromkeys(it.ctx.document_id for _, it in sup[:3]))}."
    if con:
        summary += f" Contradicting evidence comes from {', '.join(dict.fromkeys(it.ctx.document_id for _, it in con[:3]))}."
    if result.needs_review:
        summary += " Flagged for human review."

    return {
        "generated_by": TEMPLATE_VERSION,
        "summary": summary,
        "verdict": result.verdict,
        "claim_type": analysis.claim_type,
        "claim_type_reason": analysis.claim_type_reason,
        "reasons": reasons,
        "key_supporting": [_ref(i, it) for i, it in sup[:5]],
        "key_contradicting": [_ref(i, it) for i, it in con[:5]],
        "ambiguous": [_ref(i, it) for i, it in amb[:5]],
        "temporal": result.temporal_notes,
        "governance": [g.model_dump() for g in result.governance],
        "caveats": result.caveats,
        "uncertainty": (f"Heuristic evidence-weight interval {result.interval[0]:.2f}-{result.interval[1]:.2f}; "
                        f"coverage of the claim's key elements {result.coverage:.0%}; conflict ratio {result.conflict:.2f}. "
                        "These summarise the evidence considered; they are not calibrated probabilities."),
        "missing_information": result.missing_information,
        "next_steps": result.next_steps,
        "limitations": [
            "Only the indexed corpus was searched; evidence outside it is unknown to the system.",
            "Evidence labels come from automated classification and may be wrong; inspect each passage.",
            "Authority and temporal weights are configurable assumptions (config/credibility.yaml).",
        ],
    }


def llm_narrative(explanation: dict[str, Any], items: list[EvidenceItem], host: str, model: str,
                  timeout_s: float = 90.0) -> dict[str, Any] | None:
    """Optional 2-4 sentence paraphrase, validated against the recorded evidence."""
    facts = {k: explanation[k] for k in ("summary", "reasons", "temporal", "caveats", "missing_information")}
    allowed_e = {f"E{i + 1}" for i in range(len(items))}
    allowed_docs = {it.ctx.document_id for it in items}
    prompt = ("Rewrite the following structured assessment as 2-4 plain sentences for a manager. Use ONLY the facts given; "
              "do not add facts. Cite evidence as [E#] exactly as given. Do not mention scores you are not given.\n\n"
              f"{facts}")
    try:
        r = httpx.post(f"{host.rstrip('/')}/api/chat", timeout=timeout_s, json={
            "model": model, "stream": False, "options": {"temperature": 0, "seed": 7},
            "messages": [{"role": "system", "content": "You summarise analyses faithfully and never invent facts."},
                         {"role": "user", "content": prompt}]})
        r.raise_for_status()
        text = r.json()["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        log.info("LLM narrative unavailable: %s", type(exc).__name__)
        return None
    cited = set(re.findall(r"\bE\d+\b", text))
    docs = set(re.findall(r"\b[A-Z]{2,8}-\d{2,4}\b", text))
    valid = cited <= allowed_e and docs <= allowed_docs and len(text) < 1500
    if not valid:
        return {"text": None, "model": model, "prompt_version": NARRATIVE_PROMPT_VERSION, "validated": False,
                "rejected_reason": f"cited unknown references: {sorted((cited - allowed_e) | (docs - allowed_docs))}"}
    return {"text": text, "model": model, "prompt_version": NARRATIVE_PROMPT_VERSION, "validated": True}
