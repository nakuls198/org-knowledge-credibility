"""The YAML config files carry the organisation-specific wording; code must find every key it reads."""

from __future__ import annotations

import re

import pytest

from okc.claims.actors import Gazetteer
from okc.settings import load_config

CONFIG_FILES = ["answers", "compliance", "credibility", "entities", "ingestion", "reasoning", "retrieval"]


@pytest.mark.parametrize("name", CONFIG_FILES)
def test_every_config_file_loads(name):
    assert load_config(name)


def test_compliance_keys_used_by_the_answers_layer():
    rb = load_config("compliance")
    assert {"finance_approver_role", "stages", "approval_audit_action", "audit_record_type", "crm_approval_marker",
            "discount_new_business", "discount_renewal", "expense_approval", "type_names"} <= set(rb)
    assert {"won", "lost"} <= set(rb["stages"])
    assert rb["discount_new_business"]["below_threshold_approver_role"]
    assert {"decider_role", "consulted_function", "cap_pct", "policy"} <= set(rb["discount_renewal"])
    assert rb["expense_approval"]["auto_approval_markers"]
    for version in rb["discount_new_business"]["versions"]:
        assert {"policy", "section", "effective_from", "threshold_pct", "approver_role", "rule_text"} <= set(version)


def test_answers_config_patterns_compile_and_cover_the_rule_topics():
    cfg = load_config("answers")
    for spec in cfg["scan_subjects"].values():
        re.compile(spec["pattern"])
        assert spec["label"]
    for pattern in cfg["rule_topics"].values():
        re.compile(pattern)
    assert list(cfg["rule_topics"]) == ["expense", "renewal", "discount"]  # the order the answers rely on
    assert all(spec["words"] and spec["width"] for spec in cfg["record_references"].values())


def test_reasoning_lexicons_present():
    cues = load_config("reasoning")["cues"]
    for key in ("approval_predicates", "chat_approval_extras", "deviation", "claim_deviation_words", "compliance",
                "practice", "practice_report_phrases", "belief", "hedge", "proposal", "universal_denial",
                "negation_phrases", "negation_exempt"):
        assert cues[key], key


def test_dead_config_keys_stay_removed():
    assert "negation" not in load_config("reasoning")["cues"]
    assert "disagreement" not in load_config("reasoning")["nli"]
    assert "prompt_version" not in load_config("reasoning")["llm"]
    assert "default_mode" not in load_config("retrieval")


def test_expected_metadata_drives_the_missing_metadata_check():
    expected = load_config("ingestion")["expected_metadata"]
    assert expected["policy"]["severity"] == "warning"
    assert "org_reference" in load_config("ingestion")["undated_exempt_doc_types"]


def test_gazetteer_lookup_resolves_full_and_first_names():
    gz = Gazetteer({"Harriet Doyle": {"role": "Head of Finance", "function": "Finance"},
                    "Diane Okafor": {"role": "Head of Sales", "function": "Sales"}}, load_config("entities"))
    assert gz.lookup("Harriet Doyle") == ("Harriet Doyle", "Head of Finance")
    assert gz.lookup("Diane") == ("Diane Okafor", "Head of Sales")
    assert gz.lookup("Unknown Person") == ("Unknown Person", None)
    assert gz.lookup("") == ("", None)
