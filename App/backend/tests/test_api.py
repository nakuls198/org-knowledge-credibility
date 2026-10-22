"""API: validation, errors, documents, search, claims -> assessment -> evidence -> graph -> reproduce."""

import pytest

API = "/api/v1"


def test_health_and_overview(api_client):
    h = api_client.get(f"{API}/health").json()
    assert h["components"]["database"]["ok"] and h["components"]["search"]["status"] == "in-memory"
    o = api_client.get(f"{API}/overview").json()
    assert o["corpus"]["documents_total"] > 0 and o["corpus"]["is_synthetic_sample"] is True
    assert o["ingestion"]["latest_job"]["status"] == "succeeded"


def test_ingestion_job_and_issue_report(api_client):
    jobs = api_client.get(f"{API}/ingestion/jobs").json()
    job = jobs["items"][0]
    assert job["status"] == "succeeded" and job["stats"]["files_new"] == 9
    assert api_client.get(f"{API}/ingestion/jobs/{job['id']}").json()["id"] == job["id"]
    issues = api_client.get(f"{API}/ingestion/issues", params={"issue_type": "broken_reference"}).json()
    assert issues["total"] >= 1 and issues["items"][0]["document_id"] == "TASK-203"


def test_documents_endpoints(api_client):
    page = api_client.get(f"{API}/documents", params={"doc_type": "policy"}).json()
    assert {d["id"] for d in page["items"]} == {"POL-09", "POL-10"} and page["total"] == 2
    assert all(d["kind"] != "record" for d in api_client.get(f"{API}/documents").json()["items"])
    d = api_client.get(f"{API}/documents/POL-10").json()
    assert d["status"] == "approved" and d["sections"] and d["relationship_counts"]["supersedes"] == 1
    secs = api_client.get(f"{API}/documents/POL-10/sections").json()
    src = api_client.get(f"{API}/documents/POL-10/source").json()
    req = next(s for s in secs if s["id"] == "POL-10:formal-requirements")
    lines = src["content"].split("\n")
    assert lines[req["start_line"] - 1] == "## Formal requirements"  # section offsets locate the original text
    rels = api_client.get(f"{API}/documents/POL-10/relationships").json()
    assert any(r["rel_type"] == "supersedes" and r["direction"] == "outgoing" and r["other_document"]["id"] == "POL-09" for r in rels)
    assert any(r["rel_type"] == "records_decision" and r["is_inferred"] for r in rels)


def test_search_endpoint(api_client):
    r = api_client.get(f"{API}/search", params={"q": "leave approval manager", "doc_type": ["policy"], "limit": 5}).json()
    assert r["hits"] and all(h["doc_type"] == "policy" for h in r["hits"])
    assert {"section_id", "document_id", "source_path", "start_line", "highlights", "explanation", "related_claims"} <= set(r["hits"][0])
    assert api_client.get(f"{API}/search/facets").json()["doc_types"]["policy"] == 2


def test_validation_and_error_format(api_client):
    r = api_client.post(f"{API}/claims", json={"text": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = api_client.get(f"{API}/search", params={"q": "x", "mode": "magic"})
    assert r.status_code == 422
    r = api_client.get(f"{API}/documents/NOPE-1")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert api_client.get(f"{API}/claims/does-not-exist").status_code == 404
    r = api_client.get(f"{API}/documents", params={"limit": 0})
    assert r.status_code == 422


@pytest.fixture(scope="module")
def assessed_claim(api_client):
    r = api_client.post(f"{API}/claims", json={"text": "All leave requests require manager approval.", "classifier_mode": "rules"})
    assert r.status_code == 201, r.text
    claim = r.json()
    detail = api_client.get(f"{API}/claims/{claim['id']}").json()
    assert detail["latest_assessment"]["status"] == "completed", detail  # background task ran
    return detail


def test_end_to_end_assessment(api_client, assessed_claim):
    aid = assessed_claim["latest_assessment"]["id"]
    a = api_client.get(f"{API}/assessments/{aid}").json()
    assert a["verdict"] in {"SUPPORTED", "PARTIALLY_SUPPORTED", "CONTESTED"}
    labels = {e["label"] for e in a["evidence"]}
    assert "SUPPORTS" in labels
    pol = [e for e in a["evidence"] if e["document_id"] == "POL-10" and e["label"] == "SUPPORTS"]
    assert pol and pol[0]["span_line"] and pol[0]["source_path"].endswith("POL-10_leave_policy_v2.md")
    for e in a["evidence"]:  # provenance on every item
        assert e["section_text"][e["span_start"]:e["span_end"]] == e["span_text"]
        assert e["retrieval"]["channels"] and "authority" in e["features"]
    ex = a["explanation"]
    assert ex["summary"] and ex["uncertainty"] and ex["limitations"]
    assert a["versions"]["classifier"]["mode"] == "rules" and a["config_hash"]
    # the injected instruction in INT-21 is just data: it never becomes a SUPPORTS rationale
    assert not any("IGNORE ALL PREVIOUS" in e["rationale"] for e in a["evidence"])


def test_reproduce_and_config(api_client, assessed_claim):
    aid = assessed_claim["latest_assessment"]["id"]
    rep = api_client.get(f"{API}/assessments/{aid}/reproduce").json()
    assert rep["reproducible"] is True and rep["changed_sections_since"] == []
    cfg = api_client.get(f"{API}/assessments/{aid}/config").json()
    assert {"retrieval", "reasoning", "credibility"} <= set(cfg["config"])


def test_graph_integrity_and_filters(api_client, assessed_claim):
    cid = assessed_claim["id"]
    g = api_client.get(f"{API}/claims/{cid}/graph").json()
    ids = {n["id"] for n in g["nodes"]}
    assert "claim" in ids and all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    types = {n["type"] for n in g["nodes"]}
    assert {"claim", "evidence", "document"} <= types
    sup = api_client.get(f"{API}/claims/{cid}/graph", params={"label": ["SUPPORTS"]}).json()
    assert all(n["data"]["label"] == "SUPPORTS" for n in sup["nodes"] if n["type"] == "evidence")
    exp = api_client.get(f"{API}/claims/{cid}/graph", params={"expand": ["POL-09"]}).json()
    assert any(n["id"] == "doc:POL-09" for n in exp["nodes"])


def test_rerun_history_and_listing(api_client, assessed_claim):
    cid = assessed_claim["id"]
    r = api_client.post(f"{API}/claims/{cid}/assessments", json={"classifier_mode": "rules"})
    assert r.status_code == 202
    detail = api_client.get(f"{API}/claims/{cid}").json()
    assert len(detail["assessments"]) == 2
    listing = api_client.get(f"{API}/claims", params={"q": "leave"}).json()
    assert listing["total"] >= 1
    ev = api_client.get(f"{API}/claims/{cid}/evidence", params={"label": ["SUPPORTS"]}).json()
    assert ev["items"] and all(e["label"] == "SUPPORTS" for e in ev["items"])
    hit = api_client.get(f"{API}/search", params={"q": "leave requests manager approval"}).json()["hits"]
    assert any(h["related_claims"] for h in hit)  # search results link back to assessments


def test_unsupported_claim_is_insufficient(api_client):
    r = api_client.post(f"{API}/claims", json={"text": "The company offers employees a free gym membership.",
                                               "classifier_mode": "rules"})
    detail = api_client.get(f"{API}/claims/{r.json()['id']}").json()
    assert detail["latest_assessment"]["verdict"] == "INSUFFICIENT_EVIDENCE"
