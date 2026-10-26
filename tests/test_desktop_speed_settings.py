"""Speed settings of the desktop program: defaults, environment overrides, progress and partial checks."""

from __future__ import annotations

import dataclasses

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient

from decision_agent.config import DEFAULT_SETTINGS, Settings
from decision_agent.desktop.app import PARTIAL_LABEL, DecisionAgentApp
from decision_agent.desktop.progress import ProgressTracker
from decision_agent.desktop.providers import build_provider
from decision_agent.desktop.settings import AppSettings, apply_speed_settings
from decision_agent.desktop.state import StateDB
from decision_agent.desktop.webapp import create_web_app

TOKEN = "s" * 32
PORT = 8765
HEADERS = {"X-DA-Token": TOKEN}


@pytest.fixture(autouse=True)
def _restore_default_settings():
    saved = dataclasses.replace(DEFAULT_SETTINGS)
    yield
    for field in dataclasses.fields(Settings):
        setattr(DEFAULT_SETTINGS, field.name, getattr(saved, field.name))


def test_speed_defaults_and_limits():
    settings = AppSettings()
    assert settings.max_llm_calls_per_cycle == 3000
    assert (settings.llm_concurrency, settings.batch_size, settings.adaptive_sampling) == (8, 8, True)
    for bad in ({"llm_concurrency": 0}, {"llm_concurrency": 33}, {"batch_size": 0}):
        with pytest.raises(ValueError):
            AppSettings(**bad)


def test_apply_speed_settings_maps_fields():
    target = Settings()
    values = apply_speed_settings(
        AppSettings(llm_concurrency=3, batch_size=1, adaptive_sampling=False), target, environ={}
    )
    assert values == {"concurrency": 3, "extract_batch": 1, "judge_batch": 1, "adaptive_min_samples": 0}
    assert (target.concurrency, target.extract_batch, target.judge_batch) == (3, 1, 1)
    apply_speed_settings(AppSettings(), target, environ={})
    assert target.adaptive_min_samples == 2 and target.judge_batch == 8


def test_environment_variables_win_over_app_settings():
    target = Settings()
    env = {"DECISION_AGENT_CONCURRENCY": "2", "DECISION_AGENT_JUDGE_BATCH": "0",
           "DECISION_AGENT_ADAPTIVE_MIN": "0", "DECISION_AGENT_EXTRACT_BATCH": "bad"}
    values = apply_speed_settings(AppSettings(llm_concurrency=16, batch_size=4), target, environ=env)
    # A broken value is ignored; batch sizes never drop below one.
    assert values == {"concurrency": 2, "extract_batch": 4, "judge_batch": 1, "adaptive_min_samples": 0}


def test_engine_start_applies_the_speed_settings(tmp_path, monkeypatch):
    for name in ("CONCURRENCY", "EXTRACT_BATCH", "JUDGE_BATCH", "ADAPTIVE_MIN"):
        monkeypatch.delenv(f"DECISION_AGENT_{name}", raising=False)
    root = dh.make_watch_dir(tmp_path)
    dh.make_engine(root, batch_size=1, llm_concurrency=2, adaptive_sampling=False)
    assert (DEFAULT_SETTINGS.concurrency, DEFAULT_SETTINGS.judge_batch) == (2, 1)
    assert DEFAULT_SETTINGS.adaptive_min_samples == 0


def test_progress_reports_extraction_counts(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, _, _ = dh.make_engine(root)
    seen = []
    engine.progress.on_change = lambda: seen.append(engine.progress.snapshot()["extract_done"])
    engine.process()
    snap = engine.progress.snapshot()
    assert snap["extract_total"] > 0 and snap["extract_done"] == snap["extract_total"]
    assert max(seen) == snap["extract_total"]


def test_progress_reset_clears_extraction():
    progress = ProgressTracker()
    progress.set_extract(3, 9)
    assert progress.snapshot()["extract_done"] == 3
    progress.begin()
    assert progress.snapshot()["extract_total"] == 0


def make_controller(root, **overrides):
    controller = DecisionAgentApp(
        dh.make_settings(root, **overrides),
        bundle_factory=build_provider,
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
    )
    controller.start()
    assert controller.wait_idle(60)
    return controller


def test_budget_stop_is_partial_and_never_complete(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    controller = make_controller(root, max_llm_calls_per_cycle=5)
    try:
        status = controller.status()
        assert status["folders"][0]["failure"]["partial"] is True
        assert status["overall"]["state"] == "partial"
        assert status["overall"]["label"] == PARTIAL_LABEL
        assert "검사 완료" not in status["overall"]["label"]
    finally:
        controller.stop()


def test_quick_check_switch_is_the_only_new_editable_field(tmp_path, monkeypatch):
    monkeypatch.delenv("DECISION_AGENT_JUDGE_BATCH", raising=False)
    root = dh.make_watch_dir(tmp_path)
    controller = make_controller(root)
    client = TestClient(create_web_app(controller, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}")
    try:
        assert client.post("/api/settings", headers=HEADERS, json={"batch_size": 1}).status_code == 200
        assert controller.settings.batch_size == 1 and DEFAULT_SETTINGS.judge_batch == 1
        for hidden in ({"llm_concurrency": 4}, {"adaptive_sampling": False}):
            assert client.post("/api/settings", headers=HEADERS, json=hidden).status_code == 400
    finally:
        controller.wait_idle(60)
        controller.stop()
