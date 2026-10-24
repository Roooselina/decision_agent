from __future__ import annotations

from pathlib import Path

import pytest

from decision_agent.config import DEFAULT_SETTINGS
from decision_agent.eval.dataset import load_data_dir
from decision_agent.eval.methods import prepare, run_method
from decision_agent.eval.oracle_provider import GoldOracleProvider
from decision_agent.models import ConsistencyLabel

DATA = Path(__file__).resolve().parents[1] / "data" / "eval"
CASES = load_data_dir(DATA, ["synthetic", "assistant_written"])


def run_oracle(case, method):
    provider = GoldOracleProvider(case)
    prepared = prepare(case, provider, "none")
    return run_method(method, case, prepared, provider, DEFAULT_SETTINGS), prepared


@pytest.mark.parametrize("case", CASES, ids=[c.case_id for c in CASES])
def test_gold_labels_are_reachable_with_a_perfect_reader(case):
    run, prepared = run_oracle(case, "M1c")
    assert prepared.extraction_f1 == 1.0
    gold = case.gold_by_id()
    hard = "retrieval_hard" in case.tags
    for artifact_id, label in run.predictions.items():
        g = gold[artifact_id]
        if hard and g.affected and artifact_id not in run.flagged:
            continue
        if g.affected:
            assert label.value == g.label, (case.case_id, artifact_id, label)
        else:
            assert label != ConsistencyLabel.INCONSISTENT, (case.case_id, artifact_id)


@pytest.mark.parametrize("case", [c for c in CASES if c.needs_clarify()], ids=lambda c: c.case_id)
def test_clarification_cases_ask_exactly_when_the_gold_says_so(case):
    with_q, _ = run_oracle(case, "M1c")
    without, _ = run_oracle(case, "M1")
    assert with_q.questions >= 1
    assert without.questions == 0
    uncertain = [
        a
        for a, g in case.gold_by_id().items()
        if g.needs_clarification and without.predictions[a] == ConsistencyLabel.UNCERTAIN
    ]
    assert uncertain, "without a question the clarification artifacts must stay UNCERTAIN"


@pytest.mark.parametrize(
    "case", [c for c in CASES if not c.needs_clarify()], ids=lambda c: c.case_id
)
def test_cases_without_clarification_need_no_question(case):
    run, _ = run_oracle(case, "M1c")
    assert run.questions == 0
