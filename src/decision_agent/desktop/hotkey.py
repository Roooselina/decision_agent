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


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Turn 'ctrl+alt+d' into Windows modifier flags and a virtual-key code."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("조합키가 하나 이상 필요합니다. 예: ctrl+alt+d")
    *mods, key = parts
    flags = 0
    for mod in mods:
        if mod not in _MODS:
            raise ValueError(f"알 수 없는 조합키: {mod}")
        flags |= _MODS[mod]
    if len(key) == 1 and key.isalnum():
        return flags, ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        return flags, 0x70 + int(key[1:]) - 1
    raise ValueError(f"지원하지 않는 키: {key}")


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
