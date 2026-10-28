"""Desktop helper button: position, colours, heartbeat, quiet hours, and remembered position."""

from __future__ import annotations

import datetime
import json
import re

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient

from decision_agent.desktop import helper as helper_module
from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.helper import (
    COLORS,
    ICON_SIZE,
    MENU_SIZE,
    PANEL_SIZE,
    HelperActions,
    HelperWindow,
    alert_for,
    badge_count,
    default_position,
    doing_line,
    heartbeat,
    helper_state,
    panel_position,
    quiet_now,
    region_spec,
    restore_position,
    todo_items,
)
from decision_agent.desktop.notify import Notifier
from decision_agent.desktop.providers import build_provider
from decision_agent.desktop.settings import AppSettings, load_settings
from decision_agent.desktop.state import StateDB
from decision_agent.desktop.watcher import Debouncer
from decision_agent.desktop.webapp import create_web_app

WORK = (0, 0, 1920, 1032)  # 1920x1080 screen, 48 pixel taskbar at the bottom
SCREEN = (0, 0, 1920, 1080)
TOKEN = "w" * 32
PORT = 8765


# geometry


def test_default_position_is_bottom_right_above_the_taskbar():
    assert default_position(WORK) == (1920 - 56 - 16, 1032 - 56 - 16)
    # Taskbar on the left: the work area starts at x=60.
    assert default_position((60, 0, 1920, 1080)) == (1848, 1008)


def test_saved_position_is_kept_only_when_fully_on_a_screen():
    assert restore_position(100, 200, WORK, [SCREEN]) == (100, 200)
    second_monitor = (1920, 0, 3840, 1080)
    assert restore_position(2500, 500, WORK, [SCREEN, second_monitor]) == (2500, 500)
    assert restore_position(5000, 500, WORK, [SCREEN]) == default_position(WORK)
    assert restore_position(1900, 500, WORK, [SCREEN]) == default_position(WORK)
    assert restore_position(-30, 10, WORK, [SCREEN]) == default_position(WORK)
    assert restore_position(None, 10, WORK, [SCREEN]) == default_position(WORK)


def test_panel_opens_towards_the_screen_and_stays_inside():
    x, y = default_position(WORK)
    px, py = panel_position(x, y, *PANEL_SIZE, WORK)
    assert px + PANEL_SIZE[0] == x + ICON_SIZE and py + PANEL_SIZE[1] == y + ICON_SIZE
    assert panel_position(20, 20, *PANEL_SIZE, WORK) == (20, 20)
    # A button dragged to the very bottom edge: the panel is pushed up into the work area.
    px, py = panel_position(900, 1032 - 56, *PANEL_SIZE, WORK)
    assert py + PANEL_SIZE[1] <= 1032 and px >= 0
    px, py = panel_position(1890, 500, *MENU_SIZE, WORK)
    assert px + MENU_SIZE[0] <= 1920


# state


def status(**overrides):
    base = {"paused": False, "busy": False, "inconsistent": 0, "uncertain": 0, "questions": 0,
            "overall": {"state": "verified", "label": "검사 완료"}, "folders": [], "watch": {}}
    base.update(overrides)
    return base


def test_state_to_colour():
    assert helper_state(status()) == "normal"
    assert helper_state(status(overall={"state": "demo"})) == "normal"
    assert helper_state(status(busy=True, overall={"state": "running"})) == "busy"
    assert helper_state(status(inconsistent=2, questions=1)) == "attention"
    assert helper_state(status(overall={"state": "partial"})) == "attention"
    assert helper_state(status(paused=True, inconsistent=3)) == "paused"
    assert helper_state(status(overall={"state": "failed"})) == "error"
    assert helper_state(status(overall={"state": "no_folders"})) == "paused"
    assert badge_count(status(inconsistent=2, uncertain=1, questions=1)) == 4
    assert COLORS["normal"] == COLORS["busy"] == "#2563eb"
    assert {COLORS["attention"], COLORS["paused"], COLORS["error"]} == {"#ea580c", "#6b7280", "#dc2626"}


def test_heartbeat_turns_stale_after_ninety_seconds():
    assert heartbeat(None, 1000.0) == "stale"
    assert heartbeat(1000.0, 1089.0) == "alive"
    assert heartbeat(1000.0, 1090.0) == "alive"
    assert heartbeat(1000.0, 1091.0) == "stale"


def test_quiet_hours_cross_midnight():
    settings = AppSettings(quiet_hours_enabled=True, quiet_start="22:00", quiet_end="08:00")
    at = lambda h, m=0: datetime.datetime(2026, 10, 24, h, m)
    assert quiet_now(settings, at(23)) and quiet_now(settings, at(7, 59))
    assert not quiet_now(settings, at(8)) and not quiet_now(settings, at(12))
    day = AppSettings(quiet_hours_enabled=True, quiet_start="12:00", quiet_end="13:00")
    assert quiet_now(day, at(12, 30)) and not quiet_now(day, at(13))
    assert not quiet_now(AppSettings(quiet_start="00:00", quiet_end="23:59"), at(12))


def test_new_issue_shakes_unless_quiet():
    assert alert_for(None, 3, quiet=False) is False
    assert alert_for(1, 3, quiet=False) is True
    assert alert_for(1, 3, quiet=True) is False
    assert alert_for(3, 2, quiet=False) is False


def test_doing_line_and_todos():
    pending = [{"name": "회의록.md", "count": 1, "seconds": 2.6}]
    assert doing_line(status(), pending) == "회의록.md 변경 감지, 3초 뒤 점검"
    assert doing_line(status(), [{"name": "a.md", "count": 3, "seconds": 9}]) == "a.md 외 2개 변경 감지, 9초 뒤 점검"
    running = status(busy=True, folders=[{"progress": {"phase": "extracting", "extract_done": 4, "extract_total": 9}}])
    assert doing_line(running, []) == "결정 추출 4/9"
    assert doing_line(status(paused=True), pending) == "일시 중지되어 있습니다."
    assert doing_line(status(), []) == "변경을 기다리고 있습니다."
    findings = [{"label": "UNCERTAIN", "text": "b", "source_doc": "x/b.md"},
                {"label": "INCONSISTENT", "text": "a", "source_doc": "x\\a.md"},
                {"label": "CONSISTENT", "text": "c", "source_doc": "c.md"}]
    items = todo_items(findings, [{"text": "q", "topic_label": "t"}, {"text": "q2"}, {"text": "q3"}])
    assert [i["kind"] for i in items] == ["question", "question", "question"]
    assert [i["title"] for i in todo_items(findings, [])] == ["a", "b"]
    assert todo_items(findings, [])[0]["sub"] == "a.md"


def test_debouncer_reports_what_is_waiting(tmp_path):
    now = [100.0]
    debouncer = Debouncer(10.0, lambda paths: None, max_wait=60.0, clock=lambda: now[0])
    assert debouncer.pending_info() is None
    debouncer.add(tmp_path / "a.md")
    now[0] = 103.0
    debouncer.add(tmp_path / "회의록.md")
    now[0] = 106.0
    info = debouncer.pending_info()
    assert info["count"] == 2 and info["path"].name == "회의록.md" and info["seconds"] == pytest.approx(7.0)


# window (pywebview replaced by a fake)


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        return [h(*args) for h in self.handlers]


class FakeWindow:
    def __init__(self, title, url, **kwargs):
        self.title, self.url, self.kwargs = title, url, kwargs
        self.events = type("Events", (), {})()
        for name in ("closing", "moved", "shown", "loaded"):
            setattr(self.events, name, FakeEvent())
        self.calls = []
        self.x, self.y = kwargs.get("x"), kwargs.get("y")

    def resize(self, w, h):
        self.calls.append(("resize", w, h))

    def move(self, x, y):
        self.calls.append(("move", x, y))
        self.x, self.y = x, y

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def destroy(self):
        self.calls.append("destroy")

    def run_js(self, code):
        self.calls.append(("js", code))


class FakeWebview:
    def __init__(self):
        self.windows = []

    def create_window(self, title, url, **kwargs):
        window = FakeWindow(title, url, **kwargs)
        self.windows.append(window)
        return window


class FakeMetrics:
    def __init__(self, work=WORK, screens=(SCREEN,)):
        self.work, self._screens, self.hidden, self.shapes = work, list(screens), [], []

    def work_area(self):
        return self.work

    def screens(self):
        return self._screens

    def hide_from_taskbar(self, window):
        self.hidden.append(window)

    def shape(self, window, kind, width, height, badge):
        self.shapes.append((kind, width, height, badge))


class FakeController:
    def __init__(self, settings=None, now=datetime.datetime(2026, 10, 24, 12, 0)):
        self.settings = settings or AppSettings()
        self.listeners = []
        self.last_tick = 1000.0
        self.now = now
        self.counts = {"inconsistent": 0, "questions": 0}

    def clock(self):
        return self.now

    def status(self):
        return status(**self.counts, watch={"last_checked": "2026-10-24T11:58:00"})

    def findings(self):
        return []

    def questions(self):
        return []

    def pending_changes(self):
        return []


def make_helper(controller=None, metrics=None, **actions):
    saved = []
    helper = HelperWindow(FakeWebview(), "http://127.0.0.1:1/helper", controller or FakeController(),
                          HelperActions(**actions), metrics=metrics or FakeMetrics(),
                          save_position=lambda x, y: saved.append((x, y)), clock=lambda: 1010.0)
    return helper, saved


def test_helper_window_is_a_small_frameless_top_window():
    helper, _ = make_helper()
    window = helper.create()
    kw = window.kwargs
    assert (kw["width"], kw["height"]) == (56, 56) and (kw["x"], kw["y"]) == default_position(WORK)
    assert kw["frameless"] and kw["on_top"] and kw["resizable"] is False
    assert kw["js_api"] is not None and window.url.endswith("/helper")
    window.events.shown.fire()
    # Shown: no taskbar button, the real size again, and the round shape.
    assert helper.metrics.hidden == [window]
    assert window.calls[:2] == [("resize", 56, 56), ("move", *default_position(WORK))]
    assert helper.metrics.shapes[-1] == ("circle", 56, 56, False)
    assert window.events.closing.fire() == [False]
    helper.destroy()
    assert window.events.closing.fire() == [True] and "destroy" in window.calls


def test_dragging_saves_the_position_but_other_moves_do_not(monkeypatch):
    monkeypatch.setattr(helper_module, "SAVE_DELAY", 0.0)
    helper, saved = make_helper()
    window = helper.create()
    # Windows moving the window by itself (for example when the border is removed) is ignored.
    window.events.moved.fire(17, 5)
    window.x, window.y = 300, 400
    assert helper.drag_end() == {"saved": True, "x": 300, "y": 400}
    helper._save_timer.join(2)
    assert saved == [(300, 400)] and (helper.x, helper.y) == (300, 400)
    assert helper.drag_end() == {"saved": False}
    helper.expand("panel")
    assert helper.drag_end() == {"saved": False} and (helper.x, helper.y) == (300, 400)
    helper.collapse()
    assert window.calls[-2:] == [("resize", 56, 56), ("move", 300, 400)]


def test_window_region_follows_mode_and_badge():
    assert region_spec("circle", 56, 56, False) == [("ellipse", 0, 0, 57, 57)]
    assert region_spec("circle", 56, 56, True, 2.0) == [("ellipse", 0, 0, 113, 113), ("ellipse", 72, 0, 113, 41)]
    assert region_spec("round", 320, 440, False, 1.5) == [("roundrect", 0, 0, 481, 661, 42)]
    controller = FakeController()
    helper, _ = make_helper(controller)
    helper.create()
    helper.expand("panel")
    assert helper.metrics.shapes[-1] == ("round", *PANEL_SIZE, False)
    helper.collapse()
    controller.counts = {"inconsistent": 1, "questions": 0}
    helper.state()
    assert helper.metrics.shapes[-1] == ("circle", 56, 56, True)


def test_expand_and_collapse_resize_in_place():
    helper, _ = make_helper()
    window = helper.create()
    result = helper.expand("panel")
    x, y = default_position(WORK)
    assert ("resize", *PANEL_SIZE) in window.calls
    assert (result["x"] + PANEL_SIZE[0], result["y"] + PANEL_SIZE[1]) == (x + 56, y + 56)
    assert helper.expand("menu")["width"] == MENU_SIZE[0]
    assert helper.collapse()["mode"] == "icon" and helper.mode == "icon"


def test_actions_run_and_collapse_the_panel():
    done = []
    helper, _ = make_helper(rescan=lambda: done.append("rescan"), quit_app=lambda: done.append("quit"),
                            open_page=lambda page: done.append(page))
    helper.create()
    helper.expand("menu")
    assert helper.run_action("rescan") is True and helper.mode == "icon"
    assert helper.run_action("details") is True and done[-1] == "#/results"
    assert helper.run_action("quit") is True and done[-1] == "quit"
    assert helper.run_action("pause") is False
    assert helper.run_action("unknown") is False


def test_new_issue_shakes_only_outside_quiet_hours():
    controller = FakeController()
    helper, _ = make_helper(controller)
    helper.create()
    first = helper.state()
    assert first["alert"] is False and first["state"] == "normal" and first["heartbeat"] == "alive"
    controller.counts = {"inconsistent": 2, "questions": 0}
    grown = helper.state()
    assert grown["alert"] is True and grown["badge"] == 2 and grown["color"] == COLORS["attention"]
    controller.settings = AppSettings(quiet_hours_enabled=True, quiet_start="09:00", quiet_end="18:00")
    controller.counts = {"inconsistent": 3, "questions": 0}
    quiet = helper.state()
    assert quiet["alert"] is False and quiet["badge"] == 3 and quiet["quiet"] is True


def test_state_label_is_honest_about_demo_and_missing_folders():
    controller = FakeController()
    controller.status = lambda: status(overall={"state": "demo", "label": "데모 모드입니다. 실제 검사가 아닙니다."})
    helper, _ = make_helper(controller)
    data = helper.state()
    assert data["state_label"] == "데모 모드" and data["demo"] is True
    controller.status = lambda: status(overall={"state": "no_folders", "label": ""})
    assert helper.state()["state_label"] == "폴더 없음"
    controller.status = lambda: status(paused=True)
    assert helper.state()["state_label"] == "일시 중지"


def test_push_sends_json_only_after_the_page_loaded():
    helper, _ = make_helper()
    window = helper.create()
    helper.push()
    assert not [c for c in window.calls if c[0] == "js"]
    window.events.loaded.fire()
    helper._push_timer.join(2)
    scripts = [c[1] for c in window.calls if c[0] == "js"]
    assert scripts and scripts[-1].startswith("window.DAHelper && window.DAHelper.update({")
    payload = json.loads(scripts[-1].split("update(", 1)[1][:-1])
    assert payload["state_label"] == "정상"


def test_hidden_helper_does_not_push_and_shows_again():
    helper, _ = make_helper()
    window = helper.create()
    helper.hide()
    helper.schedule_push()
    assert helper._push_timer is None and "hide" in window.calls
    helper.show()
    assert "show" in window.calls and helper.visible


# settings and controller


def test_position_is_saved_to_settings_and_restored(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    settings_file = tmp_path / "settings.json"
    controller = DecisionAgentApp(dh.make_settings(root), settings_file=settings_file,
                                  db_factory=lambda r: StateDB(":memory:"), start_watchers=False)
    controller.update_settings_light({"helper_x": 640, "helper_y": 480}, announce=False)
    loaded = load_settings(settings_file)
    assert (loaded.helper_x, loaded.helper_y) == (640, 480)
    again = FakeController(settings=loaded)
    helper, _ = make_helper(again)
    assert (helper.x, helper.y) == (640, 480)
    off_screen = FakeController(settings=AppSettings(helper_x=9000, helper_y=480))
    helper, _ = make_helper(off_screen)
    assert (helper.x, helper.y) == default_position(WORK)
    with pytest.raises(ValueError):
        controller.update_settings_light({"provider": "anthropic"})


class RecordingNotifier(Notifier):
    def __init__(self):
        self.sent = []

    def notify(self, title, message):
        self.sent.append((title, message))


def run_controller(root, clock, **overrides):
    notifier = RecordingNotifier()
    controller = DecisionAgentApp(dh.make_settings(root, **overrides), notifier=notifier,
                                  bundle_factory=build_provider, db_factory=lambda r: StateDB(":memory:"),
                                  start_watchers=False, clock=lambda: clock)
    controller.start()
    assert controller.wait_idle(60)
    controller.stop()
    return controller, notifier


def test_quiet_hours_skip_toasts_but_keep_the_inbox(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    quiet = {"quiet_hours_enabled": True, "quiet_start": "22:00", "quiet_end": "08:00"}
    controller, notifier = run_controller(root, datetime.datetime(2026, 10, 24, 23, 0), **quiet)
    assert notifier.sent == [] and controller.inbox.recent()
    loud, notifier = run_controller(dh.make_watch_dir(tmp_path, "w2"), datetime.datetime(2026, 10, 24, 12, 0), **quiet)
    assert notifier.sent
    assert loud.last_tick is not None


def test_helper_page_and_light_settings(tmp_path, monkeypatch):
    root = dh.make_watch_dir(tmp_path)
    controller = DecisionAgentApp(dh.make_settings(root), db_factory=lambda r: StateDB(":memory:"),
                                  start_watchers=False)
    controller.start()
    assert controller.wait_idle(60)
    client = TestClient(create_web_app(controller, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}")
    try:
        assert client.get("/helper").status_code == 403
        client.get(f"/?t={TOKEN}&next=/helper")
        page = client.get("/helper")
        nonce = page.headers["content-security-policy"].split("'nonce-")[1].split("'")[0]
        assert all(f'nonce="{nonce}"' in s for s in re.findall(r"<script([^>]*)>", page.text))
        assert client.get("/static/helper.js").status_code == 200
        assert client.get("/static/helper.css").headers["content-type"].startswith("text/css")
        calls = []
        monkeypatch.setattr(controller, "reconfigure", lambda new: calls.append(new))
        headers = {"X-DA-Token": TOKEN}
        assert client.post("/api/settings", headers=headers, json={"helper_enabled": False}).status_code == 200
        assert controller.settings.helper_enabled is False and calls == []
        assert client.post("/api/settings", headers=headers, json={"helper_x": 5}).status_code == 400
        client.post("/api/settings", headers=headers, json={"notify": False})
        assert len(calls) == 1
    finally:
        controller.stop()
