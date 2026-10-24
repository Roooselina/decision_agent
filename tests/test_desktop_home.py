"""Home screen data: week calendar, heartbeat times, last result, and the persistent activity list."""

from __future__ import annotations

import datetime
import itertools
import json

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient

from decision_agent.desktop import app as app_module
from decision_agent.desktop import calendar_view
from decision_agent.desktop.app import DecisionAgentApp, Job
from decision_agent.desktop.progress import ActivityLog
from decision_agent.desktop.providers import build_provider
from decision_agent.desktop.state import StateDB
from decision_agent.desktop.webapp import create_web_app
from decision_agent.models import Artifact, ArtifactKind, DecisionScope, ScopeKind
from decision_agent.sources.readers_text import _ics_clock

TOKEN = "h" * 32
PORT = 8765
HEADERS = {"X-DA-Token": TOKEN}


def make_controller(root, day=datetime.datetime(2026, 9, 21, 9, 0), **kwargs):
    controller = DecisionAgentApp(
        dh.make_settings(root),
        bundle_factory=build_provider,
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
        clock=lambda: day,
        **kwargs,
    )
    controller.start()
    assert controller.wait_idle(60)
    return controller


@pytest.fixture
def home(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    controller = make_controller(root)
    client = TestClient(create_web_app(controller, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}")
    yield client, controller, root
    controller.stop()


def test_week_start_is_monday():
    assert calendar_view.week_start(datetime.date(2026, 9, 23)) == datetime.date(2026, 9, 21)
    assert calendar_view.week_start(datetime.date(2026, 9, 21)) == datetime.date(2026, 9, 21)
    assert calendar_view.week_start(datetime.date(2026, 9, 27)) == datetime.date(2026, 9, 21)


def test_source_names_hide_paths_and_name_calendars():
    assert calendar_view.source_of("@google_calendar/schedule") == ("calendar", "Google 캘린더")
    assert calendar_view.source_of("plans/2026/schedule.csv") == ("file", "schedule.csv")
    assert calendar_view.source_of("@import/team.ics") == ("ics", "team.ics")


def test_build_week_links_decisions_and_points_to_the_nearest_week():
    def art(i, text, date, kind=ArtifactKind.SCHEDULE):
        return Artifact(id=i, kind=kind, text=text, source_doc="schedule.csv", location="1",
                        scope=DecisionScope(scope_kind=ScopeKind.GLOBAL), metadata={"date": date})

    items = [("r", art("a", "발표 일정", "2026-09-23")), ("r", art("b", "리허설", "2026-09-17")),
             ("r", art("c", "날짜 없음", ""))]
    findings = [
        {"artifact_id": "a", "label": "CONSISTENT", "topic": "t", "root": "r", "evidence": ""},
        {"artifact_id": "a", "label": "INCONSISTENT", "topic": "t", "root": "r", "evidence": "다름"},
    ]
    times = {("schedule.csv", "발표 일정", "2026-09-23"): "13:00"}
    week = calendar_view.build_week(datetime.date(2026, 9, 21), items, findings, times, False)
    assert [e["title"] for e in week["events"]] == ["발표 일정"]
    event = week["events"][0]
    assert event["time"] == "13:00" and event["decision"]["label"] == "INCONSISTENT"
    assert week["has_events"] and week["nearest_week"] is None
    empty = calendar_view.build_week(datetime.date(2026, 10, 26), items, findings, times, False)
    assert empty["events"] == [] and empty["nearest_week"] == "2026-09-21"


def test_ics_start_time_is_kept_and_utc_is_converted():
    assert _ics_clock("20260922T130000") == "13:00"
    assert _ics_clock("20260922") == ""
    utc = datetime.datetime(2026, 9, 22, 4, 0, tzinfo=datetime.UTC).astimezone()
    assert _ics_clock("20260922T040000Z") == f"{utc.hour:02d}:{utc.minute:02d}"


def test_calendar_api_returns_demo_schedule_with_linked_decisions(home):
    client, _controller, _ = home
    week = client.get("/api/calendar", headers=HEADERS).json()
    assert week["start"] == "2026-09-21" and week["today"] == "2026-09-21"
    titles = {e["title"] for e in week["events"]}
    assert "발표 일정" in titles
    assert any(e["decision"] for e in week["events"])
    for event in week["events"]:
        assert set(event) == {"id", "title", "date", "time", "kind", "source", "source_name", "decision"}
        assert event["source_name"] == "schedule.csv"
    other = client.get("/api/calendar?start=2026-09-14", headers=HEADERS).json()
    assert other["start"] == "2026-09-14"
    assert client.get("/api/calendar?start=../../x", headers=HEADERS).status_code == 400
    assert client.get("/api/calendar").status_code == 403


def test_imported_ics_events_show_their_time(home):
    _client, controller, _ = home
    ics = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:발표 자료 검토\r\nDTSTART:20260922T130000\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    controller.import_file("team.ics", ics)
    assert controller.wait_idle(60)
    events = controller.calendar_week("2026-09-21")["events"]
    found = [e for e in events if e["title"] == "발표 자료 검토"]
    assert found and found[0]["time"] == "13:00" and found[0]["source"] == "ics"


def test_heartbeat_moves_without_changes_but_analysis_time_does_not(tmp_path, monkeypatch):
    stamps = (f"2026-09-21T09:{m:02d}:00" for m in itertools.count())
    monkeypatch.setattr(app_module, "now_iso", lambda: next(stamps))
    root = dh.make_watch_dir(tmp_path)
    controller = make_controller(root)
    try:
        first = controller.status()["watch"]
        assert first["last_checked"] and first["last_analyzed"]
        controller.enqueue(Job("sync", str(root.resolve())))
        assert controller.wait_idle(60)
        second = controller.status()["watch"]
        assert second["last_checked"] > first["last_checked"]
        assert second["last_analyzed"] == first["last_analyzed"]
        result = controller.status()["last_result"]
        assert result["files"] > 0 and result["topics"] > 0 and result["verified"] is False
    finally:
        controller.stop()


def test_activity_list_survives_a_restart_and_is_trimmed(tmp_path):
    path = tmp_path / "activity.jsonl"
    log = ActivityLog(limit=3, path=path)
    for i in range(8):
        log.add(f"기록 {i}")
    again = ActivityLog(limit=3, path=path)
    assert [i["text"] for i in again.recent()] == ["기록 7", "기록 6", "기록 5"]
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 6
    path.write_text("not json\n" + json.dumps({"time": "t", "level": "info", "text": "ok"}) + "\n",
                    encoding="utf-8")
    assert [i["text"] for i in ActivityLog(path=path).recent()] == ["ok"]


def test_controller_writes_activity_to_the_app_data_file(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    target = tmp_path / "home" / "activity.jsonl"
    controller = make_controller(root, activity_file=target)
    controller.stop()
    assert target.exists() and "점검" in target.read_text(encoding="utf-8")
    assert not any(p.name == "activity.jsonl" for p in root.rglob("*"))


def test_findings_carry_decision_source_date_and_item_date(home):
    client, _controller, _ = home
    rows = client.get("/api/findings?all=1", headers=HEADERS).json()
    assert rows
    for row in rows:
        assert row["decision_text"] and "decision_source" in row and "date" in row
    assert any(r["decision_source"].endswith(".md") for r in rows)
    assert any(r["date"] for r in rows if r["kind"] == "SCHEDULE")


def test_false_positive_reason_goes_to_the_activity_list(home):
    client, controller, _ = home
    row = next(r for r in client.get("/api/findings", headers=HEADERS).json() if r["label"] == "INCONSISTENT")
    body = {"root": row["root"], "topic": row["topic"], "artifact_id": row["artifact_id"],
            "status": "false_positive", "reason": "이미 반영된 일정입니다"}
    assert client.post("/api/finding/status", headers=HEADERS, json=body).status_code == 200
    assert "이미 반영된 일정입니다" in controller.activity.recent(5)[0]["text"]
    too_long = dict(body, reason="x" * 501)
    assert client.post("/api/finding/status", headers=HEADERS, json=too_long).status_code == 422
    hidden = [r for r in client.get("/api/findings", headers=HEADERS).json() if r["artifact_id"] == row["artifact_id"]]
    assert hidden == []
