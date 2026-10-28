"""Cost settings and the confirmation gate as the web API, the helper and the screens see them."""

from __future__ import annotations

import re

import desktop_helpers as dh
from fastapi.testclient import TestClient
from test_cost import make_external_app
from test_desktop_helper import FakeController, make_helper, status

from decision_agent.desktop.helper import doing_line, helper_state
from decision_agent.desktop.webapp import create_web_app
from decision_agent.desktop.webpage import all_sources

TOKEN = "c" * 32
PORT = 8765
HEADERS = {"X-DA-Token": TOKEN}
GATE_TOAST = "점검 확인이 필요합니다"


def client_for(app):
    return TestClient(create_web_app(app, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}")


def waiting_app(tmp_path, **overrides):
    root = dh.make_watch_dir(tmp_path)
    app, toasts = make_external_app(root, confirm_over_calls=1, **overrides)
    app.start()
    assert app.wait_idle(60)
    return app, toasts, next(iter(app.runtimes))


def test_confirm_needs_the_token_a_known_folder_and_then_resumes(tmp_path):
    app, _, root = waiting_app(tmp_path)
    client = client_for(app)
    try:
        assert client.post("/api/confirm", json={"root": root}).status_code in {401, 403}
        assert client.post("/api/confirm", headers={"X-DA-Token": "x" * 32}, json={"root": root}).status_code in {401, 403}
        assert app.status()["overall"]["state"] == "awaiting_confirm"
        missing = client.post("/api/confirm", headers=HEADERS, json={"root": str(tmp_path / "nowhere")})
        assert missing.status_code == 404
        ok = client.post("/api/confirm", headers=HEADERS, json={"root": root})
        assert ok.status_code == 200 and ok.json() == {"ok": True}
        assert app.wait_idle(60)
        assert app.status()["folders"][0]["pending_confirm"] is None
    finally:
        app.stop()


def test_status_json_carries_the_wait_and_the_label_is_never_done(tmp_path):
    app, _, _ = waiting_app(tmp_path)
    client = client_for(app)
    try:
        data = client.get("/api/status", headers=HEADERS).json()
        waiting = data["folders"][0]["pending_confirm"]
        assert set(waiting) == {"estimated_calls", "calls_so_far", "topics", "limit", "reused_lines"}
        assert waiting["limit"] == 1 and waiting["topics"] > 0 and waiting["estimated_calls"] > 1
        assert data["overall"]["state"] == "awaiting_confirm" and data["overall"]["label"] == "폴더가 확인을 기다립니다."
        assert data["folders"][0]["state"]["label"] == "확인 대기"
        assert data["folders"][0]["progress"]["phase"] == "awaiting_confirm"
        assert "검사 완료" not in data["overall"]["label"]
    finally:
        app.stop()


def test_cost_settings_are_checked_saved_and_read_back(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, _ = make_external_app(root, confirm_over_calls=0)
    app.start()
    client = client_for(app)
    try:
        assert app.wait_idle(60)
        for bad in ({"cost_profile": "fast"}, {"cost_profile": ""}, {"confirm_over_calls": -1}):
            assert client.post("/api/settings", headers=HEADERS, json=bad).status_code == 400
        assert app.settings.cost_profile == "balanced"
        payload = {"cost_profile": "saver", "cheap_model_name": "small-model", "confirm_over_calls": 250}
        assert client.post("/api/settings", headers=HEADERS, json=payload).status_code == 200
        assert app.wait_idle(60)
        saved = client.get("/api/settings", headers=HEADERS).json()
        assert {k: saved[k] for k in payload} == payload
    finally:
        app.stop()


def test_a_new_confirm_limit_is_saved_without_a_full_recheck(tmp_path):
    app, _, _ = waiting_app(tmp_path)
    client = client_for(app)
    try:
        before = [r.engine for r in app.runtimes.values()]
        assert client.post("/api/settings", headers=HEADERS, json={"confirm_over_calls": 0}).status_code == 200
        assert app.wait_idle(60)
        # Same engines (no reconfigure), and the waiting check was decided again under the new limit.
        assert [r.engine for r in app.runtimes.values()] == before
        assert app.status()["overall"]["state"] == "verified"
    finally:
        app.stop()


def test_the_gate_toast_is_shown_once_per_wait(tmp_path):
    app, toasts, root = waiting_app(tmp_path)
    try:
        gate = [b for t, b in toasts if t == GATE_TOAST]
        assert len(gate) == 1 and re.fullmatch(r"예상 호출 약 \d+회\. 앱을 열어 계속할지 선택해 주세요\.", gate[0])
        # Automatic checks that stop at the same gate again stay quiet.
        app.rescan(root)
        assert app.wait_idle(60)
        assert [t for t, _ in toasts].count(GATE_TOAST) == 1
        assert app.status()["overall"]["state"] == "awaiting_confirm"
        assert sum(GATE_TOAST in e["text"] for e in app.inbox.recent()) == 1
    finally:
        app.stop()


def test_helper_shows_the_wait_and_opens_home():
    waiting = {"estimated_calls": 520, "calls_so_far": 40, "topics": 12, "limit": 400}
    st = status(overall={"state": "awaiting_confirm", "label": "확인 대기"}, folders=[{"pending_confirm": waiting, "progress": {}}])
    assert helper_state(st) == "attention"
    assert doing_line(st, []) == "확인 대기: 예상 호출 약 520회"
    controller = FakeController()
    controller.status = lambda: st
    opened = []
    helper, _ = make_helper(controller, open_page=opened.append)
    data = helper.state()
    assert data["state_label"] == "확인 대기" and data["awaiting_confirm"] is True
    helper.create()
    assert helper.run_action("home") is True and opened == ["#/home"]


def test_screens_draw_the_new_fields_as_text_only():
    sources = all_sources()
    app_js, helper_js = sources["app.js"], sources["helper.js"]
    for text in ("AI 사용량 절약", "점검 정밀도", "저가 모델 이름 (선택)", "확인 기준 호출 수", "다음 점검 때 전체를 다시 점검합니다",
                 "허용하고 계속", "나중에", "중단", "지금 점검", "/api/confirm", "/api/folders/later", "/api/folders/stop",
                 "이번 점검: 모델 호출 ", "호출 내역", "로컬 확인", "AI 확인 없이", "영향 대상 선택", "도구 선택", "기타"):
        assert text in app_js, text
    assert "확인 대기" in sources["common.js"] and 'act("home")' in helper_js
    for name in ("app.js", "common.js", "helper.js"):
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", sources[name]), name


def test_batched_relation_calls_count_as_cheap_relation_work():
    from decision_agent.desktop.providers import CHEAP_TASKS, TieredProvider

    assert "RELATION_CLASSIFY_BATCH" in CHEAP_TASKS
    seen = []

    class Named:
        def __init__(self, name):
            self.name = name

        def complete(self, system, user, temperature=0.0, json_schema=None):
            seen.append(self.name)
            return "{}"

    TieredProvider(Named("main"), Named("cheap")).complete("TASK:RELATION_CLASSIFY_BATCH\nx", "u")
    assert seen == ["cheap"]
    assert 'RELATION_CLASSIFY_BATCH: "관계 분류"' in all_sources()["app.js"]


def test_parallel_speed_settings_stay_out_of_the_web_api(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    app, _ = make_external_app(root, confirm_over_calls=0)
    app.start()
    client = client_for(app)
    try:
        assert app.wait_idle(60)
        for hidden in ({"topic_workers": 2}, {"max_parallel_calls": 4}):
            assert client.post("/api/settings", headers=HEADERS, json=hidden).status_code == 400
        assert (app.settings.topic_workers, app.settings.max_parallel_calls) == (4, 8)
    finally:
        app.stop()
