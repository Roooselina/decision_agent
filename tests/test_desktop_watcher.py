from __future__ import annotations

import threading
import time
from pathlib import Path

from decision_agent.desktop.policy import FolderPolicy
from decision_agent.desktop.settings import DEFAULT_EXCLUDES
from decision_agent.desktop.watcher import Debouncer, FolderWatcher, PeriodicTimer, is_relevant


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_flush_fires_once_with_all_paths():
    got = []
    deb = Debouncer(10, got.append)
    for name in ("a", "b", "a"):
        deb.add(Path(name))
    assert deb.pending_count() == 2
    deb.flush()
    assert got == [{Path("a"), Path("b")}] and deb.pending_count() == 0
    deb.flush()
    assert len(got) == 1


def test_burst_is_batched_into_one_call():
    got = []
    deb = Debouncer(0.2, got.append, max_wait=5)
    deb.start()
    try:
        for i in range(5):
            deb.add(Path(f"f{i}"))
            time.sleep(0.03)
        assert wait_for(lambda: len(got) == 1)
        time.sleep(0.4)
        assert len(got) == 1 and len(got[0]) == 5
    finally:
        deb.stop()


def test_max_wait_bounds_the_delay_under_constant_events():
    got = []
    deb = Debouncer(0.5, got.append, max_wait=0.6)
    deb.start()
    stop = threading.Event()

    def spam():
        while not stop.is_set():
            deb.add(Path("busy.md"))
            time.sleep(0.05)

    thread = threading.Thread(target=spam)
    thread.start()
    try:
        assert wait_for(lambda: len(got) >= 1, timeout=3.0)
    finally:
        stop.set()
        thread.join()
        deb.stop()


def test_callback_error_does_not_kill_the_loop():
    calls = []

    def callback(paths):
        calls.append(paths)
        if len(calls) == 1:
            raise RuntimeError("boom")

    deb = Debouncer(0.05, callback, max_wait=1)
    deb.start()
    try:
        deb.add(Path("a"))
        assert wait_for(lambda: len(calls) == 1)
        deb.add(Path("b"))
        assert wait_for(lambda: len(calls) == 2)
    finally:
        deb.stop()


def test_is_relevant_filters_by_extension_and_exclusions(tmp_path):
    policy = FolderPolicy(tmp_path.resolve(), list(DEFAULT_EXCLUDES), 25)
    root = tmp_path.resolve()
    assert is_relevant(policy, root / "meetings" / "a.md")
    assert is_relevant(policy, root / "plan.docx")
    assert is_relevant(policy, root / "old.hwp")
    assert is_relevant(policy, root / "newdir")
    assert not is_relevant(policy, root / "photo.png")
    assert not is_relevant(policy, root / ".git" / "config")
    assert not is_relevant(policy, root / "node_modules" / "x.md")
    assert not is_relevant(policy, tmp_path.parent / "outside.md")


def test_real_filesystem_event_reaches_callback(tmp_path):
    root = tmp_path.resolve()
    seen = []
    policy = FolderPolicy(root, [], 25)
    watcher = FolderWatcher(policy, seen.append)
    watcher.start()
    try:
        time.sleep(0.3)
        (root / "note.md").write_text("hello", encoding="utf-8")
        (root / "image.png").write_bytes(b"x")
        assert wait_for(lambda: any(p.name == "note.md" for p in seen))
        assert not any(p.name == "image.png" for p in seen)
    finally:
        watcher.stop()


def test_periodic_timer_runs_and_stops():
    count = []
    timer = PeriodicTimer(0.05, lambda: count.append(1))
    timer.start()
    assert wait_for(lambda: len(count) >= 2)
    timer.stop()
    settled = len(count)
    time.sleep(0.2)
    assert len(count) == settled
    off = PeriodicTimer(0, lambda: count.append(1))
    off.start()
    off.stop()
