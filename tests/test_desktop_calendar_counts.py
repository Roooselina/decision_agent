"""Home calendar sources and dates, folder counts from the state database, and UI source checks."""

from __future__ import annotations

import datetime
import logging
import time

import desktop_helpers as dh

from decision_agent.desktop import calendar_view
from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.state import StateDB
from decision_agent.desktop.webpage import all_sources
from decision_agent.models import Artifact, ArtifactKind, DecisionScope, ScopeKind


def art(i, text, date, kind=ArtifactKind.SCHEDULE, source="schedule.csv"):
    return Artifact(id=i, kind=kind, text=text, source_doc=source, location="3",
                    scope=DecisionScope(scope_kind=ScopeKind.GLOBAL), metadata={"date": date})


MONDAY = datetime.date(2026, 10, 19)


def test_calendar_shows_schedule_items_and_calendar_events_only():
    items = [
        ("r", art("s", "발표", "2026-10-24")),
        ("r", art("t", "API 개발", "2026-10-22", kind=ArtifactKind.TASK, source="tasks.csv")),
        ("r", art("g", "주간 회의", "2026-10-20", kind=ArtifactKind.TASK, source="@google_calendar/events")),
        ("r", art("o", "고객 미팅", "2026-10-21", kind=ArtifactKind.REQUIREMENT, source="@outlook_calendar/events")),
    ]
    findings = [{"artifact_id": "s", "label": "INCONSISTENT", "topic": "t", "root": "r", "evidence": ""}]
    week = calendar_view.build_week(MONDAY, items, findings, {}, True)
    assert [(e["title"], e["source"]) for e in week["events"]] == [
        ("주간 회의", "calendar"), ("고객 미팅", "calendar"), ("발표", "file")]
    linked = {e["title"]: e["decision"] for e in week["events"]}
    assert linked["발표"]["label"] == "INCONSISTENT" and linked["주간 회의"] is None


def test_calendar_reads_other_date_forms_and_logs_unreadable_ones_once(caplog):
    assert calendar_view.event_day("2026/10/24") == datetime.date(2026, 10, 24)
    assert calendar_view.event_day("2026년 10월 23일 오후") == datetime.date(2026, 10, 23)
    assert calendar_view.event_day("다음 주") is None and calendar_view.event_day("") is None
    items = [("r", art("a", "비밀 회의 내용", "다음 주 화요일")), ("r", art("b", "점검", "2026/10/22"))]
    with caplog.at_level(logging.INFO, logger="decision_agent.desktop.calendar_view"):
        week = calendar_view.build_week(MONDAY, items, [], {}, False)
        calendar_view.build_week(MONDAY, items, [], {}, False)
    assert [e["title"] for e in week["events"]] == ["점검"]
    logged = [r.getMessage() for r in caplog.records if "no readable date" in r.getMessage()]
    assert len(logged) == 1 and "schedule.csv" in logged[0]
    # The log names the source and the raw date, never the item text.
    assert "비밀 회의" not in logged[0]


def test_empty_week_still_reports_today_week_bounds():
    week = calendar_view.build_week(calendar_view.week_start(datetime.date(2026, 10, 24)), [], [], {}, True)
    assert (week["start"], week["end"]) == ("2026-10-19", "2026-10-25")
    assert week["events"] == [] and week["nearest_week"] is None


def test_calendar_api_starts_on_the_week_of_today(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    controller = DecisionAgentApp(dh.make_settings(root), db_factory=lambda r: StateDB(":memory:"),
                                  start_watchers=False, clock=lambda: datetime.datetime(2026, 10, 24, 10, 0))
    week = controller.calendar_week(None)
    assert (week["start"], week["end"], week["today"]) == ("2026-10-19", "2026-10-25", "2026-10-24")


def test_file_counts_come_from_sql_and_skip_external_documents():
    db = StateDB(":memory:")
    for i in range(5):
        db.upsert_file(f"docs/{i}.md", 1, 1, "h", "md", "ok", "", "x" * 10)
    db.upsert_file("docs/bad.pdf", 1, 1, "h", "pdf", "error", "broken")
    db.upsert_file("@gmail/1", 0, 1, "h", "connector", "ok", "", "{}")
    assert db.file_status_counts() == {"ok": 6, "error": 1}
    assert db.file_status_counts(include_external=False) == {"ok": 5, "error": 1}


def test_folder_cards_and_status_stay_fast_with_many_large_documents(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    db = StateDB(tmp_path / "state.db")
    controller = DecisionAgentApp(dh.make_settings(root), db_factory=lambda r: db, start_watchers=False)
    controller.start(initial_scan=False)
    try:
        big = "x" * 100_000
        for i in range(600):
            db.upsert_file(f"docs/{i}.pdf", 1, 1, "h", "pdf", "ok" if i % 7 else "unsupported", "", big)
        started = time.perf_counter()
        cards = controller.folder_cards()
        status = controller.status()
        elapsed = time.perf_counter() - started
        assert cards[0]["files_ok"] == 514 and cards[0]["files_unsupported"] == 86
        assert status["folders"][0]["files"]["ok"] == 514
        assert elapsed < 1.0
    finally:
        controller.stop()


def test_ui_sources_have_the_calendar_and_result_paging_controls():
    app_js = all_sources()["app.js"]
    for text in ("이 주에는 일정이 없습니다.", "오늘이 있는 주로 돌아가기", "resetCalendar()", "PAGE_ROWS = 50",
                 "더 보기 (", "result-group-head", "aria-expanded"):
        assert text in app_js, text
