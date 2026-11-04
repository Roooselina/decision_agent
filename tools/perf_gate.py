"""Regression gate: speed work must not change what is found.

Usage:  python tools/perf_gate.py [--latency 0]

Runs the simulated perfect model (see tools/bench_demo.py and tools/bench_incremental.py) on the
three demo folders and fails (exit code 1) when a detected count drops below the floor or a
false finding appears. The floors are the counts of the build before the speed work.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DOWNLOADS = Path.home() / "Downloads"
# (pack, files only, with mail): the stale lines found before the speed work.
FLOORS = {"demo_capstone": (6, 10), "demo_capstone_full": (5, 9)}
DEMO_BIG_FLOOR = 145


def capstone(name: str) -> list[str]:
    import bench_demo

    pack = DOWNLOADS / name
    key = json.loads((pack / f"{name}_answer_key.json").read_text(encoding="utf-8"))
    files, mail, _ = bench_demo.run(pack / name, key, echo=lambda *_: None)
    floor_files, floor_mail = FLOORS[name]
    problems = []
    if files["found"] < floor_files or mail["found"] < floor_mail:
        problems.append(f"{name}: found {files['found']} (files) / {mail['found']} (with mail), "
                        f"floor {floor_files} / {floor_mail}")
    if files["false"] or mail["false"]:
        problems.append(f"{name}: false findings {files['false'] + mail['false']}")
    print(f"{name}: files {files['found']}/{files['expected']}, with mail {mail['found']}/{mail['expected']}, "
          f"false {len(files['false']) + len(mail['false'])}")
    return problems


def demo_big() -> list[str]:
    from bench_incremental import Sim

    from decision_agent.desktop.engine import ProjectEngine
    from decision_agent.desktop.progress import ProgressTracker
    from decision_agent.desktop.providers import ProviderBundle
    from decision_agent.desktop.settings import AppSettings
    from decision_agent.desktop.state import StateDB

    pack = DOWNLOADS / "demo_big"
    if not pack.exists():
        pack = ROOT / "samples" / "demo_big"
    key = json.loads((pack / "demo_big_answer_key.json").read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="perf_gate_")) / "demo_big"
    shutil.copytree(pack / "demo_big", work)
    settings = AppSettings(allowed_folders=[str(work)], max_llm_calls_per_cycle=100000, confirm_over_calls=0)
    db = StateDB(work.parent / "state.db")
    engine = ProjectEngine(work, settings, db, ProviderBundle(Sim(key), "sim", False, "simulated", True), ProgressTracker())
    engine.sync()
    engine.analyze()
    truth = {(line["file"], line["text"]) for line in key["artifact_lines"] if line["expected"] == "INCONSISTENT"}
    found = {(r["source_doc"], r["text"]) for r in db.list_findings(include_hidden=True) if r["label"] == "INCONSISTENT"}
    detected = len(found & truth)
    print(f"demo_big: {detected}/{len(truth)}")
    return [] if detected >= DEMO_BIG_FLOOR else [f"demo_big: {detected} found, floor {DEMO_BIG_FLOOR}"]


def main() -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    problems = capstone("demo_capstone") + capstone("demo_capstone_full") + demo_big()
    for problem in problems:
        print("FAIL:", problem)
    print("gate passed" if not problems else "gate failed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
