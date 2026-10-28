"""Folders are independent: one folder waiting, failing or running never holds another."""

from __future__ import annotations

import threading
import time

import desktop_helpers as dh
from test_cost import make_external_app

from decision_agent.desktop.app import DecisionAgentApp, Job
from decision_agent.desktop.jobs import AUTO, USER, JobBoard
from decision_agent.desktop.notify import CallbackNotifier
from decision_agent.desktop.providers import ProviderBundle
from decision_agent.desktop.state import StateDB
from decision_agent.llm.mock_provider import MockProvider


def two_folders(tmp_path, file_dbs: bool = False, **overrides):
    a, b = tmp_path / "folder_a", tmp_path / "folder_b"
    if not a.exists():
        a, b = dh.make_watch_dir(tmp_path, "folder_a"), dh.make_watch_dir(tmp_path, "folder_b")
    settings = dh.make_settings(a, **{"confirm_over_calls": 1, **overrides})
    settings.allowed_folders = [str(a), str(b)]
    dbs = {}

    def db_for(root):
        if not file_dbs:
            return StateDB(":memory:")
        return StateDB(tmp_path / f"{root.name}.db")

    app = DecisionAgentApp(
        settings,
        notifier=CallbackNotifier(lambda title, body: None),
        bundle_factory=lambda s: ProviderBundle(MockProvider(), "fake:external", False, "test", True),
        db_factory=db_for,
        start_watchers=False,
    )
    return app, str(a.resolve()), str(b.resolve()), dbs


def state(app, root) -> dict:
    return next(f["state"] for f in app.status()["folders"] if f["root"] == root)


def analyses(app, root) -> list[bool]:
    """Record each analyze() of one folder (True when confirmed)."""
    engine = app.runtimes[root].engine
    seen: list[bool] = []
    original = engine.analyze

    def counted(*args, **kwargs):
        seen.append(bool(kwargs.get("confirm")))
        return original(*args, **kwargs)

    engine.analyze = counted
    return seen


def test_confirming_one_folder_runs_only_that_folder(tmp_path):
    app, a, b, _ = two_folders(tmp_path)
    app.start()
    try:
        assert app.wait_idle(60)
        assert state(app, a)["state"] == "awaiting_confirm" and state(app, b)["state"] == "awaiting_confirm"
        assert app.status()["overall"]["label"] == "폴더 2개 모두 확인을 기다립니다."
        seen_a, seen_b = analyses(app, a), analyses(app, b)
        app.confirm(b)
        assert app.wait_idle(60)
        assert seen_b == [True] and seen_a == []
        assert state(app, a)["state"] == "awaiting_confirm" and state(app, b)["state"] != "awaiting_confirm"
        assert app.status()["overall"]["label"] == "폴더 2개 중 1개가 확인을 기다립니다."
        # B is edited while A still waits: B is checked and finishes, A is not touched.
        (dh.Path(b) / "zz_note.md").write_text("# 메모\n\n- 서버 로그는 30일 보관\n", encoding="utf-8")
        app.enqueue(Job("sync", b))
        assert app.wait_idle(60)
        assert len(seen_b) == 2 and seen_a == []
        assert state(app, b)["state"] in {"verified", "demo"}
        app.confirm(a)
        assert app.wait_idle(60)
        assert seen_a == [True] and len(seen_b) == 2
        assert app.status()["overall"]["label"] in {"검사 완료. 폴더 2개 모두 최신입니다.",
                                                    "데모 확인 방식으로만 점검했습니다. 실제 검사가 아닙니다."}
    finally:
        app.stop()


def test_a_long_check_in_one_folder_does_not_hold_another(tmp_path):
    app, a, b, _ = two_folders(tmp_path, confirm_over_calls=0)
    app.start()
    try:
        assert app.wait_idle(60)
        release = threading.Event()
        engine = app.runtimes[a].engine
        original = engine.analyze

        def slow(*args, **kwargs):
            release.wait(20)
            return original(*args, **kwargs)

        engine.analyze = slow
        app.enqueue(Job("reanalyze", a))
        time.sleep(0.2)
        assert state(app, a)["state"] == "running"
        done_b = analyses(app, b)
        app.enqueue(Job("reanalyze", b))
        end = time.time() + 20
        while not done_b and time.time() < end:
            time.sleep(0.05)
        assert done_b and state(app, a)["state"] == "running"
        release.set()
        assert app.wait_idle(60)
    finally:
        app.stop()


def test_later_keeps_the_estimate_and_stops_estimating_again_and_survives_a_restart(tmp_path):
    app, a, _b, _ = two_folders(tmp_path, file_dbs=True)
    app.start()
    try:
        assert app.wait_idle(60)
        estimate = app.runtimes[a].engine.pending_confirm()
        app.confirm_later(a)
        seen = analyses(app, a)
        (dh.Path(a) / "zz_note.md").write_text("# 메모\n\n- 새 파일\n", encoding="utf-8")
        app.enqueue(Job("sync", a))
        app.enqueue(Job("reanalyze", a))
        assert app.wait_idle(60)
        assert seen == [] and app.runtimes[a].engine.pending_confirm() == estimate
        assert state(app, a)["later"] is True
    finally:
        app.stop()
    again, a2, _, _ = two_folders(tmp_path, file_dbs=True)
    again.start()
    try:
        assert again.wait_idle(60)
        waiting = state(again, a2)
        assert waiting["state"] == "awaiting_confirm" and waiting["later"] is True
        assert "AI 요청 약" in waiting["detail"]
        # The user's own check runs it and clears "나중에".
        again.confirm(a2)
        assert again.wait_idle(60)
        assert state(again, a2)["state"] != "awaiting_confirm"
    finally:
        again.stop()


def test_stop_keeps_stored_verdicts_and_pauses_the_folder(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, _ = make_external_app(root, confirm_over_calls=0)
    app.start()
    try:
        assert app.wait_idle(60)
        key = next(iter(app.runtimes))
        runtime = app.runtimes[key]
        stored = runtime.db.conn.execute("SELECT COUNT(*) FROM line_verdicts").fetchone()[0]
        assert stored > 0
        app.stop_folder(key)
        assert state(app, key)["state"] == "paused"
        # Automatic jobs wait while paused; nothing stored is lost.
        app.enqueue(Job("reanalyze", key))
        time.sleep(0.3)
        assert key in app.jobs.queued_roots()
        assert runtime.db.conn.execute("SELECT COUNT(*) FROM line_verdicts").fetchone()[0] == stored
        # "지금 점검" resumes, and the stopped engine checks again normally.
        app.rescan(key)
        assert app.wait_idle(60)
        assert state(app, key)["state"] in {"verified", "demo"}
        assert not runtime.engine.cancelled.is_set()
    finally:
        app.stop()


def test_user_jobs_go_first_and_one_folder_runs_one_job_at_a_time():
    board = JobBoard()
    board.put(Job("sync", "a", priority=AUTO))
    board.put(Job("sync", "b", priority=AUTO))
    board.put(Job("confirm", "b", priority=USER))
    first = board.take()
    assert (first.kind, first.root) == ("confirm", "b")
    second = board.take()
    assert second.root == "a"
    # b is running, so its waiting sync is not handed out until b is done.
    taken: list = []
    worker = threading.Thread(target=lambda: taken.append(board.take()), daemon=True)
    worker.start()
    time.sleep(0.2)
    assert taken == []
    board.done(first)
    worker.join(5)
    assert taken[0].root == "b"
    board.done(second)
    board.done(taken[0])
    assert board.idle()


def test_exclusive_waits_for_running_jobs():
    board = JobBoard()
    board.put(Job("sync", "a"))
    job = board.take()
    entered = threading.Event()

    def settings_change():
        with board.exclusive():
            entered.set()

    thread = threading.Thread(target=settings_change, daemon=True)
    thread.start()
    assert not entered.wait(0.3)
    board.done(job)
    assert entered.wait(5)


def test_summary_counts_folders_instead_of_one_state(tmp_path):
    app, _a, _b, _ = two_folders(tmp_path)
    record = {"provider_id": "fake:external", "cost_profile": app.settings.cost_profile, "verified": True,
              "files_ok": 3, "topics": 2, "at": "2026-10-28T10:00:00", "local_answers": 0}

    def folder(name):
        return {"state": {"state": name, "label": "", "detail": ""}, "completion": record}

    assert app.overall([folder("verified"), folder("verified")])["label"] == "검사 완료. 폴더 2개 모두 최신입니다."
    assert app.overall([folder("awaiting_confirm"), folder("verified")])["label"] == "폴더 2개 중 1개가 확인을 기다립니다."
    assert app.overall([folder("awaiting_confirm"), folder("awaiting_confirm")])["label"] == "폴더 2개 모두 확인을 기다립니다."
    assert app.overall([folder("verified")])["label"] == "검사 완료. 폴더가 최신입니다."
    demo = dict(record, verified=False)
    mixed = [folder("verified"), {"state": {"state": "demo", "label": "", "detail": ""}, "completion": demo}]
    assert "검사 완료" not in app.overall(mixed)["label"]
