from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DecisionAgent"


class Autostart:
    """Start the program when the user signs in to Windows (per-user Run registry key).

    The registry module is injectable so the logic can be tested on any platform. Only the
    current user's key is touched, so no administrator rights are needed.
    """

    def __init__(self, registry: Any = None, command: str | None = None, platform: str = sys.platform) -> None:
        self.platform = platform
        self._registry = registry
        self._command = command

    @property
    def supported(self) -> bool:
        return self.platform == "win32" or self._registry is not None

    def _reg(self):
        if self._registry is not None:
            return self._registry
        import winreg

        return winreg

    def command_line(self) -> str:
        if self._command:
            return self._command
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" run --background'
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        interpreter = pythonw if pythonw.exists() else Path(sys.executable)
        return f'"{interpreter}" -m decision_agent.desktop run --background'

    def is_enabled(self) -> bool:
        if not self.supported:
            return False
        reg = self._reg()
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
                value, _ = reg.QueryValueEx(key, VALUE_NAME)
        except OSError:
            return False
        return bool(value)

    def enable(self) -> None:
        if not self.supported:
            raise RuntimeError("자동 실행은 Windows에서만 지원합니다.")
        reg = self._reg()
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.SetValueEx(key, VALUE_NAME, 0, reg.REG_SZ, self.command_line())

    def disable(self) -> None:
        if not self.supported:
            return
        reg = self._reg()
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key, VALUE_NAME)
        except OSError:
            return

    def toggle(self) -> bool:
        if self.is_enabled():
            self.disable()
            return False
        self.enable()
        return True
