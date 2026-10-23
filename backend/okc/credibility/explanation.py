"""User-facing explanations generated ONLY from recorded evidence and engine outputs.

The template explanation is the authoritative one. An optional LLM narrative may paraphrase it, but
it is validated: it may only cite evidence ids [E#] that exist and document ids that appear in the
evidence; otherwise it is discarded.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from okc.claims.analysis import ClaimAnalysis
from okc.common.text import IDENTIFIER_RE
from okc.credibility.models import CredibilityResult, EvidenceItem

log = logging.getLogger(__name__)
TEMPLATE_VERSION = "explain-template-v1"
NARRATIVE_PROMPT_VERSION = "narrative-v1"
_MAX_NARRATIVE_CHARS = 1500

_VERDICT_TEXT = {
    "SUPPORTED": "The available evidence supports this claim.",
    "PARTIALLY_SUPPORTED": "The claim is only partly supported: the support is weak, indirect or covers only part of the claim.",
    "CONTESTED": "The evidence conflicts and does not settle the claim.",
    "CONTRADICTED": "The available evidence contradicts this claim.",
    "INSUFFICIENT_EVIDENCE": "There is not enough relevant evidence to assess this claim.",
}
_LIMITATIONS = [
    "Only the indexed corpus was searched; evidence outside it is unknown to the system.",
    "Evidence labels come from automated classification and may be wrong; inspect each passage.",
    "Authority and temporal weights are configurable assumptions (config/credibility.yaml).",
]


def _ref(i: int, it: EvidenceItem) -> dict[str, Any]:
    return {"evidence": f"E{i + 1}", "document_id": it.ctx.document_id, "section_id": it.ctx.section_id,
            "scope": it.judgement.scope, "weight": round(it.weight, 3), "why": it.judgement.rationale}


def _claim_type_reason(analysis: ClaimAnalysis) -> str | None:
    if analysis.claim_type == "normative":
        return ("This is a claim about a rule, so documented policies/procedures and recorded decisions carry the most "
                "weight; observed practice is reported separately as a caveat.")
    if analysis.claim_type == "practice":
        return ("This is a claim about practice, so records and first-hand accounts carry the most weight; a written "
                "rule says what should happen, not what does.")
    return None


def build_explanation(analysis: ClaimAnalysis, items: list[EvidenceItem], result: CredibilityResult) -> dict[str, Any]:
    ranked = lambda label: sorted([(i, it) for i, it in enumerate(items) if it.judgement.label == label],
                                  key=lambda x: -x[1].weight)
    support, contradict = ranked("SUPPORTS"), ranked("CONTRADICTS")
    ambiguous = [(i, it) for i, it in enumerate(items) if it.judgement.label == "AMBIGUOUS"]

    reasons: list[str] = []
    if support:
        top = support[0][1]
        reasons.append(f"Strongest support: E{support[0][0] + 1} from {top.ctx.document_id} ({top.judgement.scope}, "
                       f"weight {top.weight:.2f}). {result.independent_support} independent support lineage(s) in total.")
    if contradict:
        top = contradict[0][1]
        reasons.append(f"Strongest contradiction: E{contradict[0][0] + 1} from {top.ctx.document_id} ({top.judgement.scope}, "
                       f"weight {top.weight:.2f}). {result.independent_contradiction} independent contradicting lineage(s).")
    if not support and not contradict:
        reasons.append("No retrieved passage clearly supports or contradicts the claim.")
    reasons.append(f"Weighted support {result.support_mass:.2f} vs contradiction {result.contradict_mass:.2f} gives a heuristic "
                   f"credibility score of {result.score:.2f} (evidence-weight interval {result.interval[0]:.2f}-{result.interval[1]:.2f}).")
    type_reason = _claim_type_reason(analysis)
    if type_reason:
        reasons.append(type_reason)
    for g in result.governance:
        reasons.append(f"Conflict between {', '.join(g.supporting)} and {', '.join(g.contradicting)}: {g.explanation}")

    summary = _VERDICT_TEXT.get(result.verdict, result.verdict)
    if support:
        summary += f" Support comes mainly from {', '.join(dict.fromkeys(it.ctx.document_id for _, it in support[:3]))}."
    if contradict:
        summary += f" Contradicting evidence comes from {', '.join(dict.fromkeys(it.ctx.document_id for _, it in contradict[:3]))}."
    if result.needs_review:
        summary += " Flagged for human review."

    return {
        "generated_by": TEMPLATE_VERSION,
        "summary": summary,
        "verdict": result.verdict,
        "claim_type": analysis.claim_type,
        "claim_type_reason": analysis.claim_type_reason,
        "reasons": reasons,
        "key_supporting": [_ref(i, it) for i, it in support[:5]],
        "key_contradicting": [_ref(i, it) for i, it in contradict[:5]],
        "ambiguous": [_ref(i, it) for i, it in ambiguous[:5]],
        "temporal": result.temporal_notes,
        "governance": [g.model_dump() for g in result.governance],
        "caveats": result.caveats,
        "uncertainty": (f"Heuristic evidence-weight interval {result.interval[0]:.2f}-{result.interval[1]:.2f}; "
                        f"coverage of the claim's key elements {result.coverage:.0%}; conflict ratio {result.conflict:.2f}. "
                        "These summarise the evidence considered; they are not calibrated probabilities."),
        "missing_information": result.missing_information,
        "next_steps": result.next_steps,
        "limitations": list(_LIMITATIONS),
    }


def llm_narrative(explanation: dict[str, Any], items: list[EvidenceItem], host: str, model: str,
                  timeout_s: float = 90.0) -> dict[str, Any] | None:
    """Optional 2-4 sentence paraphrase, validated against the recorded evidence."""
    facts = {k: explanation[k] for k in ("summary", "reasons", "temporal", "caveats", "missing_information")}
    allowed_evidence = {f"E{i + 1}" for i in range(len(items))}
    allowed_documents = {it.ctx.document_id for it in items}
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
    documents = {m.group(0) for m in IDENTIFIER_RE.finditer(text)}
    if not (cited <= allowed_evidence and documents <= allowed_documents and len(text) < _MAX_NARRATIVE_CHARS):
        return {"text": None, "model": model, "prompt_version": NARRATIVE_PROMPT_VERSION, "validated": False,
                "rejected_reason": f"cited unknown references: {sorted((cited - allowed_evidence) | (documents - allowed_documents))}"}
    return {"text": text, "model": model, "prompt_version": NARRATIVE_PROMPT_VERSION, "validated": True}
