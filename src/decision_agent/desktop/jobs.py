"""The job board: which folder job runs next, with a small pool of workers.

Each folder runs at most one job at a time (its engine and state database are used by one job),
but different folders run side by side, so a folder that waits for confirmation, failed, or
runs a long confirmed check never stops the others. Jobs the user started go ahead of automatic
ones. Settings changes that rebuild the folders wait for the running jobs through exclusive().
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator

USER = 0
AUTO = 1


class JobBoard:
    def __init__(self, held: Callable[[object], bool] | None = None) -> None:
        self.cond = threading.Condition()
        self.pending: list = []
        self.running: dict[str, int] = {}
        self.closed = False
        self.seq = 0
        self.blocked = 0
        # held(job) says that an automatic job of a paused folder must wait.
        self.held = held or (lambda job: False)

    def put(self, job) -> None:
        with self.cond:
            self.seq += 1
            job.seq = self.seq
            self.pending.append(job)
            self.cond.notify_all()

    def take(self):
        """The next runnable job, or None once the board is closed. Blocks until one is free."""
        with self.cond:
            while True:
                if self.closed:
                    return None
                job = None
                if not self.blocked:
                    runnable = [j for j in self.pending if j.root not in self.running and not self.held(j)]
                    job = min(runnable, key=lambda j: (j.priority, j.seq), default=None)
                if job is not None:
                    self.pending.remove(job)
                    self.running[job.root] = threading.get_ident()
                    return job
                self.cond.wait(1.0)

    def done(self, job) -> None:
        with self.cond:
            self.running.pop(job.root, None)
            self.cond.notify_all()

    def wake(self) -> None:
        """Look again at held jobs (a folder was resumed)."""
        with self.cond:
            self.cond.notify_all()

    def close(self) -> None:
        with self.cond:
            self.closed = True
            self.cond.notify_all()

    def drop(self, root: str, kinds: set[str] | None = None) -> int:
        """Remove waiting jobs of a folder (all, or of some kinds). Returns how many."""
        with self.cond:
            keep = [j for j in self.pending if j.root != root or (kinds is not None and j.kind not in kinds)]
            dropped = len(self.pending) - len(keep)
            self.pending = keep
            return dropped

    def idle(self) -> bool:
        with self.cond:
            return not self.pending and not self.running

    def queued_roots(self) -> set[str]:
        with self.cond:
            return {j.root for j in self.pending}

    def running_roots(self) -> set[str]:
        with self.cond:
            return set(self.running)

    @contextlib.contextmanager
    def exclusive(self) -> Iterator[None]:
        """No job starts, and the running ones (of other threads) have finished, inside."""
        me = threading.get_ident()
        with self.cond:
            self.blocked += 1
            while any(ident != me for ident in self.running.values()):
                self.cond.wait(1.0)
        try:
            yield
        finally:
            with self.cond:
                self.blocked -= 1
                self.cond.notify_all()
