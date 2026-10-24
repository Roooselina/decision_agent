from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}


_ALIASES = {"control": "ctrl", "windows": "win", "return": "enter", "esc": "escape", "spacebar": "space",
            "pgup": "pageup", "pgdn": "pagedown", "del": "delete", "ins": "insert"}
# Virtual-key codes of the named keys (winuser.h).
_NAMED = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "escape": 0x1B, "backspace": 0x08,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "insert": 0x2D, "delete": 0x2E,
}
_MOD_ORDER = ["ctrl", "alt", "shift", "win"]
DEFAULT_HOTKEY = "ctrl+shift+space"
FALLBACK_HOTKEYS = ["ctrl+shift+space", "ctrl+alt+space", "ctrl+alt+d", "ctrl+shift+k", "ctrl+alt+f9"]


def _split(spec: str) -> tuple[list[str], str]:
    parts = [_ALIASES.get(p.strip().lower(), p.strip().lower()) for p in str(spec).split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("조합키가 하나 이상 필요합니다. 예: ctrl+shift+space")
    *mods, key = parts
    for mod in mods:
        if mod not in _MODS:
            raise ValueError(f"알 수 없는 조합키: {mod}")
    if len(set(mods)) != len(mods) or len(mods) > 3:
        raise ValueError("조합키는 서로 다른 것으로 최대 3개까지입니다.")
    return mods, key


def _key_code(key: str) -> int:
    if len(key) == 1 and key.isascii() and key.isalnum():
        return ord(key.upper())
    if key in _NAMED:
        return _NAMED[key]
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x70 + int(key[1:]) - 1
    raise ValueError(f"지원하지 않는 키: {key}")


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Turn 'ctrl+shift+space' into Windows modifier flags and a virtual-key code.

    Keys: letters, digits, f1-f24, space, enter, tab, escape, backspace, arrows, home, end,
    pageup, pagedown, insert, delete. Modifiers: ctrl, alt, shift, win.
    """
    mods, key = _split(spec)
    flags = 0
    for mod in mods:
        flags |= _MODS[mod]
    return flags, _key_code(key)


def normalize_hotkey(spec: str) -> str:
    """Canonical lower-case form ("Shift+Ctrl+Space" -> "ctrl+shift+space"); raises ValueError."""
    mods, key = _split(spec)
    _key_code(key)
    return "+".join(sorted(mods, key=_MOD_ORDER.index) + [key])


def display_hotkey(spec: str) -> str:
    """How a shortcut is written on screen: "Ctrl + Shift + Space"."""
    try:
        mods, key = _split(spec)
    except ValueError:
        return spec
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    shown = key.upper() if len(key) <= 3 else key.capitalize()
    return " + ".join([names[m] for m in sorted(mods, key=_MOD_ORDER.index)] + [shown])


def failure_reason(spec: str, reason: str) -> str:
    """Explain a failed registration. Space combinations often clash with Korean input switching."""
    try:
        _, key = _split(spec)
    except ValueError:
        return reason
    if key == "space":
        return (f"{reason} Space 조합은 한/영 전환 등 입력기 단축키와 겹칠 수 있습니다. "
                "다른 조합을 고르세요.").strip()
    return reason


def suggest_hotkeys(current: str, count: int = 3) -> list[str]:
    """Alternatives to offer when the current shortcut could not be registered."""
    try:
        current = normalize_hotkey(current)
    except ValueError:
        pass
    return [h for h in FALLBACK_HOTKEYS if h != current][:count]


def hotkey_status(spec: str, enabled: bool, active: bool, reason: str) -> dict:
    """What the settings screen shows about the shortcut, including alternatives on failure."""
    failed = enabled and not active and bool(reason)
    return {
        "enabled": enabled,
        "active": active,
        "spec": spec,
        "display": display_hotkey(spec),
        "reason": failure_reason(spec, reason) if failed else reason,
        "suggestions": suggest_hotkeys(spec) if failed else [],
    }


class HotkeyBackend(Protocol):
    def run(
        self,
        modifiers: int,
        vk: int,
        on_hotkey: Callable[[], None],
        stop: threading.Event,
        ready: Callable[[bool, str], None],
    ) -> None: ...


class WindowsBackend:
    """Global hotkey through RegisterHotKey. Needs no extra package and no administrator rights."""

    def run(self, modifiers, vk, on_hotkey, stop, ready) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        if not user32.RegisterHotKey(None, 1, modifiers | MOD_NOREPEAT, vk):
            ready(False, "다른 프로그램이 이미 이 단축키를 사용하고 있습니다.")
            return
        ready(True, "")
        message = wintypes.MSG()
        try:
            while not stop.is_set():
                if user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    if message.message == WM_HOTKEY:
                        try:
                            on_hotkey()
                        except Exception:
                            log.exception("hotkey handler failed")
                else:
                    time.sleep(0.05)
        finally:
            user32.UnregisterHotKey(None, 1)


class HotkeyListener:
    """Runs the backend on its own thread and reports whether registration worked."""

    def __init__(
        self, spec: str, callback: Callable[[], None], backend: HotkeyBackend | None = None
    ) -> None:
        self.spec = spec
        self.callback = callback
        self.backend = backend
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.reason = ""
        self.active = False

    def start(self) -> bool:
        backend = self.backend
        if backend is None:
            if sys.platform != "win32":
                self.reason = "전역 단축키는 Windows에서만 지원합니다. 트레이 메뉴를 사용하세요."
                return False
            backend = WindowsBackend()
        try:
            modifiers, vk = parse_hotkey(self.spec)
        except ValueError as exc:
            self.reason = str(exc)
            return False
        result: dict = {}
        done = threading.Event()

        def ready(ok: bool, reason: str) -> None:
            result["ok"], result["reason"] = ok, reason
            done.set()

        self._stop.clear()
        self._thread = threading.Thread(
            target=backend.run,
            args=(modifiers, vk, self.callback, self._stop, ready),
            name="hotkey",
            daemon=True,
        )
        self._thread.start()
        if not done.wait(3.0):
            self.reason = "단축키 등록 응답이 없습니다."
            self.stop()
            return False
        self.active = bool(result["ok"])
        self.reason = result["reason"]
        return self.active

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.active = False
