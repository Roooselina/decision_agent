from __future__ import annotations

import sqlite3

import desktop_helpers as dh

from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.progress import ActivityLog, ProgressTracker
from decision_agent.desktop.providers import ProviderBundle, describe_llm_error
from decision_agent.desktop.state import StateDB
from decision_agent.llm.base import LLMProvider
from decision_agent.llm.mock_provider import MockProvider


class AuthenticationError(RuntimeError):
    """Same class name as the SDK error, so the program treats it as a bad key."""


class RejectingProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system, user, temperature=0.0, json_schema=None):
        self.calls += 1
        raise AuthenticationError("invalid x-api-key")


def make_app(root, bundle, **overrides):
    return DecisionAgentApp(
        dh.make_settings(root, **overrides),
        bundle_factory=lambda s: bundle,
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
    )


def real_bundle():
    """A stand-in for a real model: marked as not local_only, answers like the mock judge."""
    return ProviderBundle(MockProvider(), "fake:real-model", False, "test", True)


def test_tracker_counts_and_reports_changes():
    seen = []
    tracker = ProgressTracker(on_change=lambda: seen.append(1), activity=ActivityLog())
    tracker.begin()
    tracker.set_phase("reading")
    tracker.set_files(3)
    tracker.file_done("a.md")
    tracker.file_done("b.md")
    tracker.set_topics(2)
    tracker.topic_done("t1", unchanged=True)
    tracker.set_llm_calls(7)
    snap = tracker.snapshot()
    assert snap["files_total"] == 3 and snap["files_done"] == 2
    assert snap["topics_total"] == 2 and snap["topics_done"] == 1 and snap["topics_unchanged"] == 1
    assert snap["llm_calls"] == 7
    assert snap["phase"] == "reading"
    assert seen


def test_activity_log_keeps_only_the_latest_entries():
    log = ActivityLog(limit=3)
    for index in range(5):
        log.add(f"line {index}")
    texts = [row["text"] for row in log.recent(10)]
    assert texts == ["line 4", "line 3", "line 2"] or texts == ["line 2", "line 3", "line 4"]


def test_demo_judge_is_never_reported_as_a_verified_check(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app = DecisionAgentApp(
        dh.make_settings(root),
        bundle_factory=lambda s: ProviderBundle(MockProvider(), "mock", True, "demo"),
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
    )
    app.start()
    try:
        assert app.wait_idle(60)
        status = app.status()
        assert status["overall"]["state"] == "demo"
        assert "실제 검사가 아닙니다" in status["overall"]["label"]
        folder = status["folders"][0]
        assert folder["completion"]["verified"] is False
        assert folder["progress"]["files_total"] == folder["progress"]["files_done"] > 0
        assert folder["progress"]["topics_total"] == folder["progress"]["topics_done"] > 0
    finally:
        app.stop()


def test_real_model_run_ends_in_verified_with_counts(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app = make_app(root, real_bundle())
    app.start()
    try:
        assert app.wait_idle(60)
        status = app.status()
        assert status["overall"]["state"] == "verified"
        assert status["overall"]["label"] == "검사 완료. 폴더가 최신입니다."
        assert status["folders"][0]["state"]["label"] == "검사 완료"
        record = status["folders"][0]["completion"]
        assert record["verified"] is True and record["provider_id"] == "fake:real-model"
        assert record["files_ok"] == 5 and record["topics"] == 3
        assert status["folders"][0]["failure"] is None
    finally:
        app.stop()


def test_changing_the_judge_marks_the_old_result_stale(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    holder = {"bundle": ProviderBundle(MockProvider(), "mock", True, "demo")}
    app = DecisionAgentApp(
        dh.make_settings(root),
        bundle_factory=lambda s: holder["bundle"],
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
    )
    app.start()
    try:
        assert app.wait_idle(60)
        assert app.status()["overall"]["state"] == "demo"
        holder["bundle"] = real_bundle()
        app.bundle = holder["bundle"]
        assert app.status()["overall"]["state"] == "stale"
    finally:
        app.stop()


def test_rejected_key_is_reported_as_failed_and_not_retried(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    provider = RejectingProvider()
    bundle = ProviderBundle(provider, "fake:bad-key", False, "test", True)
    app = make_app(root, bundle)
    app.start()
    try:
        assert app.wait_idle(60)
        status = app.status()
        assert status["overall"]["state"] == "failed"
        assert "API 키" in status["overall"]["detail"]
        assert status["folders"][0]["completion"] is None
        calls = provider.calls
        assert calls > 0
        app.wait_idle(5)
        assert provider.calls == calls
        assert any(row["level"] == "error" for row in app.activity.recent(50))
    finally:
        app.stop()


def test_error_descriptions_distinguish_fatal_from_temporary():
    message, fatal = describe_llm_error(AuthenticationError("x"))
    assert fatal and "API 키" in message
    class RateLimitError(RuntimeError):
        pass
    message, fatal = describe_llm_error(RateLimitError("slow down"))
    assert not fatal
    class OllamaModelMissing(RuntimeError):
        pass
    message, fatal = describe_llm_error(OllamaModelMissing("no model"))
    assert fatal and "ollama pull" in message
    message, fatal = describe_llm_error(ValueError("odd"))
    assert not fatal and "odd" in message


def test_old_state_database_gets_the_new_columns(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE topics (
            topic TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, decision_fp TEXT NOT NULL,
            state TEXT NOT NULL, checked_at TEXT NOT NULL, summary TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE findings (
            topic TEXT NOT NULL, artifact_id TEXT NOT NULL, kind TEXT, text TEXT, source_doc TEXT,
            location TEXT, label TEXT NOT NULL, confidence REAL, evidence TEXT, action TEXT,
            decision_id TEXT, status TEXT NOT NULL DEFAULT 'open', snooze_until TEXT,
            first_seen TEXT, last_seen TEXT, notified INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (topic, artifact_id)
        );
        INSERT INTO topics VALUES ('t', 'f', 'd', 'ok', '2026-01-01T00:00:00', '');
        """
    )
    conn.commit()
    conn.close()
    db = StateDB(path)
    try:
        topic_columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(topics)")}
        finding_columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(findings)")}
        assert "judge" in topic_columns and "owner" in finding_columns
        assert [row["topic"] for row in db.list_topics()] == ["t"]
    finally:
        db.close()
