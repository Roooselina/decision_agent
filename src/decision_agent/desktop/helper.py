"""Desktop helper: a small round button that stays on top at the bottom right of the screen.

The tray icon can end up in the hidden overflow area of the Windows 11 taskbar, so this button
is the indicator that is always visible. Clicking it opens a mini panel in the same place;
right-clicking opens a short menu. Its colour follows the engine state through the
controller's change listeners, so nothing polls.

Everything that decides something (positions, colours, heartbeat, quiet hours) is a plain
function here and is tested directly. The pywebview window and the Win32 calls sit behind
small interfaces (webview module, DesktopMetrics) that the tests replace with fakes.
"""

from __future__ import annotations

import datetime
import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)

HELPER_TITLE = "Decision Agent 도우미"
ICON_SIZE = 56
PANEL_SIZE = (320, 440)
MENU_SIZE = (200, 232)
MARGIN = 16
BADGE_SIZE = 20
PANEL_RADIUS = 14
HEARTBEAT_LIMIT = 90.0
PUSH_DELAY = 0.4
SAVE_DELAY = 1.0
TODO_COUNT = 3

# State -> fill colour of the round button. "busy" keeps the normal colour and adds a ring.
COLORS = {
    "normal": "#2563eb",
    "busy": "#2563eb",
    "attention": "#ea580c",
    "paused": "#6b7280",
    "error": "#dc2626",
}
STATE_LABELS = {
    "normal": "정상",
    "busy": "점검 중",
    "attention": "확인 필요",
    "paused": "일시 중지",
    "error": "오류",
}
MENU_ACTIONS = ("open_app", "quick", "rescan", "pause", "hide_helper", "quit")

Rect = tuple[int, int, int, int]  # left, top, right, bottom in logical pixels


# geometry


def default_position(work: Rect, size: int = ICON_SIZE, margin: int = MARGIN) -> tuple[int, int]:
    """Bottom right corner of the work area, just above the taskbar."""
    left, top, right, bottom = work
    return max(left, right - size - margin), max(top, bottom - size - margin)


def fits(x: int, y: int, size: int, area: Rect) -> bool:
    left, top, right, bottom = area
    return left <= x and top <= y and x + size <= right and y + size <= bottom


def restore_position(
    saved_x: int | None,
    saved_y: int | None,
    work: Rect,
    screens: list[Rect] | None = None,
    size: int = ICON_SIZE,
) -> tuple[int, int]:
    """The remembered position when the whole button is on some screen, else the default."""
    if saved_x is not None and saved_y is not None:
        for area in screens or [work]:
            if fits(saved_x, saved_y, size, area):
                return saved_x, saved_y
    return default_position(work, size)


def panel_position(x: int, y: int, width: int, height: int, work: Rect, size: int = ICON_SIZE) -> tuple[int, int]:
    """Where a panel opened from the button at (x, y) goes.

    It grows from the button towards the middle of the screen (left and up from the default
    bottom-right spot), and is pushed back inside the work area when it would leave it.
    """
    left, top, right, bottom = work
    centre_x, centre_y = x + size / 2, y + size / 2
    px = x + size - width if centre_x > (left + right) / 2 else x
    py = y + size - height if centre_y > (top + bottom) / 2 else y
    px = min(max(px, left), max(left, right - width))
    py = min(max(py, top), max(top, bottom - height))
    return int(px), int(py)


# state


def badge_count(status: dict) -> int:
    """Items the user should look at: the same count as the home to-do card."""
    return int(status.get("inconsistent", 0)) + int(status.get("uncertain", 0)) + int(status.get("questions", 0))


def helper_state(status: dict) -> str:
    overall = (status.get("overall") or {}).get("state", "")
    if status.get("paused") or overall == "no_folders":
        return "paused"
    if overall == "failed":
        return "error"
    if overall == "running" or status.get("busy"):
        return "busy"
    if badge_count(status) or overall in {"partial", "retrying", "stale"}:
        return "attention"
    return "normal"


def heartbeat(last_tick: float | None, now: float, limit: float = HEARTBEAT_LIMIT) -> str:
    """"alive" while the program ticked within limit seconds, otherwise "stale"."""
    if last_tick is None or now - last_tick > limit:
        return "stale"
    return "alive"


def _minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def quiet_now(settings: Any, now: datetime.datetime) -> bool:
    """True inside the do-not-disturb hours (the range may cross midnight)."""
    if not getattr(settings, "quiet_hours_enabled", False):
        return False
    start, end = _minutes(settings.quiet_start), _minutes(settings.quiet_end)
    current = now.hour * 60 + now.minute
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def alert_for(previous: int | None, count: int, quiet: bool) -> bool:
    """Shake the button when the count grew since the last update, unless it is quiet time.

    previous is None for the first update after start, which never shakes.
    """
    return previous is not None and count > previous and not quiet


def doing_line(status: dict, pending: list[dict]) -> str:
    """One short sentence about what the program is doing right now."""
    if status.get("paused"):
        return "일시 중지되어 있습니다."
    for folder in status.get("folders", []):
        p = folder.get("progress") or {}
        if p.get("phase") == "checking" and p.get("topics_total"):
            return f"판정 {p['topics_done']}/{p['topics_total']}"
        if p.get("phase") == "extracting" and p.get("extract_total"):
            return f"결정 추출 {p['extract_done']}/{p['extract_total']}"
        if p.get("phase") == "reading" and p.get("files_total"):
            return f"문서 읽는 중 {p['files_done']}/{p['files_total']}"
    if status.get("busy"):
        return "점검 중입니다."
    if pending:
        first = pending[0]
        name = first["name"] + (f" 외 {first['count'] - 1}개" if first["count"] > 1 else "")
        return f"{name} 변경 감지, {max(0, round(first['seconds']))}초 뒤 점검"
    return "변경을 기다리고 있습니다."


def todo_items(findings: list[dict], questions: list[dict], limit: int = TODO_COUNT) -> list[dict]:
    items = [
        {"kind": "question", "title": q.get("text", "")[:60], "sub": q.get("topic_label", "")}
        for q in questions
    ]
    open_findings = [f for f in findings if f.get("label") in {"INCONSISTENT", "UNCERTAIN"}]
    open_findings.sort(key=lambda f: 0 if f["label"] == "INCONSISTENT" else 1)
    for f in open_findings:
        name = str(f.get("source_doc", "")).replace("\\", "/").rsplit("/", 1)[-1]
        items.append(
            {
                "kind": "bad" if f["label"] == "INCONSISTENT" else "warn",
                "title": str(f.get("text", ""))[:60],
                "sub": name,
            }
        )
    return items[:limit]


def snapshot(controller: Any, now: float | None = None) -> dict:
    """Everything the helper page shows, from the controller. No document text beyond titles."""
    now = time.time() if now is None else now
    status = controller.status()
    state = helper_state(status)
    tick = getattr(controller, "last_tick", None)
    pending = controller.pending_changes() if hasattr(controller, "pending_changes") else []
    overall = status.get("overall") or {}
    label = STATE_LABELS[state]
    if overall.get("state") == "no_folders" and state == "paused" and not status.get("paused"):
        label = "폴더 없음"
    elif overall.get("state") == "demo" and state == "normal":
        label = "시연용 모드"
    return {
        "state": state,
        "state_label": label,
        "color": COLORS[state],
        "badge": badge_count(status),
        "paused": bool(status.get("paused")),
        "last_checked": (status.get("watch") or {}).get("last_checked"),
        "heartbeat": heartbeat(tick, now),
        "tick_age": None if tick is None else round(max(0.0, now - tick), 1),
        "doing": doing_line(status, pending),
        "summary": overall.get("label", ""),
        "demo": overall.get("state") == "demo",
        "todos": todo_items(controller.findings(), controller.questions()),
        "quiet": quiet_now(controller.settings, controller.clock()),
        "quick_batch": controller.settings.batch_size > 1,
    }


def region_spec(kind: str, width: int, height: int, badge: bool, scale: float = 1.0) -> list[tuple]:
    """Window shape in physical pixels: the round button (plus its badge) or a rounded card.

    The window is not transparent, so its shape is cut with a window region instead. Each entry
    is ("ellipse", left, top, right, bottom) or ("roundrect", left, top, right, bottom, radius);
    the parts are joined. Right and bottom are exclusive, hence the + 1.
    """
    w, h = round(width * scale), round(height * scale)
    if kind == "circle":
        parts: list[tuple] = [("ellipse", 0, 0, w + 1, h + 1)]
        if badge:
            b = round(BADGE_SIZE * scale)
            parts.append(("ellipse", w - b, 0, w + 1, b + 1))
        return parts
    r = round(PANEL_RADIUS * 2 * scale)
    return [("roundrect", 0, 0, w + 1, h + 1, r)]


# platform


class DesktopMetrics(Protocol):
    def work_area(self) -> Rect | None: ...

    def screens(self) -> list[Rect]: ...

    def hide_from_taskbar(self, window: Any) -> None: ...

    def shape(self, window: Any, kind: str, width: int, height: int, badge: bool) -> None: ...


class Win32Metrics:
    """Work area of the primary monitor and the taskbar style, through user32."""

    SPI_GETWORKAREA = 0x0030
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_APPWINDOW = 0x00040000

    def __init__(self, user32: Any = None) -> None:
        self._user32 = user32

    @property
    def user32(self) -> Any:
        if self._user32 is None:
            import ctypes

            self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return self._user32

    def _scale(self) -> float:
        """Physical to logical pixels. pywebview positions windows in logical pixels."""
        try:
            if self.user32.IsProcessDPIAware():
                return max(1.0, self.user32.GetDpiForSystem() / 96.0)
        except (AttributeError, OSError):
            pass
        return 1.0

    def work_area(self) -> Rect | None:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        try:
            ok = self.user32.SystemParametersInfoW(self.SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
        except (AttributeError, OSError):
            return None
        if not ok:
            return None
        scale = self._scale()
        return (
            int(rect.left / scale), int(rect.top / scale),
            int(rect.right / scale), int(rect.bottom / scale),
        )

    def screens(self) -> list[Rect]:
        """The whole virtual desktop (all monitors) as one rectangle."""
        try:
            x, y = self.user32.GetSystemMetrics(76), self.user32.GetSystemMetrics(77)
            w, h = self.user32.GetSystemMetrics(78), self.user32.GetSystemMetrics(79)
        except (AttributeError, OSError):
            return []
        if w <= 0 or h <= 0:
            return []
        scale = self._scale()
        return [(int(x / scale), int(y / scale), int((x + w) / scale), int((y + h) / scale))]

    @staticmethod
    def _hwnd(window: Any) -> int | None:
        handle = getattr(getattr(window, "native", None), "Handle", None)
        if handle is None:
            return None
        return handle.ToInt64() if hasattr(handle, "ToInt64") else int(handle)

    def shape(self, window: Any, kind: str, width: int, height: int, badge: bool) -> None:
        """Cut the window to the round button or the rounded panel (see region_spec)."""
        import ctypes
        from ctypes import wintypes

        hwnd = self._hwnd(window)
        if hwnd is None:
            return
        gdi32 = ctypes.windll.gdi32  # type: ignore[attr-defined]
        gdi32.CreateEllipticRgn.restype = wintypes.HRGN
        gdi32.CreateEllipticRgn.argtypes = [ctypes.c_int] * 4
        gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
        gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        gdi32.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        self.user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        try:
            scale = self.user32.GetDpiForWindow(hwnd) / 96.0 or 1.0
        except (AttributeError, OSError):
            scale = 1.0
        region = None
        for part in region_spec(kind, width, height, badge, scale):
            if part[0] == "ellipse":
                piece = gdi32.CreateEllipticRgn(*part[1:])
            else:
                piece = gdi32.CreateRoundRectRgn(*part[1:], part[5])
            if region is None:
                region = piece
            else:
                gdi32.CombineRgn(region, region, piece, 2)  # RGN_OR
                gdi32.DeleteObject(piece)
        # The system owns the region once SetWindowRgn succeeds.
        self.user32.SetWindowRgn(hwnd, region, True)

    def hide_from_taskbar(self, window: Any) -> None:
        """A tool window has no taskbar button. Hide and show so the shell notices the change."""
        hwnd = self._hwnd(window)
        if hwnd is None:
            return
        user32 = self.user32
        style = user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
        wanted = (style | self.WS_EX_TOOLWINDOW) & ~self.WS_EX_APPWINDOW
        if wanted != style:
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
            user32.SetWindowLongW(hwnd, self.GWL_EXSTYLE, wanted)
            user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE


class NullMetrics:
    """Fallback off Windows: a 1920x1080 screen with a 48 pixel taskbar."""

    def work_area(self) -> Rect | None:
        return (0, 0, 1920, 1032)

    def screens(self) -> list[Rect]:
        return [(0, 0, 1920, 1080)]

    def hide_from_taskbar(self, window: Any) -> None:
        return None

    def shape(self, window: Any, kind: str, width: int, height: int, badge: bool) -> None:
        return None


def default_metrics() -> DesktopMetrics:
    return Win32Metrics() if sys.platform == "win32" else NullMetrics()


# window


class HelperActions:
    """What the helper's buttons and menu do. Set by the launcher; all optional."""

    def __init__(
        self,
        open_app: Callable[[], None] | None = None,
        open_quick: Callable[[], None] | None = None,
        rescan: Callable[[], None] | None = None,
        toggle_pause: Callable[[], None] | None = None,
        hide_helper: Callable[[], None] | None = None,
        quit_app: Callable[[], None] | None = None,
        open_page: Callable[[str], None] | None = None,
    ) -> None:
        self.open_app = open_app
        self.open_quick = open_quick
        self.rescan = rescan
        self.toggle_pause = toggle_pause
        self.hide_helper = hide_helper
        self.quit_app = quit_app
        self.open_page = open_page


class HelperApi:
    """Methods the helper page calls through window.pywebview.api (in-process, no HTTP)."""

    def __init__(self, helper: HelperWindow) -> None:
        self._helper = helper

    def state(self) -> dict:
        return self._helper.state()

    def expand(self, kind: str = "panel") -> dict:
        return self._helper.expand(kind)

    def collapse(self) -> dict:
        return self._helper.collapse()

    def action(self, name: str) -> bool:
        return self._helper.run_action(name)

    def dragged(self) -> dict:
        return self._helper.drag_end()


class HelperWindow:
    """The always-on-top round button and its panel, as one frameless pywebview window.

    The window is 56x56 while collapsed. Opening the panel or the menu resizes it in place;
    the remembered position always refers to the collapsed button.
    """

    def __init__(
        self,
        webview: Any,
        url: str,
        controller: Any,
        actions: HelperActions,
        metrics: DesktopMetrics | None = None,
        save_position: Callable[[int, int], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.webview = webview
        self.url = url
        self.controller = controller
        self.actions = actions
        self.metrics = metrics or default_metrics()
        self.save_position = save_position
        self.clock = clock
        self.window: Any = None
        self.mode = "icon"
        self.visible = False
        self.x, self.y = self._start_position()
        self._lock = threading.Lock()
        self._push_timer: threading.Timer | None = None
        self._save_timer: threading.Timer | None = None
        self._last_badge: int | None = None
        self._badge_shown = False
        self._loaded = False
        self._quitting = False

    def _work(self) -> Rect:
        return self.metrics.work_area() or NullMetrics().work_area()

    def _start_position(self) -> tuple[int, int]:
        settings = self.controller.settings
        return restore_position(
            getattr(settings, "helper_x", None),
            getattr(settings, "helper_y", None),
            self._work(),
            self.metrics.screens() or None,
        )

    # lifecycle

    def create(self) -> Any:
        self.window = self.webview.create_window(
            HELPER_TITLE,
            self.url,
            width=ICON_SIZE,
            height=ICON_SIZE,
            x=self.x,
            y=self.y,
            min_size=(ICON_SIZE, ICON_SIZE),
            resizable=False,
            frameless=True,
            easy_drag=False,
            on_top=True,
            shadow=False,
            text_select=False,
            background_color="#ffffff",
            js_api=HelperApi(self),
        )
        events = self.window.events
        if hasattr(events, "shown"):
            events.shown += self._shown
        if hasattr(events, "loaded"):
            events.loaded += self._page_loaded
        events.closing += self._closing
        self.visible = True
        self.controller.listeners.append(self.schedule_push)
        return self.window

    def _shown(self, *args) -> None:
        try:
            self.metrics.hide_from_taskbar(self.window)
        except Exception:  # a taskbar button is a cosmetic problem only
            log.warning("could not hide the helper from the taskbar", exc_info=True)
        # Windows widens a window to its minimum caption width while it still has a border;
        # set the real size and spot again now that the window is frameless and shown.
        self.window.resize(ICON_SIZE, ICON_SIZE)
        self.window.move(self.x, self.y)
        self._shape()

    def _shape(self) -> None:
        if self.window is None:
            return
        try:
            if self.mode == "icon":
                self.metrics.shape(self.window, "circle", ICON_SIZE, ICON_SIZE, self._badge_shown)
            else:
                width, height = PANEL_SIZE if self.mode == "panel" else MENU_SIZE
                self.metrics.shape(self.window, "round", width, height, False)
        except Exception:  # a square window is a cosmetic problem only
            log.warning("could not shape the helper window", exc_info=True)

    def _page_loaded(self, *args) -> None:
        if not self._loaded:
            log.info("helper shown at %d,%d", self.x, self.y)
        self._loaded = True
        self.schedule_push()

    def _closing(self, *args) -> bool:
        """The helper is closed only together with the program (quit destroys it)."""
        return self._quitting

    def show(self) -> None:
        if self.window is None:
            self.create()
            return
        if not self.visible:
            self.collapse()
            self.window.show()
            self.visible = True
            self.schedule_push()

    def hide(self) -> None:
        if self.window is not None and self.visible:
            self.window.hide()
        self.visible = False

    def destroy(self) -> None:
        self._quitting = True
        for timer in (self._push_timer, self._save_timer):
            if timer is not None:
                timer.cancel()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                log.debug("helper already closed", exc_info=True)

    # position

    def drag_end(self) -> dict:
        """The page reports that the user finished dragging the button; remember the spot.

        Only this counts as a user move. Moves made by Windows or by opening the panel are not
        saved.
        """
        if self.window is None or self.mode != "icon":
            return {"saved": False}
        x, y = int(self.window.x), int(self.window.y)
        with self._lock:
            if (x, y) == (self.x, self.y):
                return {"saved": False}
            self.x, self.y = x, y
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(SAVE_DELAY, self._save)
            self._save_timer.daemon = True
            self._save_timer.start()
        return {"saved": True, "x": x, "y": y}

    def _save(self) -> None:
        if self.save_position is not None:
            try:
                self.save_position(self.x, self.y)
            except Exception:
                log.warning("could not save the helper position", exc_info=True)

    def expand(self, kind: str = "panel") -> dict:
        width, height = PANEL_SIZE if kind != "menu" else MENU_SIZE
        px, py = panel_position(self.x, self.y, width, height, self._work())
        self.mode = "menu" if kind == "menu" else "panel"
        if self.window is not None:
            self.window.resize(width, height)
            self.window.move(px, py)
            self._shape()
        return {"mode": self.mode, "x": px, "y": py, "width": width, "height": height}

    def collapse(self) -> dict:
        expanded = self.mode != "icon"
        self.mode = "icon"
        if self.window is not None and expanded:
            self.window.resize(ICON_SIZE, ICON_SIZE)
            self.window.move(self.x, self.y)
            self._shape()
        return {"mode": "icon", "x": self.x, "y": self.y}

    # state

    def state(self) -> dict:
        data = snapshot(self.controller, self.clock())
        data["mode"] = self.mode
        data["alert"] = False
        with self._lock:
            previous, self._last_badge = self._last_badge, data["badge"]
        data["alert"] = alert_for(previous, data["badge"], data["quiet"])
        if bool(data["badge"]) != self._badge_shown:
            self._badge_shown = bool(data["badge"])
            self._shape()
        return data

    def schedule_push(self) -> None:
        """Coalesce bursts of engine events (one per file read) into one update."""
        if self.window is None or not self.visible:
            return
        with self._lock:
            if self._push_timer is not None and self._push_timer.is_alive():
                return
            self._push_timer = threading.Timer(PUSH_DELAY, self.push)
            self._push_timer.daemon = True
            self._push_timer.start()

    def push(self) -> None:
        if self.window is None or not self._loaded:
            return
        try:
            payload = json.dumps(self.state(), ensure_ascii=True)
            script = f"window.DAHelper && window.DAHelper.update({payload})"
            runner = getattr(self.window, "run_js", None) or self.window.evaluate_js
            runner(script)
        except Exception:  # the window may be closing
            log.debug("helper update failed", exc_info=True)

    # actions

    def run_action(self, name: str) -> bool:
        actions = self.actions
        table: dict[str, Callable[[], None] | None] = {
            "open_app": actions.open_app,
            "quick": actions.open_quick,
            "rescan": actions.rescan,
            "pause": actions.toggle_pause,
            "hide_helper": actions.hide_helper,
            "quit": actions.quit_app,
            "details": (lambda: actions.open_page("#/results")) if actions.open_page else actions.open_app,
        }
        if name not in table:
            return False
        fn = table[name]
        if name != "quit":
            self.collapse()
        if fn is None:
            return False
        try:
            fn()
        except Exception:
            log.exception("helper action %s failed", name)
            return False
        self.schedule_push()
        return True
