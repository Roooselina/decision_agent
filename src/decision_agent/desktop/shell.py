from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from decision_agent.desktop.uistate import MIN_HEIGHT, MIN_WIDTH, load_ui_state, save_ui_state

log = logging.getLogger(__name__)

APP_TITLE = "Decision Agent - 결정 변경 점검"
QUICK_TITLE = "빠른 점검 - Decision Agent"
QUICK_SIZE = (480, 640)
MUTEX_NAME = "DecisionAgentSingleInstance"
WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_DOWNLOAD = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def static_dir() -> Path:
    """Folder with the bundled UI files (also inside the PyInstaller bundle)."""
    return Path(__file__).resolve().parent / "static"


def icon_path() -> Path | None:
    candidate = static_dir() / "app.ico"
    return candidate if candidate.exists() else None


def webview2_version(registry: Any = None, platform: str = sys.platform) -> str | None:
    """Installed WebView2 runtime version, or None. Reads the documented EdgeUpdate keys."""
    if platform != "win32" and registry is None:
        return None
    if registry is None:
        import winreg as registry
    candidates = [
        (registry.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
        (registry.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
        (registry.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
    ]
    for hive, key_path in candidates:
        try:
            with registry.OpenKey(hive, key_path, 0, registry.KEY_READ) as key:
                value, _ = registry.QueryValueEx(key, "pv")
        except OSError:
            continue
        if value and value != "0.0.0.0":
            return str(value)
    return None


def choose_ui_mode(
    platform: str = sys.platform,
    import_webview: Callable[[], Any] | None = None,
    runtime_version: Callable[[], str | None] = webview2_version,
) -> tuple[str, str]:
    """Pick "window" (native window) or "browser" (default browser fallback), with the reason."""

    def default_import():
        import webview

        return webview

    try:
        (import_webview or default_import)()
    except Exception as exc:  # noqa: BLE001 - any import failure means no native window
        return "browser", f"창 구성 요소를 불러오지 못했습니다: {exc.__class__.__name__}"
    if platform == "win32" and not runtime_version():
        return "browser", "WebView2 런타임이 없어 기본 브라우저로 엽니다."
    return "window", ""


class SingleInstance:
    """A named mutex so only one copy runs per user session (the installer checks it too)."""

    def __init__(self, name: str = MUTEX_NAME, platform: str = sys.platform, kernel32: Any = None) -> None:
        self.name = name
        self.platform = platform
        self._kernel32 = kernel32
        self._handle = None

    def acquire(self) -> bool:
        """True when this is the first instance. Always True where mutexes are unavailable."""
        if self.platform != "win32" and self._kernel32 is None:
            return True
        kernel32 = self._kernel32
        if kernel32 is None:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._handle = kernel32.CreateMutexW(None, False, self.name)
        already = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        return bool(self._handle) and not already

    def release(self) -> None:
        if self._handle and self.platform == "win32" and self._kernel32 is None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)  # type: ignore[attr-defined]
        self._handle = None


def _folder_dialog_kind(webview: Any) -> Any:
    kinds = getattr(webview, "FileDialog", None)
    if kinds is not None and hasattr(kinds, "FOLDER"):
        return kinds.FOLDER
    return getattr(webview, "FOLDER_DIALOG", 20)


def fit_to_screen(state: dict, screen: tuple[int, int] | None) -> tuple[int, int, int | None, int | None]:
    """Window size and position that fit the screen (logical pixels).

    The remembered size is kept when it fits; otherwise it shrinks to the screen, leaving room for
    the taskbar. A remembered position that would put the window off screen is dropped, and the
    window is centred instead.
    """
    width, height, x, y = state["width"], state["height"], state["x"], state["y"]
    if screen is None:
        return width, height, x, y
    screen_w, screen_h = screen
    width = min(width, max(screen_w - 40, 640))
    height = min(height, max(screen_h - 80, 480))
    if x is None or y is None or x < -20 or y < -20 or x + width > screen_w + 20 or y + height > screen_h - 20:
        x = max((screen_w - width) // 2, 0)
        y = max((screen_h - 40 - height) // 2, 0)
    return width, height, x, y


class DesktopShell:
    """Native windows around the local web UI, through pywebview (WebView2 on Windows).

    Closing the main window hides it and the program keeps watching from the tray. Only quit()
    (the tray menu "종료") really closes the windows. The quick-check window is small, fixed in
    size, and stays on top.
    """

    def __init__(
        self,
        webview: Any,
        url: str,
        quick_url: str,
        storage_path: Path,
        on_first_hide: Callable[[], None] | None = None,
        on_drop: Callable[[str], None] | None = None,
        background: bool = False,
        state_path: Path | None = None,
    ) -> None:
        self.webview = webview
        self.url = url
        self.quick_url = quick_url
        self.storage_path = storage_path
        self.on_first_hide = on_first_hide
        self.on_drop = on_drop
        self.background = background
        self.state_path = state_path
        self.state = load_ui_state(state_path)
        self.main: Any = None
        self.quick: Any = None
        self.quitting = False
        self._lock = threading.Lock()
        self._hidden = background

    # windows

    def create_main(self) -> Any:
        width, height, x, y = fit_to_screen(self.state, self._screen())
        self.main = self.webview.create_window(
            APP_TITLE,
            self.url,
            width=width,
            height=height,
            x=x,
            y=y,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            hidden=self.background,
            text_select=True,
            background_color="#f7f8fa",
        )
        events = self.main.events
        events.closing += self._closing_main
        events.resized += self._resized
        events.moved += self._moved
        if hasattr(events, "maximized"):
            events.maximized += self._maximized
        if hasattr(events, "restored"):
            events.restored += self._restored
        if self.on_drop is not None and hasattr(events, "loaded"):
            events.loaded += self._bind_drop
        return self.main

    def _screen(self) -> tuple[int, int] | None:
        try:
            screen = self.webview.screens[0]
            return int(screen.width), int(screen.height)
        except Exception:  # noqa: BLE001 - screen information is optional
            return None

    def _resized(self, width: int, height: int) -> None:
        if not self.state.get("maximized"):
            self.state["width"], self.state["height"] = int(width), int(height)

    def _moved(self, x: int, y: int) -> None:
        if not self.state.get("maximized"):
            self.state["x"], self.state["y"] = int(x), int(y)

    def _maximized(self, *args) -> None:
        self.state["maximized"] = True

    def _restored(self, *args) -> None:
        self.state["maximized"] = False

    def save_state(self) -> None:
        try:
            save_ui_state(self.state, self.state_path)
        except OSError:
            log.warning("could not save window state", exc_info=True)

    def _closing_main(self) -> bool:
        """Hide instead of close. Returning False cancels the close in pywebview."""
        if self.quitting:
            self.save_state()
            return True
        self.hide_main()
        return False

    def hide_main(self) -> None:
        if self.main is None:
            return
        self.main.hide()
        self._hidden = True
        first = not self.state.get("close_hint_shown")
        self.state["close_hint_shown"] = True
        self.save_state()
        if first and self.on_first_hide is not None:
            try:
                self.on_first_hide()
            except Exception:
                log.exception("first hide hint failed")

    def show_main(self, page: str = "") -> None:
        if self.main is None:
            return
        if page:
            self.main.evaluate_js(f"window.location.hash = {page!r}")
        self.main.show()
        try:
            self.main.restore()
        except Exception:
            log.debug("restore not supported", exc_info=True)
        self._hidden = False
        self._bring_to_front(self.main)

    @staticmethod
    def _bring_to_front(window: Any) -> None:
        try:
            window.on_top = True
            window.on_top = False
        except Exception:
            log.debug("could not raise the window", exc_info=True)

    def show_quick(self) -> None:
        with self._lock:
            if self.quick is None:
                width, height = QUICK_SIZE
                self.quick = self.webview.create_window(
                    QUICK_TITLE,
                    self.quick_url,
                    width=width,
                    height=height,
                    min_size=QUICK_SIZE,
                    resizable=False,
                    on_top=True,
                    text_select=True,
                    background_color="#f7f8fa",
                )
                self.quick.events.closing += self._closing_quick
                return
        self.quick.load_url(self.quick_url)
        self.quick.show()
        self._bring_to_front(self.quick)
        self.quick.on_top = True

    def _closing_quick(self) -> bool:
        if self.quitting:
            return True
        self.quick.hide()
        return False

    # dialogs and drag and drop

    def pick_folder(self) -> str | None:
        if self.main is None:
            return None
        result = self.main.create_file_dialog(_folder_dialog_kind(self.webview))
        if not result:
            return None
        chosen = result[0] if isinstance(result, (list, tuple)) else result
        return str(chosen) or None

    def _bind_drop(self) -> None:
        """Dropped folders arrive with their full path only on the Python side of pywebview."""
        try:
            from webview.dom import DOMEventHandler
        except ImportError:
            return
        try:
            document = self.main.dom.document
            document.events.dragover += DOMEventHandler(lambda e: None, True, True)
            document.events.drop += DOMEventHandler(self._dropped, True, True)
        except Exception:
            log.info("drag and drop not available", exc_info=True)

    def _dropped(self, event: dict) -> None:
        files = (event or {}).get("dataTransfer", {}).get("files", [])
        for item in files:
            path = item.get("pywebviewFullPath") if isinstance(item, dict) else None
            if path and self.on_drop is not None:
                self.on_drop(str(path))
                return

    # lifecycle

    def run(self, on_started: Callable[[], None] | None = None) -> None:
        """Block on the GUI loop (must be the main thread) until quit()."""
        if self.main is None:
            self.create_main()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {"private_mode": False, "storage_path": str(self.storage_path)}
        if sys.platform == "win32":
            kwargs["gui"] = "edgechromium"
        icon = icon_path()
        if icon is not None:
            kwargs["icon"] = str(icon)
        args = (on_started,) if on_started else ()
        try:
            self.webview.start(*args, **kwargs)
        except TypeError:
            kwargs.pop("icon", None)
            self.webview.start(*args, **kwargs)

    def quit(self) -> None:
        self.quitting = True
        self.save_state()
        for window in (self.quick, self.main):
            if window is not None:
                try:
                    window.destroy()
                except Exception:
                    log.debug("window already closed", exc_info=True)


class DropBox:
    """Holds the last folder dropped on the window until the page asks for it."""

    def __init__(self, ttl: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self.clock = clock
        self._item: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def put(self, path: str) -> None:
        with self._lock:
            self._item = (path, self.clock())

    def take(self) -> str | None:
        with self._lock:
            item, self._item = self._item, None
        if item is None or self.clock() - item[1] > self.ttl:
            return None
        return item[0]
