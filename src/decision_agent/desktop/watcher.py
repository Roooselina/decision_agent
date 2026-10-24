from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from decision_agent.desktop.policy import FolderPolicy
from decision_agent.sources.readers import READERS, UNSUPPORTED_HINTS

log = logging.getLogger(__name__)


class Debouncer:
    """Collects changed paths and fires once after a quiet period.

    A burst of events (an editor saving, a sync client writing many files) produces one call.
    max_wait bounds the delay when events never stop, for example with frequent autosave.
    """

    def __init__(
        self,
        delay: float,
        callback: Callable[[set[Path]], None],
        max_wait: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.delay = delay
        self.max_wait = max_wait if max_wait is not None else max(delay * 4, 5.0)
        self.callback = callback
        self.clock = clock
        self._pending: dict[Path, float] = {}
        self._first: float | None = None
        self._last: float = 0.0
        self._cond = threading.Condition()
        self._stop = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name="debouncer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def add(self, path: Path) -> None:
        now = self.clock()
        with self._cond:
            if self._first is None:
                self._first = now
            self._last = now
            self._pending[path] = now
            self._cond.notify_all()

    def pending_count(self) -> int:
        with self._cond:
            return len(self._pending)

    def flush(self) -> None:
        """Fire immediately with whatever is pending."""
        with self._cond:
            batch = self._take()
        if batch:
            self._fire(batch)

    def _take(self) -> set[Path]:
        batch = set(self._pending)
        self._pending.clear()
        self._first = None
        return batch

    def _fire(self, batch: set[Path]) -> None:
        try:
            self.callback(batch)
        except Exception:
            log.exception("debounce callback failed")

    def _loop(self) -> None:
        while True:
            with self._cond:
                if self._stop:
                    return
                if not self._pending:
                    self._cond.wait(timeout=1.0)
                    continue
                now = self.clock()
                quiet_for = now - self._last
                waited = now - (self._first if self._first is not None else now)
                if quiet_for >= self.delay or waited >= self.max_wait:
                    batch = self._take()
                else:
                    remaining = min(self.delay - quiet_for, self.max_wait - waited)
                    self._cond.wait(timeout=max(0.01, min(remaining, 1.0)))
                    continue
            self._fire(batch)


def is_relevant(policy: FolderPolicy, path: Path) -> bool:
    """Cheap pre-filter on event paths; the engine applies the full policy again before reading."""
    try:
        relative = path.absolute().relative_to(policy.root).parts
    except ValueError:
        try:
            relative = path.resolve().relative_to(policy.root).parts
        except (OSError, ValueError):
            return False
    if not relative or policy.is_excluded_relative(relative):
        return False
    suffix = path.suffix.lower().lstrip(".")
    if not suffix:
        return True
    return suffix in READERS or suffix in UNSUPPORTED_HINTS


class FolderWatcher:
    """Watches one folder with watchdog and feeds relevant paths to a callback."""

    def __init__(self, policy: FolderPolicy, on_path: Callable[[Path], None]) -> None:
        self.policy = policy
        self.on_path = on_path
        self.observer = None
        self.mode = "native"

    def _handler(self):
        from watchdog.events import FileSystemEventHandler

        watcher = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event) -> None:
                if event.event_type in {"opened", "closed", "closed_no_write"}:
                    return
                paths = [event.src_path]
                dest = getattr(event, "dest_path", "")
                if dest:
                    paths.append(dest)
                for raw in paths:
                    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                    path = Path(text)
                    if is_relevant(watcher.policy, path):
                        watcher.on_path(path)

        return Handler()

    def start(self) -> None:
        from watchdog.observers import Observer
        from watchdog.observers.polling import PollingObserver

        handler = self._handler()
        try:
            observer = Observer()
            observer.schedule(handler, str(self.policy.root), recursive=True)
            observer.start()
            self.mode = "native"
        except Exception:  # noqa: BLE001 - fall back to polling on unsupported file systems
            log.warning("native watcher failed for %s, using polling", self.policy.root)
            observer = PollingObserver(timeout=10)
            observer.schedule(handler, str(self.policy.root), recursive=True)
            observer.start()
            self.mode = "polling"
        self.observer = observer

    def stop(self) -> None:
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None


class PeriodicTimer:
    """Calls a function every interval seconds until stopped; used for the safety rescan."""

    def __init__(self, interval: float, callback: Callable[[], None]) -> None:
        self.interval = interval
        self.callback = callback
        self._event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.interval <= 0:
            return
        self._event.clear()
        self._thread = threading.Thread(target=self._loop, name="rescan-timer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._event.wait(self.interval):
            try:
                self.callback()
            except Exception:
                log.exception("periodic callback failed")
