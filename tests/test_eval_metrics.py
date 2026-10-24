from __future__ import annotations

import json

import pandas as pd

from decision_agent.eval import metrics, path_analysis


def make_rows(spec):
    rows = []
    for i, (gold, pred, affected, flagged, conf, trap) in enumerate(spec):
        rows.append(
            {
                "case_id": f"c{i // 2}",
                "level": 1,
                "source": "synthetic",
                "tags": "scope_trap" if trap else "",
                "method": "M",
                "oracle": "none",
                "repeat": 0,
                "artifact_id": f"A{i}",
                "gold_label": gold,
                "gold_affected": affected,
                "gold_clar": False,
                "scope_trap": trap,
                "pred": pred,
                "flagged": flagged,
                "confidence": conf,
                "evidence": "",
            }
        )
    return pd.DataFrame(rows)


def test_confusion_and_f1_treat_uncertain_as_not_positive():
    rows = make_rows(
        [
            ("INCONSISTENT", "INCONSISTENT", True, True, 1.0, False),
            ("INCONSISTENT", "UNCERTAIN", True, True, 0.0, False),
            ("CONSISTENT", "INCONSISTENT", True, True, 1.0, False),
            ("CONSISTENT", "CONSISTENT", True, True, 1.0, False),
        ]
    )
    assert metrics.confusion(rows) == (1, 1, 1)
    assert metrics.f1_from_counts(1, 1, 1) == 0.5


def test_ece_is_zero_when_calibrated_and_positive_when_overconfident():
    confidence = pd.Series([1.0, 1.0, 1.0, 1.0])
    assert metrics.expected_calibration_error(confidence, pd.Series([True] * 4)) == 0.0
    over = metrics.expected_calibration_error(confidence, pd.Series([True, True, False, False]))
    assert abs(over - 0.5) < 1e-9
    assert pd.isna(
        metrics.expected_calibration_error(pd.Series([], dtype=float), pd.Series([], dtype=bool))
    )


def test_bootstrap_interval_brackets_the_point_estimate_and_is_reproducible():
    spec = []
    for i in range(20):
        gold = "INCONSISTENT" if i % 2 == 0 else "CONSISTENT"
        pred = gold if i % 5 else "CONSISTENT"
        spec.append((gold, pred, True, True, 1.0, False))
    rows = make_rows(spec)
    low, high = metrics.bootstrap_f1(rows, 400, seed=1)
    tp, fp, fn = metrics.confusion(rows)
    point = metrics.f1_from_counts(tp, fp, fn)
    assert low <= point <= high
    assert (low, high) == metrics.bootstrap_f1(rows, 400, seed=1)


def test_error_categories():
    rows = make_rows(
        [
            ("INCONSISTENT", "CONSISTENT", True, False, None, False),
            ("CONSISTENT", "INCONSISTENT", True, True, 1.0, True),
            ("CONSISTENT", "INCONSISTENT", True, True, 1.0, False),
            ("INCONSISTENT", "CONSISTENT", True, True, 1.0, False),
            ("INCONSISTENT", "UNCERTAIN", True, True, 0.0, False),
            ("CONSISTENT", "CONSISTENT", True, True, 1.0, False),
        ]
    )
    categories = list(metrics.classify_errors(rows)["category"])
    assert categories == [
        "missed_affected",
        "scope_trap_false_positive",
        "false_positive",
        "missed_inconsistency",
        "uncertain_determinable",
    ]


def test_summary_has_one_row_per_method_and_stage():
    rows = make_rows(
        [
            ("INCONSISTENT", "INCONSISTENT", True, True, 1.0, False),
            ("CONSISTENT", "CONSISTENT", True, True, 1.0, True),
            ("CONSISTENT", "CONSISTENT", False, False, None, False),
            ("INCONSISTENT", "INCONSISTENT", True, True, 1.0, False),
        ]
    )
    runs = pd.DataFrame(
        [
            {
                "method": "M",
                "oracle": "none",
                "questions": 0,
                "llm_calls": 3,
                "tool_calls": 2,
                "elapsed": 0.1,
                "needs_clarify": False,
                "error": "",
            }
        ]
    )
    summary = metrics.summarize(rows, runs, n_boot=50)
    assert len(summary) == 1
    assert summary.loc[0, "f1"] == 1.0
    assert summary.loc[0, "scope_trap_fp_rate"] == 0.0
    assert summary.loc[0, "affected_recall"] == 1.0


def test_path_table_measures_adherence_to_case_needs():
    def run(method, steps, history, scope, clarify):
        return {
            "method": method,
            "oracle": "none",
            "steps": json.dumps(steps),
            "needs_history": history,
            "needs_scope": scope,
            "needs_clarify": clarify,
        }

    runs = pd.DataFrame(
        [
            run(
                "M1c",
                ["predict_affected", "get_decision_history", "check_consistency", "finalize"],
                True,
                False,
                False,
            ),
            run(
                "M1c",
                ["predict_affected", "check_consistency", "check_consistency", "finalize"],
                False,
                False,
                False,
            ),
            run(
                "M1c",
                ["predict_affected", "resolve_scope", "check_consistency", "ask_user", "finalize"],
                False,
                True,
                True,
            ),
            run("P", ["affected_prediction", "finalize"], False, False, False),
        ]
    )
    table = path_analysis.path_table(runs).set_index("method")
    assert list(table.index) == ["M1c"]
    row = table.loc["M1c"]
    assert row["history_when_needed"] == 1.0
    assert row["history_when_not_needed"] == 0.0
    assert row["scope_check_when_needed"] == 1.0
    assert row["ask_when_needed"] == 1.0
    assert abs(row["redundant_checks"] - 1 / 3) < 1e-9
    assert row["distinct_paths"] == 3
