"""Credibility engine: features, dependence, temporal reasoning, verdicts, explanations."""

from datetime import date

import pytest

from okc.claims.analysis import analyze
from okc.settings import load_config
from okc.credibility.engine import CredibilityEngine
from okc.credibility.models import DocInfo, EvidenceItem, RelationEdge
from okc.credibility.explanation import build_explanation, llm_narrative
from okc.evidence.schemas import EvidenceContext, Judgement


def item(doc, label, scope="formal", strength=0.8, text=None, kind="file", doc_type="policy", people=None, rank=0):
    text = text or f"{doc} passage {rank} about discount approval threshold"
    c = EvidenceContext(section_id=f"{doc}:s{rank}", document_id=doc, doc_title=doc, doc_type=doc_type, doc_kind=kind,
                        status="approved", heading="h", section_role="requirement", source_path=f"{doc}.md", start_line=1,
                        end_line=2, section_text=text, span_start=0, span_end=len(text), relevance=0.6, doc_people=people or [])
    j = Judgement(label=label, scope=scope, strength=strength, directness=1.0 if scope == "formal" else 0.7,
                  modality="requirement", rationale="test", classifier="test")
    return EvidenceItem(ctx=c, judgement=j)


def info(doc, status="approved", flags=(), doc_type="policy", d=date(2025, 3, 1), owner="Owner A"):
    return DocInfo(id=doc, title=doc, doc_type=doc_type, status=status, owner=owner, flags=list(flags), doc_date=d,
                   effective_date=d, superseded_date=None)


@pytest.fixture
def engine():
    return CredibilityEngine(load_config("credibility"))


CLAIM = analyze("Discounts above 15% require Finance approval before the deal is closed.")


def test_supported_by_current_formal_source(engine):
    items = [item("POL-01", "SUPPORTS", text="Any discount of 15% or greater requires Finance approval before the deal is closed.")]
    r = engine.assess(CLAIM, items, {"POL-01": info("POL-01")}, [])
    assert r.verdict == "SUPPORTED" and r.score > 0.7 and r.assessment_kind == "heuristic"
    assert 0 <= r.interval[0] < r.score < r.interval[1] <= 1
    f = items[0].features
    assert set(f) == {"authority", "temporal", "directness", "provenance", "strength"}
    assert items[0].contribution == pytest.approx(r.support_mass)


def test_superseded_evidence_is_discounted_and_explained(engine):
    items = [item("POL-02", "SUPPORTS", text="Discounts of 10% or greater require Finance approval."),
             item("POL-01", "CONTRADICTS", text="Discounts of 15% or greater require Finance approval.")]
    docs = {"POL-02": info("POL-02", status="superseded"), "POL-01": info("POL-01")}
    claim = analyze("Discounts of 10% or more require Finance approval.")
    r = engine.assess(claim, items, docs, [RelationEdge("POL-01", "POL-02", "supersedes", "metadata", "")])
    assert items[0].features["temporal"] < items[1].features["temporal"]
    assert r.verdict == "CONTRADICTED"
    assert any("superseded" in n for n in r.temporal_notes)
    assert r.governance and r.governance[0].resolution == "resolved_by_supersession" and r.governance[0].governing_document == "POL-01"


def test_dependent_repetitions_are_not_independent(engine):
    base = [item("COMMS-02", "SUPPORTS", "practice", doc_type="communication", text="urgent deals get Diane's sign-off directly"),
            item("COMMS-03", "SUPPORTS", "practice", doc_type="communication", text="grab Diane directly for urgent ones"),
            item("MTG-02", "SUPPORTS", "decision", doc_type="meeting_note", text="get her sign-off directly on urgent deals")]
    docs = {"COMMS-02": info("COMMS-02", doc_type="communication"), "COMMS-03": info("COMMS-03", doc_type="communication"),
            "MTG-02": info("MTG-02", doc_type="meeting_note")}
    claim = analyze("In practice the Head of Sales sometimes approves urgent discounts directly.")
    linked = engine.assess(claim, [item(i.ctx.document_id, "SUPPORTS", i.judgement.scope, doc_type=i.ctx.doc_type, text=i.ctx.span)
                                   for i in base], docs,
                           [RelationEdge("COMMS-02", "MTG-02", "follows_up", "metadata", ""), RelationEdge("COMMS-03", "COMMS-02", "depends_on", "metadata", "")])
    independent = engine.assess(claim, base, docs, [])
    assert linked.independent_support == 1 and independent.independent_support == 3
    assert linked.support_mass < independent.support_mass
    assert any("one source lineage" in c for c in linked.caveats)


def test_same_document_spans_share_a_cluster(engine):
    items = [item("POL-01", "SUPPORTS", rank=1), item("POL-01", "SUPPORTS", rank=2)]
    r = engine.assess(CLAIM, items, {"POL-01": info("POL-01")}, [])
    assert r.independent_support == 1 and items[0].cluster_id == items[1].cluster_id


def test_insufficient_and_contested(engine):
    r = engine.assess(CLAIM, [item("X-1", "NEUTRAL")], {"X-1": info("X-1")}, [])
    assert r.verdict == "INSUFFICIENT_EVIDENCE"
    items = [item("INT-07", "SUPPORTS", "testimony", 0.8, doc_type="interview", text="reps close tickets themselves once fixed"),
             item("INT-08", "CONTRADICTS", "testimony", 0.8, doc_type="interview", text="I check with Sam before closing tickets")]
    claim = analyze("In practice support reps close tickets themselves once the customer confirms the fix.")
    r2 = engine.assess(claim, items, {"INT-07": info("INT-07", doc_type="interview"), "INT-08": info("INT-08", doc_type="interview")}, [])
    assert r2.verdict == "CONTESTED" and r2.needs_review


def test_unresolved_formal_conflict_requests_review(engine):
    items = [item("POL-01", "SUPPORTS", text="Head of Finance approves discounts above 15% before close"),
             item("PROC-09", "CONTRADICTS", doc_type="procedure", text="Financial Controller approves discounts above 15% before close")]
    r = engine.assess(CLAIM, items, {"POL-01": info("POL-01"), "PROC-09": info("PROC-09", doc_type="procedure")}, [])
    assert any(g.resolution == "unresolved" for g in r.governance) and r.needs_review
    assert any("governs" in s for s in r.next_steps)
    stale = engine.assess(CLAIM, items, {"POL-01": info("POL-01"),
                                         "PROC-09": info("PROC-09", flags=["potentially_stale"], doc_type="procedure")},
                          [RelationEdge("PROC-09", "POL-01", "implements", "metadata", "")])
    assert stale.governance[0].resolution == "resolved_by_staleness" and stale.governance[0].governing_document == "POL-01"


def test_as_of_excludes_later_evidence(engine):
    items = [item("POL-01", "SUPPORTS")]
    r = engine.assess(CLAIM, items, {"POL-01": info("POL-01", d=date(2025, 3, 1))}, [], as_of=date(2024, 1, 1))
    assert items[0].weight == 0 and r.verdict == "INSUFFICIENT_EVIDENCE"
    assert any("post-dates" in n for n in r.temporal_notes)


def test_explanation_only_references_recorded_evidence(engine):
    items = [item("POL-01", "SUPPORTS", text="Any discount of 15% or greater requires Finance approval before close."),
             item("DEAL-014", "CONTRADICTS", "practice", kind="record", doc_type="crm_record", text="approval role: Diane via Slack")]
    r = engine.assess(CLAIM, items, {"POL-01": info("POL-01"), "DEAL-014": info("DEAL-014", status="record", doc_type="crm_record")}, [])
    ex = build_explanation(CLAIM, items, r)
    refs = {x["evidence"] for x in ex["key_supporting"] + ex["key_contradicting"]}
    assert refs <= {"E1", "E2"} and ex["verdict"] == r.verdict
    assert "not calibrated probabilities" in ex["uncertainty"]


def test_llm_narrative_rejects_unknown_references(monkeypatch):
    import httpx

    def fake_post(*a, **k):
        return httpx.Response(200, json={"message": {"content": "Supported by [E1] and [E9] from POL-99."}},
                              request=httpx.Request("POST", "http://x"))
    monkeypatch.setattr(httpx, "post", fake_post)
    items = [item("POL-01", "SUPPORTS")]
    out = llm_narrative({"summary": "s", "reasons": [], "temporal": [], "caveats": [], "missing_information": []},
                        items, "http://x", "m")
    assert out["validated"] is False and out["text"] is None
