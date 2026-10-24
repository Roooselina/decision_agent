from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.autostart import Autostart

log = logging.getLogger(__name__)

COLORS = {
    "normal": (29, 78, 216),
    "busy": (13, 148, 136),
    "attention": (217, 119, 6),
    "paused": (107, 114, 128),
}


@dataclass
class MenuSpec:
    """Toolkit-independent menu entry, converted to pystray items at run time."""

    label: str | Callable[[], str]
    action: Callable[[], None] | None = None
    checked: Callable[[], bool] | None = None
    default: bool = False
    enabled: bool = True
    separator: bool = False


def tray_state(status: dict) -> str:
    if status["paused"]:
        return "paused"
    if status["inconsistent"] or status["questions"]:
        return "attention"
    if status["busy"]:
        return "busy"
    return "normal"


def tray_title(status: dict) -> str:
    return f"결정 변경 점검 - 반영 안 됨 {status['inconsistent']}건, 질문 {status['questions']}건"


def make_icon_image(state: str, size: int = 64):
    from PIL import Image, ImageDraw

    color = COLORS.get(state, COLORS["normal"])
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size - 3, size - 3), radius=size // 5, fill=color + (255,))
    white = (255, 255, 255, 255)
    unit = size / 64.0
    if state == "attention":
        draw.rounded_rectangle(
            (28 * unit, 14 * unit, 36 * unit, 38 * unit), radius=3 * unit, fill=white
        )
        draw.ellipse((27 * unit, 43 * unit, 37 * unit, 53 * unit), fill=white)
    elif state == "paused":
        draw.rounded_rectangle((20 * unit, 16 * unit, 28 * unit, 48 * unit), radius=2 * unit, fill=white)
        draw.rounded_rectangle((36 * unit, 16 * unit, 44 * unit, 48 * unit), radius=2 * unit, fill=white)
    else:
        draw.line(
            [(16 * unit, 34 * unit), (28 * unit, 46 * unit), (48 * unit, 20 * unit)],
            fill=white,
            width=max(2, int(7 * unit)),
            joint="curve",
        )
    return image


class TrayApp:
    """System tray icon with a small menu. The dashboard itself is the local web page."""

    def __init__(
        self,
        controller: DecisionAgentApp,
        open_ui: Callable[[], None],
        quit_app: Callable[[], None],
        autostart: Autostart | None = None,
        pick_folder: Callable[[], str | None] | None = None,
        open_log_folder: Callable[[], None] | None = None,
        open_quick: Callable[[], None] | None = None,
        open_digest: Callable[[], None] | None = None,
    ) -> None:
        self.controller = controller
        self.open_ui = open_ui
        self.quit_app = quit_app
        self.autostart = autostart
        self.pick_folder = pick_folder
        self.open_log_folder = open_log_folder
        self.open_quick = open_quick
        self.open_digest = open_digest
        self.icon = None
        self._last_state = ""

    def menu_spec(self) -> list[MenuSpec]:
        controller = self.controller

        def status_line() -> str:
            return tray_title(controller.status())

        def toggle_pause() -> None:
            if controller.paused:
                controller.resume()
            else:
                controller.pause()

        def add_folder() -> None:
            if self.pick_folder is None:
                self.open_ui()
                return
            chosen = self.pick_folder()
            if chosen:
                try:
                    controller.add_folder(chosen)
                except ValueError as exc:
                    self.notify("폴더를 추가하지 못했습니다", str(exc))

        def make_digest() -> None:
            info = controller.digest_now()
            self.notify("오늘 요약", info.get("short") or "요약 파일을 만들었습니다.")
            if self.open_digest is not None:
                self.open_digest()

        spec: list[MenuSpec] = [
            MenuSpec(status_line, None, enabled=False),
            MenuSpec("", separator=True),
            MenuSpec("대시보드 열기", self.open_ui, default=True),
            MenuSpec("빠른 점검 (회의 중 메모)", self.open_quick or self.open_ui),
            MenuSpec("지금 다시 점검", controller.rescan),
            MenuSpec("일시 중지", toggle_pause, checked=lambda: controller.paused),
            MenuSpec("감시 폴더 추가...", add_folder),
        ]
        if self.autostart is not None and self.autostart.supported:
            autostart = self.autostart
            spec.append(
                MenuSpec(
                    "Windows 시작 시 자동 실행",
                    lambda: autostart.toggle(),
                    checked=autostart.is_enabled,
                )
            )
        if self.open_digest is not None:
            spec.append(MenuSpec("오늘 요약 만들기", make_digest))
        if self.open_log_folder is not None:
            spec.append(MenuSpec("로그 폴더 열기", self.open_log_folder))
        spec.append(MenuSpec("", separator=True))
        spec.append(MenuSpec("종료", self.quit_app))
        return spec

    @staticmethod
    def _make_action(fn):
        """pystray accepts only callables with at most two parameters (icon, item)."""

        def wrapped(icon, item):
            if fn is not None:
                try:
                    fn()
                except Exception:
                    log.exception("tray action failed")

        return wrapped

    def _build_menu(self):
        import pystray

        items = []
        for entry in self.menu_spec():
            if entry.separator:
                items.append(pystray.Menu.SEPARATOR)
                continue
            label = entry.label
            if callable(label):
                text = (lambda item, fn=label: str(fn()))
            else:
                text = label
            checked = (lambda item, fn=entry.checked: bool(fn())) if entry.checked else None
            items.append(
                pystray.MenuItem(
                    text,
                    self._make_action(entry.action),
                    checked=checked,
                    default=entry.default,
                    enabled=entry.enabled,
                )
            )
        return pystray.Menu(*items)

    def notify(self, title: str, message: str) -> None:
        if self.icon is not None:
            try:
                self.icon.notify(message, title)
            except Exception:
                log.warning("tray notify unsupported", exc_info=True)

    def refresh(self) -> None:
        if self.icon is None:
            return
        status = self.controller.status()
        state = tray_state(status)
        try:
            if state != self._last_state:
                self.icon.icon = make_icon_image(state)
                self._last_state = state
            self.icon.title = tray_title(status)
            self.icon.update_menu()
        except Exception:
            log.debug("tray refresh failed", exc_info=True)

    def run(self) -> None:
        """Show the icon and block until quit. Raises if no tray backend is available."""
        import pystray

        status = self.controller.status()
        state = tray_state(status)
        self._last_state = state
        self.icon = pystray.Icon(
            "decision-agent", make_icon_image(state), tray_title(status), self._build_menu()
        )
        self.controller.listeners.append(self.refresh)
        self.icon.run()

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                log.debug("tray stop failed", exc_info=True)


def pick_folder_dialog() -> str | None:
    """Native folder chooser through tkinter; returns None when unavailable or cancelled."""
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title="감시할 폴더 선택")
        root.destroy()
        return chosen or None
    except Exception:  # noqa: BLE001 - no display or Tk failure
        return None
