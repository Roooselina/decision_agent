"""Parallel topic checking inside the app: cancelling on stop and on folder removal."""

from __future__ import annotations

import threading
import time

import desktop_helpers as dh
import pytest

from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.providers import (
    CheckCancelled,
    CountingProvider,
    ProviderBundle,
    task_of,
)
from decision_agent.desktop.state import StateDB
from decision_agent.llm.base import LLMProvider
from decision_agent.llm.mock_provider import MockProvider


class Slow(LLMProvider):
    """The mock model with a delay; calls `hook` with the task name of each call."""

    def __init__(self, delay=0.0, hook=None):
        self.inner = MockProvider()
        self.delay = delay
        self.hook = hook
        self.calls = 0
        self.topic_stage = threading.Event()
        self.lock = threading.Lock()

    def complete(self, system, user, temperature=0.0, json_schema=None):
        task = task_of(system)
        with self.lock:
            self.calls += 1
        if task == "IMPACT_SELECT":
            self.topic_stage.set()
        if self.hook:
            self.hook(task)
        if self.delay:
            time.sleep(self.delay)
        return self.inner.complete(system, user, temperature, json_schema)


def topic_threads():
    return [t for t in threading.enumerate() if t.name.startswith("topic")]


def test_cancelled_counter_raises_before_spending_a_call():
    flag = threading.Event()
    inner = Slow()
    counter = CountingProvider(inner, 100, 4, flag)
    counter.complete("TASK:IMPACT_SELECT\nx", "u")
    flag.set()
    with pytest.raises(CheckCancelled):
        counter.complete("TASK:IMPACT_SELECT\nx", "u")
    assert counter.calls == 1 and inner.calls == 1


def test_cancel_during_the_topic_stage_stores_nothing_more_and_records_no_failure(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    holder = {}

    def hook(task):
        if task == "IMPACT_SELECT":
            holder["engine"].cancel()

    settings = dh.make_settings(root, confirm_over_calls=0)
    db = StateDB(":memory:")
    engine = ProjectEngine(root, settings, db, ProviderBundle(Slow(hook=hook), "fake:external", False, "t", True))
    holder["engine"] = engine
    engine.sync()
    report = engine.analyze()
    assert report.partial and engine.completion() is None
    assert engine.last_failure() is None and report.topics_checked == []
    assert db.list_findings(include_hidden=True) == []
    assert not topic_threads()


def slow_app(root, delay):
    slow = Slow(delay)
    app = DecisionAgentApp(
        dh.make_settings(root, confirm_over_calls=0, provider="anthropic"),
        bundle_factory=lambda s: ProviderBundle(slow, "fake:external", False, "t", True),
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
    )
    return app, slow


def wait_for_topic_stage(slow, timeout=60):
    assert slow.topic_stage.wait(timeout)


def test_stopping_the_app_mid_check_ends_quickly_without_threads(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, slow = slow_app(root, 1.0)
    app.start(initial_scan=False)
    app.rescan()
    wait_for_topic_stage(slow)
    started = time.perf_counter()
    app.stop()
    assert time.perf_counter() - started < 2.5
    assert not app._worker.is_alive()
    assert not topic_threads()


def test_removing_a_folder_mid_check_does_not_wait_for_the_whole_check(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, slow = slow_app(root, 1.0)
    app.start(initial_scan=False)
    try:
        app.rescan()
        wait_for_topic_stage(slow)
        before = slow.calls
        started = time.perf_counter()
        assert app.remove_folder(str(root)) is True
        assert time.perf_counter() - started < 2.5
        assert app.runtimes == {} and not topic_threads()
        # In-flight calls may finish, but no new ones start after the cancel.
        assert slow.calls - before <= app.settings.max_parallel_calls
    finally:
        app.stop()
