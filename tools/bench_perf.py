"""Speed benchmark: wall time, model requests, CPU time, memory and time per phase.

Usage:
  python tools/bench_perf.py [--latency 0.4] [--out out/perf] [--label before]
                             [--folders capstone,full] [--profiles balanced,precise] [--app] [--idle 60]

The model is simulated (tools/bench_demo.py's decision-aware simulation) with a fixed artificial
delay per request, so parallelism shows up as it would with a real provider. --latency 0 gives
pure CPU time. Real-network timing will differ. Every folder is copied to a temporary directory
first; nothing in the demo packs is changed.

Phases are summed wall time of the instrumented functions across threads, so with parallel
work they can add up to more than the wall time:
  parse      reading files (ProjectEngine.sync)
  ingest     building meeting items and artifacts (build_inputs)
  retrieval  candidate ranking (impact and relation candidates)
  judge      waiting for model requests (all tasks)
  store      state database writes
"""

from __future__ import annotations

import argparse
import functools
import json
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_demo import DecisionAwareSim
from make_demo_capstone import mail_documents

from decision_agent.connectors.base import MailMessage, mail_document
from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.progress import ProgressTracker
from decision_agent.desktop.providers import ProviderBundle
from decision_agent.desktop.settings import AppSettings
from decision_agent.desktop.state import StateDB
from decision_agent.llm.base import LLMProvider

try:
    import psutil
except ImportError:  # memory and idle CPU are then not reported
    psutil = None

PACKS = {
    "capstone": Path.home() / "Downloads" / "demo_capstone",
    "full": Path.home() / "Downloads" / "demo_capstone_full",
}
STORE_PREFIXES = ("put", "upsert", "replace", "topic_put", "store", "add_", "save", "delete", "meta_put", "touch",
                  "clear", "set_")


class Phases:
    """Summed wall time per phase, filled by wrappers on the instrumented functions."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.seconds: dict[str, float] = defaultdict(float)
        self.local = threading.local()

    def add(self, name: str, seconds: float) -> None:
        with self.lock:
            self.seconds[name] += seconds

    def wrap(self, name: str, fn):
        @functools.wraps(fn)
        def timed(*args, **kwargs):
            # Nested calls of the same phase are counted once.
            depth = getattr(self.local, name, 0)
            setattr(self.local, name, depth + 1)
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                setattr(self.local, name, depth)
                if depth == 0:
                    self.add(name, time.perf_counter() - start)
        return timed

    def snapshot(self) -> dict[str, float]:
        with self.lock:
            return dict(self.seconds)


PHASES = Phases()


def instrument() -> None:
    from decision_agent import impact, relations
    from decision_agent.desktop import engine as engine_module

    ProjectEngine.sync = PHASES.wrap("parse", ProjectEngine.sync)
    engine_module.build_inputs = PHASES.wrap("ingest", engine_module.build_inputs)
    impact._rank_candidates = PHASES.wrap("retrieval", impact._rank_candidates)
    relations.relation_candidates = PHASES.wrap("retrieval", relations.relation_candidates)
    for name in dir(StateDB):
        if name.startswith(STORE_PREFIXES) and callable(getattr(StateDB, name)):
            setattr(StateDB, name, PHASES.wrap("store", getattr(StateDB, name)))


class SlowModel(LLMProvider):
    """The simulated model with a fixed delay per request (one batch is one request)."""

    def __init__(self, sim: DecisionAwareSim, latency: float) -> None:
        self.sim = sim
        self.latency = latency
        self.requests = 0
        self.lock = threading.Lock()

    def complete(self, system, user, temperature=0.0, json_schema=None):
        with self.lock:
            self.requests += 1
        start = time.perf_counter()
        if self.latency:
            time.sleep(self.latency)
        answer = self.sim.complete(system, user, temperature, json_schema)
        PHASES.add("judge", time.perf_counter() - start)
        return answer


class Memory:
    """Peak resident memory, sampled every 50 ms while a scenario runs."""

    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        if psutil is None:
            return self
        process = psutil.Process()
        self.peak = process.memory_info().rss

        def sample():
            while not self._stop.wait(0.05):
                self.peak = max(self.peak, process.memory_info().rss)

        self._thread = threading.Thread(target=sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()


def measure(label: str, model: SlowModel, action) -> dict:
    before_phases = PHASES.snapshot()
    before_requests = model.requests
    cpu = time.process_time()
    with Memory() as memory:
        start = time.perf_counter()
        result = action()
        wall = time.perf_counter() - start
    after = PHASES.snapshot()
    phases = {k: round(after.get(k, 0) - before_phases.get(k, 0), 3) for k in sorted(set(after) | set(before_phases))}
    row = {
        "scenario": label, "wall": round(wall, 3), "requests": model.requests - before_requests,
        "cpu": round(time.process_time() - cpu, 3), "peak_mb": round(memory.peak / 2**20, 1) if memory.peak else None,
        "phases": {k: v for k, v in phases.items() if v}, **(result or {}),
    }
    print(f"  {label:34s} wall {row['wall']:7.2f}s  requests {row['requests']:4d}  cpu {row['cpu']:6.2f}s  "
          f"peak {row['peak_mb']} MB  {row['phases']}")
    return row


def engine_scenarios(name: str, profile: str, latency: float, workers: int, mode: str = "normal") -> list[dict]:
    pack = PACKS[name]
    source = pack / pack.name
    key = json.loads((pack / f"{pack.name}_answer_key.json").read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="bench_perf_")) / pack.name
    shutil.copytree(source, work)
    settings = AppSettings(allowed_folders=[str(work)], cost_profile=profile, max_llm_calls_per_cycle=100000,
                           confirm_over_calls=0, perf_mode=mode)
    sim = DecisionAwareSim(key)
    model = SlowModel(sim, latency)
    bundle = ProviderBundle(model, "sim", False, "simulated", True)
    db = StateDB(work.parent / "state.db")
    state = {"engine": ProjectEngine(work, settings, db, bundle, ProgressTracker())}
    stale = {line["text"] for line in key["artifact_lines"] if line["expected"] == "INCONSISTENT"}

    def run_check():
        engine = state["engine"]
        engine.sync()
        report = engine.analyze()
        found = {r["text"] for r in db.list_findings(include_hidden=True) if r["label"] == "INCONSISTENT"}
        return {"found": len(found & stale), "false": len(found - stale), "reused": report.reused_lines}

    def append_line():
        target = work / "requirements" / "requirements.md"
        target.write_text(target.read_text(encoding="utf-8") + "- NFR-05 로그인 실패는 5회까지 허용한다\n", encoding="utf-8")
        return run_check()

    def new_meeting():
        text = "검색 결과는 최신 등록순 대신 가격 낮은순으로 바꾸기로 한다"
        sim.decisions[text] = ("search_order", "가격 낮은순")
        (work / "meetings" / "2026-10-27_추가회의.md").write_text(
            f"# 2026-10-27 추가 회의\n\n## 결정\n- {text}\n", encoding="utf-8")
        return run_check()

    def new_mail():
        docs = mail_documents()
        state["engine"].set_external("gmail", docs)
        result = run_check()
        extra = mail_document(MailMessage("x1", "2026-10-28", "회의 결과", "민재 <m@example.com>",
                                          "오늘 회의에서 쪽지 새로고침은 10초 간격 그대로 두기로 했다"), {})
        docs[extra[0]] = extra[1]
        state["engine"].set_external("gmail", docs)
        return result

    def switch(to_profile):
        def go():
            other = settings.model_copy(update={"cost_profile": to_profile})
            state["engine"] = ProjectEngine(work, other, db, bundle, ProgressTracker())
            return run_check()
        return go

    other = "precise" if profile != "precise" else "balanced"
    print(f"{name} / {profile} / {mode} / latency {latency}s / {work}")
    rows = [
        measure("cold first scan", model, run_check),
        measure("no change rescan", model, run_check),
        measure("one line added to one file", model, append_line),
        measure("one new meeting with one decision", model, new_meeting),
        measure("mail threads arrive (17 mails)", model, new_mail),
        measure("one more mail", model, run_check),
        measure(f"profile {profile} -> {other}", model, switch(other)),
        measure(f"profile back to {profile}", model, switch(profile)),
    ]
    for row in rows:
        row.update({"folder": name, "profile": profile, "files": sum(1 for _ in work.rglob("*") if _.is_file())})
    return rows


def app_scenarios(idle_seconds: int) -> list[dict]:
    """App start, status with five folders, the mail page with 5000 mails, idle CPU."""
    import os

    os.environ["DECISION_AGENT_HOME"] = tempfile.mkdtemp(prefix="bench_home_")
    from fastapi.testclient import TestClient

    from decision_agent.desktop.app import DecisionAgentApp
    from decision_agent.desktop.maildb import MailDB
    from decision_agent.desktop.webapp import create_web_app

    rows = []
    base = Path(tempfile.mkdtemp(prefix="bench_app_"))
    folders = []
    for n in range(5):
        target = base / f"folder{n}"
        shutil.copytree(PACKS["capstone"] / "demo_capstone", target)
        folders.append(str(target))
    settings = AppSettings(allowed_folders=folders, provider="mock", rescan_minutes=0, debounce_seconds=0.5)
    started = time.perf_counter()
    app = DecisionAgentApp(settings, settings_file=base / "settings.json", start_watchers=True)
    app.start()
    ready = time.perf_counter() - started
    client = TestClient(create_web_app(app, "t" * 32, 8765), base_url="http://127.0.0.1:8765")
    first = client.get("/api/status", headers={"X-DA-Token": "t" * 32})
    first_status = time.perf_counter() - started
    rows.append({"scenario": "app start to ready (window can show)", "wall": round(ready, 3)})
    rows.append({"scenario": "app start to first status", "wall": round(first_status, 3), "ok": first.status_code == 200})
    app.wait_idle(600)
    times = []
    for _ in range(20):
        t = time.perf_counter()
        client.get("/api/status", headers={"X-DA-Token": "t" * 32})
        times.append(time.perf_counter() - t)
    rows.append({"scenario": "status request, 5 folders (median of 20)", "wall": round(sorted(times)[10], 4)})
    db = MailDB()
    db.upsert("big", [MailMessage(f"m{i}", f"2026-10-{1 + i % 28:02d}", f"제목 {i % 700}", f"p{i % 90}@example.com",
                                  "본문 " * 40, thread_id=f"T{i % 1200}") for i in range(5000)])
    app.maildb = db
    times = []
    for _ in range(5):
        t = time.perf_counter()
        client.get("/api/mail/threads", headers={"X-DA-Token": "t" * 32})
        times.append(time.perf_counter() - t)
    rows.append({"scenario": "mail page open, 5000 mails (median of 5)", "wall": round(sorted(times)[2], 4)})
    if psutil is not None and idle_seconds:
        process = psutil.Process()
        process.cpu_percent(None)
        time.sleep(idle_seconds)
        rows.append({"scenario": f"idle CPU over {idle_seconds} s (percent of one core)",
                     "wall": round(process.cpu_percent(None), 2)})
    app.stop()
    for row in rows:
        print(f"  {row['scenario']:44s} {row['wall']}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latency", type=float, default=0.4)
    parser.add_argument("--out", default=str(ROOT / "out" / "perf"))
    parser.add_argument("--label", default="run")
    parser.add_argument("--folders", default="capstone,full")
    parser.add_argument("--profiles", default="balanced,precise")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--mode", default="normal", choices=["save", "normal", "max"])
    parser.add_argument("--app", action="store_true")
    parser.add_argument("--idle", type=int, default=60)
    args = parser.parse_args()
    instrument()
    rows: list[dict] = []
    for name in [f for f in args.folders.split(",") if f]:
        for profile in [p for p in args.profiles.split(",") if p]:
            rows.extend(engine_scenarios(name, profile, args.latency, args.workers, args.mode))
    if args.app:
        rows.extend(app_scenarios(args.idle))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"bench_{args.label}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out / f'bench_{args.label}.json'}; requests by task: {dict(Counter())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
