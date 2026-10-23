"""Candidate claim extraction: only rule/decision sections, provenance kept, tentative sources flagged."""

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from okc.claims.candidates import extract_candidates
from okc.db.models import Document


def test_candidates_come_only_from_rules_and_decisions(ingested):
    db, *_ = ingested
    docs = db.scalars(select(Document).options(selectinload(Document.sections))).all()
    cands = extract_candidates(docs)
    assert cands
    by_doc = {c.document_id for c in cands}
    assert "POL-10" in by_doc and "MTG-10" in by_doc
    # interviews and chat threads are never mined as facts
    assert not by_doc & {"INT-20", "INT-21", "COMMS-20", "COMMS-21"}
    pol = [c for c in cands if c.document_id == "POL-10"]
    assert any("manager" in c.text for c in pol) and all(c.kind == "normative" for c in pol)
    assert all(c.section_id.startswith(c.document_id + ":") for c in cands)  # provenance kept
    old = [c for c in cands if c.document_id == "POL-09"]
    assert old and all(c.caution and "superseded" in c.caution for c in old)
    proc = [c for c in cands if c.document_id == "PROC-10"]
    assert proc and all("out of date" in (c.caution or "") for c in proc)


def test_candidates_endpoint(api_client):
    r = api_client.get("/api/v1/claims/candidates", params={"kind": "normative"})
    assert r.status_code == 200
    body = r.json()
    assert body["items"] and all(i["kind"] == "normative" for i in body["items"])
    assert "not established facts" in body["note"]
