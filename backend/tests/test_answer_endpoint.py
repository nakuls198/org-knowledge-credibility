"""POST /answer end to end over the synthetic sample corpus, and the lifespan start-up hook."""

from __future__ import annotations

API = "/api/v1"


def test_answer_checks_a_claim_and_cites_its_sources(api_client):
    r = api_client.post(f"{API}/answer", json={"question": "Leave requests require manager approval.",
                                               "classifier_mode": "rules"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["kind"] == "claim" and a["outcome"] in {"YES", "MOSTLY", "MIXED", "FOUND"}
    assert a["answer"] and a["evidence"] and a["assessment_id"] and a["claim_id"]
    assert {"doc_id", "quote", "role", "path"} <= set(a["evidence"][0])


def test_answer_turns_a_yes_no_question_into_a_claim(api_client):
    a = api_client.post(f"{API}/answer", json={"question": "Do managers approve all leave requests?",
                                               "classifier_mode": "rules"}).json()
    assert a["kind"] in {"claim", "passage"} and a["answer"]


def test_answer_says_so_when_nothing_in_the_files_applies(api_client):
    a = api_client.post(f"{API}/answer", json={"question": "hi there"}).json()
    assert a["kind"] == "none" and a["outcome"] == "UNKNOWN"


def test_answer_reuses_an_up_to_date_assessment(api_client):
    body = {"question": "Leave requests require manager approval.", "classifier_mode": "rules"}
    first = api_client.post(f"{API}/answer", json=body).json()
    second = api_client.post(f"{API}/answer", json=body).json()
    assert first["assessment_id"] == second["assessment_id"]
    fresh = api_client.post(f"{API}/answer", json={**body, "fresh": True}).json()
    assert fresh["assessment_id"] != first["assessment_id"]


def test_answer_validates_its_input(api_client):
    r = api_client.post(f"{API}/answer", json={"question": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_root_and_openapi_list_every_route_group(api_client):
    assert api_client.get("/").json()["api"] == API
    paths = api_client.get("/openapi.json").json()["paths"]
    for expected in ("/api/v1/answer", "/api/v1/claims", "/api/v1/documents", "/api/v1/search", "/api/v1/health",
                     "/api/v1/ingestion/jobs", "/api/v1/evaluation"):
        assert expected in paths, expected


# ---------------------------------------------------------------- what the UI reads

def test_credibility_config_exposes_the_assumptions_the_ui_explains(api_client):
    cfg = api_client.get(f"{API}/config/credibility").json()
    assert cfg["authority"] and {"superseded_current_claim", "potentially_stale", "undated", "review_overdue"} <= set(cfg["temporal"])


def test_overview_counts_source_files(api_client):
    corpus = api_client.get(f"{API}/overview").json()["corpus"]
    assert 0 < corpus["source_files"] <= corpus["documents_total"]
