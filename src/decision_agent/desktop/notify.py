from __future__ import annotations

import logging
import sys
from collections.abc import Callable

log = logging.getLogger(__name__)


class Notifier:
    """Base notifier. Implementations must never raise."""

    def notify(self, title: str, message: str) -> None:
        raise NotImplementedError


class LogNotifier(Notifier):
    def notify(self, title: str, message: str) -> None:
        log.info("notification: %s | %s", title, message)


class WindowsToastNotifier(Notifier):
    """Windows toast through winotify (pure Python, no extra system dependency)."""

    def __init__(self, app_id: str = "결정 변경 점검") -> None:
        self.app_id = app_id

    def notify(self, title: str, message: str) -> None:
        try:
            from winotify import Notification

            Notification(app_id=self.app_id, title=title, msg=message, duration="short").show()
        except Exception:
            log.warning("toast failed", exc_info=True)


class CallbackNotifier(Notifier):
    """Delegates to a function, for example the tray icon balloon."""

    def __init__(self, callback: Callable[[str, str], None]) -> None:
        self.callback = callback

    def notify(self, title: str, message: str) -> None:
        try:
            self.callback(title, message)
        except Exception:
            log.warning("callback notification failed", exc_info=True)


class ChainNotifier(Notifier):
    """Sends through the first notifier that is available, and always logs."""

    def __init__(self, primary: Notifier | None, fallback: Notifier | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.log = LogNotifier()

    def notify(self, title: str, message: str) -> None:
        self.log.notify(title, message)
        for target in (self.primary, self.fallback):
            if target is not None:
                target.notify(title, message)
                return


def make_notifier(tray_callback: Callable[[str, str], None] | None = None) -> Notifier:
    tray = CallbackNotifier(tray_callback) if tray_callback is not None else None
    if sys.platform == "win32":
        try:
            import winotify  # noqa: F401

            return ChainNotifier(WindowsToastNotifier(), tray)
        except ImportError:
            pass
    return ChainNotifier(tray)
