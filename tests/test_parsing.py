from pathlib import Path

import pytest

from decision_agent.parsing.csv_parser import parse_schedule, parse_tasks
from decision_agent.parsing.loader import load_project

DEMO = Path(__file__).resolve().parents[1] / "data" / "demo"


def test_demo_loads():
    meetings, artifacts, cfg = load_project(DEMO)
    assert len(meetings) >= 6
    assert len(artifacts) >= 10
    assert cfg["project"].current_release == "mvp"


def test_missing_column(tmp_path):
    path = tmp_path / "tasks.csv"
    path.write_text("id,title\n1,x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_tasks(path)


def test_bad_date(tmp_path):
    path = tmp_path / "schedule.csv"
    path.write_text("id,title,date\n1,x,2026/01/01\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_schedule(path)


def test_csv_scope_columns_and_text_fallback():
    _, artifacts, _ = load_project(DEMO)
    by_id = {a.id: a for a in artifacts}
    assert by_id["TASK_01"].scope.release == "mvp"
    assert by_id["REQUIREMENT_2"].scope.release == "production"
    assert by_id["SCHED_02"].scope.scope_kind.value == "UNKNOWN"
