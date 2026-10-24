from __future__ import annotations

import time

import desktop_helpers as dh

from decision_agent.desktop.app import DecisionAgentApp, Job
from decision_agent.desktop.notify import CallbackNotifier
from decision_agent.desktop.providers import build_provider
from decision_agent.desktop.state import StateDB


def make_app(root, watchers=False, **overrides):
    toasts = []
    settings = dh.make_settings(root, **overrides)
    app = DecisionAgentApp(
        settings,
        notifier=CallbackNotifier(lambda t, m: toasts.append((t, m))),
        bundle_factory=lambda s: build_provider(s, key_getter=lambda: None),
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=watchers,
    )
    return app, toasts


def test_initial_scan_produces_findings_and_one_summary_toast(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, toasts = make_app(root)
    app.start()
    try:
        assert app.wait_idle(60)
        labels = {f["text"]: f["label"] for f in app.findings()}
        assert labels["MVP 이메일 인증 개발 진행 중"] == "INCONSISTENT"
        assert [t for t, _ in toasts].count("처음 점검을 마쳤습니다") == 1
        assert len(toasts) <= 3
        status = app.status()
        assert status["inconsistent"] >= 3 and status["questions"] == 1
        assert app.questions()[0]["topic"] == "presentation_schedule"
        before = len(toasts)
        app.rescan()
        assert app.wait_idle(60)
        assert len(toasts) == before
    finally:
        app.stop()


def test_notifications_can_be_switched_off(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, toasts = make_app(root, notify=False)
    app.start()
    try:
        assert app.wait_idle(60)
        assert toasts == []
    finally:
        app.stop()


def test_dismiss_hides_a_finding_and_survives_rescan(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, _ = make_app(root)
    app.start()
    try:
        assert app.wait_idle(60)
        target = next(f for f in app.findings() if f["label"] == "INCONSISTENT")
        assert app.set_finding_status(target["root"], target["topic"], target["artifact_id"], "dismissed")
        assert all(f["artifact_id"] != target["artifact_id"] for f in app.findings())
        app.rescan()
        assert app.wait_idle(60)
        assert all(f["artifact_id"] != target["artifact_id"] for f in app.findings())
        assert any(f["artifact_id"] == target["artifact_id"] for f in app.findings(include_hidden=True))
    finally:
        app.stop()


def test_pause_defers_and_resume_merges_jobs(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, _ = make_app(root)
    app.start(initial_scan=False)
    try:
        key = next(iter(app.runtimes))
        engine = app.runtimes[key].engine
        calls = []
        original = engine.process

        def spy(paths=None):
            calls.append(paths)
            return original(paths)

        engine.process = spy
        app.pause()
        first, second = root / "tasks.csv", root / "schedule.csv"
        app.enqueue(Job("sync", key, {first}))
        app.enqueue(Job("sync", key, {second}))
        assert len(app.deferred) == 2 and app.status()["paused"]
        app.resume()
        assert app.deferred == []
        assert app.wait_idle(60)
        assert len(calls) == 1 and calls[0] == {first, second}
        assert {r["path"] for r in app.runtimes[key].db.list_files()} >= {"tasks.csv", "schedule.csv"}
    finally:
        app.stop()


def test_answer_flow_clears_the_question(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, _ = make_app(root)
    app.start()
    try:
        assert app.wait_idle(60)
        question = app.questions()[0]
        app.answer(question["root"], question["topic"], "발표 일정만 변경됩니다.")
        assert app.wait_idle(60)
        assert app.questions() == []
    finally:
        app.stop()


def test_file_change_with_real_watcher_triggers_recheck_and_toast(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, toasts = make_app(root, watchers=True, debounce_seconds=0.3)
    app.start()
    try:
        assert app.wait_idle(60)
        time.sleep(0.5)
        toasts.clear()
        with (root / "tasks.csv").open("a", encoding="utf-8") as f:
            f.write("TASK_07,MVP 이메일 인증 UI 개발,TODO,C,mvp,login,,\n")
        end = time.monotonic() + 30
        while time.monotonic() < end and not toasts:
            time.sleep(0.1)
        assert app.wait_idle(60)
        assert len(toasts) == 1 and "이메일 인증" in toasts[0][1]
    finally:
        app.stop()


def test_add_and_remove_folder_reconfigure(tmp_path):
    first = dh.make_watch_dir(tmp_path, "one")
    second = dh.make_watch_dir(tmp_path, "two")
    app, _ = make_app(first)
    app.start()
    try:
        assert app.wait_idle(60)
        added = app.add_folder(str(second))
        assert app.wait_idle(60)
        assert str(added) in app.runtimes and len(app.runtimes) == 2
        assert {f["root"] for f in app.findings()} == {str(first.resolve()), str(second.resolve())}
        assert app.remove_folder(str(second))
        assert app.wait_idle(60)
        assert list(app.runtimes) == [str(first.resolve())]
        assert not app.remove_folder(str(tmp_path / "never"))
    finally:
        app.stop()


def test_missing_folder_is_skipped_not_fatal(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, _ = make_app(root, allowed_folders=[str(root), str(tmp_path / "gone")])
    app.start()
    try:
        assert app.wait_idle(60)
        assert len(app.runtimes) == 1
    finally:
        app.stop()


def test_reconfigure_saves_settings_file(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, _ = make_app(root)
    app.settings_file = tmp_path / "settings.json"
    app.start()
    try:
        assert app.wait_idle(60)
        new = app.settings.model_copy(update={"notify": False})
        app.reconfigure(new)
        assert app.wait_idle(60)
        assert '"notify": false' in app.settings_file.read_text(encoding="utf-8")
    finally:
        app.stop()
