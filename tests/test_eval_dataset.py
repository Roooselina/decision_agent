from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from decision_agent.eval.dataset import EvalCase, load_data_dir
from decision_agent.eval.generator import generate_cases

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "eval"


def test_synthetic_set_is_large_and_covers_every_level():
    cases = load_data_dir(DATA, ["synthetic"])
    assert len(cases) >= 60
    assert {c.level for c in cases} == set(range(1, 8))
    assert all(c.source == "synthetic" for c in cases)


def test_assistant_written_set_has_ten_cases_over_all_levels():
    cases = load_data_dir(DATA, ["assistant_written"])
    assert len(cases) == 10
    assert {c.level for c in cases} == set(range(1, 8))
    assert all(c.source == "assistant_written" for c in cases)
    assert all("not independent" in c.author for c in cases)
    assert any(g.needs_clarification for c in cases for g in c.gold)
    assert any(g.scope_trap for c in cases for g in c.gold)


def test_generator_is_deterministic_and_matches_the_committed_file():
    first = [c.model_dump(mode="json") for c in generate_cases(7)]
    second = [c.model_dump(mode="json") for c in generate_cases(7)]
    other = [c.model_dump(mode="json") for c in generate_cases(8)]
    assert first == second
    assert first != other
    committed = json.loads((DATA / "synthetic" / "cases.json").read_text(encoding="utf-8"))
    assert committed == first


def test_every_case_labels_each_artifact_once_and_has_consistent_ids():
    for case in load_data_dir(DATA, ["synthetic", "assistant_written"]):
        assert sorted(g.artifact_id for g in case.gold) == sorted(a.id for a in case.artifacts)
        assert len({a.id for a in case.artifacts}) == len(case.artifacts)
        assert len(case.meetings) == len(case.gold_decisions)


def test_every_clarification_case_has_scripted_user_facts():
    for case in load_data_dir(DATA, ["synthetic", "assistant_written"]):
        if case.needs_clarify():
            assert case.user_facts, case.case_id


def test_gold_must_cover_every_artifact():
    case = load_data_dir(DATA, ["assistant_written"])[0].model_dump(mode="json")
    case["gold"] = case["gold"][:-1]
    with pytest.raises(ValidationError):
        EvalCase.model_validate(case)


def test_relation_must_refer_to_known_decisions():
    case = load_data_dir(DATA, ["assistant_written"])[1].model_dump(mode="json")
    case["gold_relations"][0]["new_id"] = "D99"
    with pytest.raises(ValidationError):
        EvalCase.model_validate(case)


def test_global_scope_restrict_is_rejected():
    case = load_data_dir(DATA, ["assistant_written"])[3].model_dump(mode="json")
    case["gold_relations"][0]["affected_scope"] = {"scope_kind": "GLOBAL"}
    with pytest.raises(ValidationError):
        EvalCase.model_validate(case)


def test_clarification_without_user_facts_is_rejected():
    case = load_data_dir(DATA, ["assistant_written"])[5].model_dump(mode="json")
    case["user_facts"] = []
    with pytest.raises(ValidationError):
        EvalCase.model_validate(case)


def test_duplicate_case_ids_are_rejected(tmp_path):
    from decision_agent.eval.dataset import load_cases

    path = DATA / "assistant_written" / "cases.json"
    with pytest.raises(ValueError):
        load_cases([path, path])
