from __future__ import annotations

import collections
import logging
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

PHASE_LABELS = {
    "idle": "대기 중",
    "reading": "파일 읽는 중",
    "extracting": "결정 추출 중",
    "checking": "결정별 점검 중",
    "done": "완료",
    "error": "문제 발생",
}


class ActivityLog:
    """Short in-memory list of what the program did recently, shown in the dashboard.

    The full technical log is the rotating log file; this list holds readable one-liners.
    """

    def __init__(self, limit: int = 300) -> None:
        self._items: collections.deque[dict] = collections.deque(maxlen=limit)
        self._lock = threading.Lock()

    def add(self, text: str, level: str = "info") -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._items.append({"time": stamp, "level": level, "text": text})

    def recent(self, count: int = 100) -> list[dict]:
        with self._lock:
            items = list(self._items)
        return items[-count:][::-1]


class ProgressTracker:
    """Counts what one analysis cycle has finished, so the UI can show X of Y.

    files are the documents read in this cycle; topics are the decisions being checked.
    A topic whose inputs did not change counts as done (and as unchanged), so the bar reaches 100.
    """

    def __init__(
        self, on_change: Callable[[], None] | None = None, activity: ActivityLog | None = None
    ) -> None:
        self._lock = threading.Lock()
        self.on_change = on_change
        self.activity = activity or ActivityLog()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.phase = "idle"
            self.files_total = 0
            self.files_done = 0
            self.topics_total = 0
            self.topics_done = 0
            self.topics_unchanged = 0
            self.llm_calls = 0
            self.started_at: float | None = None
            self.finished_at: float | None = None
            self.current = ""

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                log.debug("progress listener failed", exc_info=True)

    def begin(self) -> None:
        self.reset()
        with self._lock:
            self.started_at = time.time()
        self._notify()

    def set_phase(self, phase: str, current: str = "") -> None:
        with self._lock:
            self.phase = phase
            self.current = current
            if phase == "done":
                self.finished_at = time.time()
        self._notify()

    def set_files(self, total: int) -> None:
        with self._lock:
            self.files_total = total
            self.files_done = 0
        self._notify()

    def file_done(self, name: str = "") -> None:
        with self._lock:
            self.files_done += 1
            if name:
                self.current = name
        self._notify()

    def set_topics(self, total: int) -> None:
        with self._lock:
            self.topics_total = total
            self.topics_done = 0
            self.topics_unchanged = 0
        self._notify()

    def topic_done(self, name: str = "", unchanged: bool = False) -> None:
        with self._lock:
            self.topics_done += 1
            if unchanged:
                self.topics_unchanged += 1
            if name:
                self.current = name
        self._notify()

    def set_llm_calls(self, calls: int) -> None:
        with self._lock:
            self.llm_calls = calls

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = None
            if self.started_at is not None:
                elapsed = round((self.finished_at or time.time()) - self.started_at, 1)
            return {
                "phase": self.phase,
                "phase_label": PHASE_LABELS.get(self.phase, self.phase),
                "files_total": self.files_total,
                "files_done": self.files_done,
                "topics_total": self.topics_total,
                "topics_done": self.topics_done,
                "topics_unchanged": self.topics_unchanged,
                "llm_calls": self.llm_calls,
                "current": self.current,
                "elapsed_seconds": elapsed,
            }
