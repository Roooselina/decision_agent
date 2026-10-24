from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

import desktop_helpers as dh
import pytest

from decision_agent.desktop import cli
from decision_agent.desktop import secrets as secret_store
from decision_agent.desktop.autostart import RUN_KEY, VALUE_NAME, Autostart
from decision_agent.desktop.paths import settings_path
from decision_agent.desktop.settings import load_settings
from decision_agent.desktop.tray import MenuSpec, TrayApp, make_icon_image, tray_state, tray_title


def status(**overrides):
    base = {"paused": False, "busy": False, "inconsistent": 0, "questions": 0}
    base.update(overrides)
    return base


def test_tray_state_priority_and_title():
    assert tray_state(status()) == "normal"
    assert tray_state(status(busy=True)) == "busy"
    assert tray_state(status(inconsistent=2, busy=True)) == "attention"
    assert tray_state(status(questions=1)) == "attention"
    assert tray_state(status(paused=True, inconsistent=5)) == "paused"
    assert "반영 안 됨 2건" in tray_title(status(inconsistent=2))


@pytest.mark.parametrize("state", ["normal", "busy", "attention", "paused"])
def test_icon_images_are_drawn_for_every_state(state):
    image = make_icon_image(state, 32)
    assert image.size == (32, 32) and image.getbbox() is not None


class FakeController:
    def __init__(self):
        self.paused = False
        self.rescans = 0
        self.added = []

    def status(self):
        return status(paused=self.paused)

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def rescan(self):
        self.rescans += 1

    def add_folder(self, path):
        self.added.append(path)


def test_tray_menu_actions():
    controller = FakeController()
    opened = []
    tray = TrayApp(
        controller,
        open_ui=lambda: opened.append(1),
        quit_app=lambda: opened.append("quit"),
        autostart=Autostart(registry=FakeRegistry(), command="x"),
        pick_folder=lambda: "/some/folder",
        open_log_folder=lambda: opened.append("logs"),
    )
    menu = {m.label if isinstance(m.label, str) else "status": m for m in tray.menu_spec() if not m.separator}
    assert menu["status"].enabled is False and "반영 안 됨" in menu["status"].label()
    assert menu["대시보드 열기"].default is True
    menu["대시보드 열기"].action()
    menu["지금 다시 점검"].action()
    menu["일시 중지"].action()
    assert controller.paused and menu["일시 중지"].checked()
    menu["일시 중지"].action()
    assert not controller.paused
    menu["감시 폴더 추가..."].action()
    assert controller.added == ["/some/folder"]
    assert menu["Windows 시작 시 자동 실행"].checked() is False
    menu["Windows 시작 시 자동 실행"].action()
    assert menu["Windows 시작 시 자동 실행"].checked() is True
    menu["로그 폴더 열기"].action()
    menu["종료"].action()
    assert opened == [1, "logs", "quit"] and controller.rescans == 1


def test_tray_hides_autostart_item_where_unsupported():
    tray = TrayApp(FakeController(), lambda: None, lambda: None, autostart=Autostart(platform="linux"))
    labels = [m.label for m in tray.menu_spec() if isinstance(m, MenuSpec)]
    assert "Windows 시작 시 자동 실행" not in labels


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}
        self.opened = []

    def OpenKey(self, hive, path, reserved, access):
        self.opened.append((hive, path))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_autostart_writes_only_the_current_user_run_key():
    registry = FakeRegistry()
    auto = Autostart(registry=registry, command='"C:\\App\\decision-agent.exe" run --background')
    assert auto.supported and not auto.is_enabled()
    auto.enable()
    assert registry.values[VALUE_NAME].endswith("run --background")
    assert auto.is_enabled()
    assert {opened for opened in registry.opened} == {("HKCU", RUN_KEY)}
    assert auto.toggle() is False and not auto.is_enabled()
    auto.disable()
    assert auto.toggle() is True


def test_autostart_unsupported_platform_is_safe():
    auto = Autostart(platform="linux")
    assert not auto.supported and not auto.is_enabled()
    auto.disable()
    with pytest.raises(RuntimeError):
        auto.enable()


def test_autostart_command_line_for_dev_and_frozen(monkeypatch):
    dev = Autostart(platform="win32").command_line()
    assert "-m decision_agent.desktop run --background" in dev
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    frozen = Autostart(platform="win32").command_line()
    assert frozen.endswith("run --background") and "-m" not in frozen


def test_cli_folder_management(tmp_path, capsys):
    root = dh.make_watch_dir(tmp_path)
    assert cli.main(["folders", "list"]) == 0
    assert cli.main(["folders", "add", str(root)]) == 0
    assert str(root.resolve()) in load_settings().allowed_folders
    assert cli.main(["folders", "add", "/"]) == 1
    assert cli.main(["folders", "add", str(root / "meetings")]) == 1
    assert cli.main(["folders", "remove", str(tmp_path / "unknown")]) == 1
    assert cli.main(["folders", "remove", str(root)]) == 0
    assert load_settings().allowed_folders == []
    assert settings_path().exists()


def test_cli_scan_reports_and_sets_exit_code(tmp_path, capsys):
    root = dh.make_watch_dir(tmp_path)
    assert cli.main(["scan", "--folder", str(root)]) == 0
    out = capsys.readouterr().out
    assert "INCONSISTENT" in out and "확인 질문" in out and "mock" in out
    assert cli.main(["scan", "--folder", str(root), "--fail-on-inconsistent"]) == 2
    assert cli.main(["scan"]) == 1


def test_cli_scan_leaves_the_folder_untouched(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    cli.main(["scan", "--folder", str(root)])
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before


def test_cli_key_commands_use_the_secret_store_only(monkeypatch, capsys):
    stored = {}
    monkeypatch.setattr(secret_store, "set_api_key", lambda v: stored.__setitem__("k", v))
    monkeypatch.setattr(secret_store, "delete_api_key", lambda: stored.pop("k", None))
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "sk-test-abcdefgh")
    assert cli.main(["key", "set"]) == 0 and stored["k"] == "sk-test-abcdefgh"
    assert "sk-test" not in capsys.readouterr().out
    assert "sk-test" not in settings_path().read_text(encoding="utf-8") if settings_path().exists() else True
    assert cli.main(["key", "clear"]) == 0 and "k" not in stored
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "short")
    assert cli.main(["key", "set"]) == 1


def test_cli_key_set_reports_missing_store(monkeypatch, capsys):
    def unavailable(value):
        raise secret_store.SecretStoreUnavailable("no store")

    monkeypatch.setattr(secret_store, "set_api_key", unavailable)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "sk-test-abcdefgh")
    assert cli.main(["key", "set"]) == 1
    assert "no store" in capsys.readouterr().err


def test_cli_autostart_is_windows_only(capsys):
    if sys.platform != "win32":
        assert cli.main(["autostart", "status"]) == 1


def test_settings_file_never_contains_secrets(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    cli.main(["folders", "add", str(root)])
    data = json.loads(settings_path().read_text(encoding="utf-8"))
    assert not any("key" in name.lower() and "api" in name.lower() for name in data)
    assert Path(data["allowed_folders"][0]).is_absolute()


def test_tray_has_quick_check_and_digest_entries():
    class Controller(FakeController):
        def digest_now(self):
            return {"short": "불일치 1건", "markdown": "# x", "title": "t"}

    opened = []
    toasts = []
    tray = TrayApp(
        Controller(),
        open_ui=lambda: opened.append("ui"),
        quit_app=lambda: None,
        open_quick=lambda: opened.append("quick"),
        open_digest=lambda: opened.append("digest"),
    )
    tray.notify = lambda title, message: toasts.append((title, message))
    menu = {m.label: m for m in tray.menu_spec() if not m.separator and isinstance(m.label, str)}
    menu["빠른 점검 (회의 중 메모)"].action()
    menu["오늘 요약 만들기"].action()
    assert opened == ["quick", "digest"] and toasts == [("오늘 요약", "불일치 1건")]


def test_tray_quick_entry_falls_back_to_the_dashboard():
    opened = []
    tray = TrayApp(FakeController(), open_ui=lambda: opened.append("ui"), quit_app=lambda: None)
    menu = {m.label: m for m in tray.menu_spec() if not m.separator and isinstance(m.label, str)}
    menu["빠른 점검 (회의 중 메모)"].action()
    assert opened == ["ui"] and "오늘 요약 만들기" not in menu


def test_ui_url_carries_the_target_page():
    assert cli.ui_url(8765, "tok") == "http://127.0.0.1:8765/?t=tok"
    assert cli.ui_url(8765, "tok", "/quick") == "http://127.0.0.1:8765/?t=tok&next=/quick"


def test_real_pystray_accepts_the_menu(monkeypatch):
    """pystray rejects callbacks with more than two parameters, which a fake menu would hide."""
    monkeypatch.setenv("PYSTRAY_BACKEND", "dummy")
    pystray = pytest.importorskip("pystray")
    tray = TrayApp(FakeController(), lambda: None, lambda: None, open_quick=lambda: None)
    menu = tray._build_menu()
    assert isinstance(menu, pystray.Menu) and len(menu.items) >= 6
