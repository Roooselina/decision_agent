from __future__ import annotations

from pathlib import Path

import pandas as pd

from decision_agent.eval.dataset import load_data_dir
from decision_agent.eval.runner import evaluate, main
from decision_agent.llm.base import LLMProvider

DATA = Path(__file__).resolve().parents[1] / "data" / "eval"


class BrokenProvider(LLMProvider):
    def complete(self, system, user, temperature=0.0, json_schema=None):
        raise RuntimeError("provider down")


def test_evaluate_produces_rows_and_runs_for_each_method():
    cases = load_data_dir(DATA, ["synthetic"])[:3]
    rows, runs = evaluate(cases, ["B1_tfidf", "M1c"], "mock")
    assert set(rows["method"]) == {"B1_tfidf", "M1c"}
    assert len(rows) == 2 * sum(len(c.artifacts) for c in cases)
    assert len(runs) == 2 * len(cases)
    assert (runs["error"] == "").all()
    assert set(rows["pred"]) <= {"CONSISTENT", "INCONSISTENT", "UNCERTAIN"}


def test_provider_failures_are_recorded_not_raised(monkeypatch):
    from decision_agent.eval import runner

    monkeypatch.setattr(runner, "make_provider", lambda name, seed: BrokenProvider())
    cases = load_data_dir(DATA, ["synthetic"])[:2]
    rows, runs = runner.evaluate(cases, ["M1c", "B3_long_context"], "mock", oracle_stages=["full"])
    assert rows.empty
    assert len(runs) == 4
    assert runs["error"].str.len().gt(0).all()


def test_scope_aware_agent_beats_retrieval_baseline_on_the_mock_set():
    cases = load_data_dir(DATA, ["synthetic"])
    rows, _ = evaluate(cases, ["B1_tfidf", "M1c"], "mock")
    from decision_agent.eval import metrics

    scores = {}
    for method, group in rows.groupby("method"):
        scores[method] = metrics.f1_from_counts(*metrics.confusion(group))
    assert scores["M1c"] > scores["B1_tfidf"] + 0.3


def test_cli_writes_report_files_and_marks_mock_results(tmp_path):
    out = tmp_path / "run"
    code = main(
        [
            "--data-dir",
            str(DATA),
            "--sources",
            "assistant_written",
            "--methods",
            "B1_tfidf,P,M1c",
            "--oracle-stage",
            "none,full",
            "--out",
            str(out),
            "--bootstrap",
            "50",
        ]
    )
    assert code == 0
    for name in (
        "report.md",
        "rows.csv",
        "runs.csv",
        "summary.csv",
        "errors.json",
        "f1_by_method.png",
    ):
        assert (out / name).exists(), name
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "Smoke test only" in text
    assert "Experiment 6" in text
    summary = pd.read_csv(out / "summary.csv")
    assert set(summary["oracle"]) == {"none", "full"}


def test_cli_rejects_unknown_method(tmp_path):
    import pytest

    with pytest.raises(SystemExit):
        main(["--data-dir", str(DATA), "--methods", "nope", "--out", str(tmp_path)])
