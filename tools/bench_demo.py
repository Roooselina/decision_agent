"""Benchmark of a capstone demo folder with a simulated perfect model.

Usage:  python tools/bench_demo.py <demo_folder> <answer_key.json> [profile]

It copies the folder to a temporary directory first. Step 1 checks the files alone; step 2
adds the 17 thread messages of demo/mail_threads.json as connector documents. For each step it
prints the stale lines found out of the key, false findings, model calls and time.

The simulated model reads the answer key, so it is perfect by construction: it says a line is
stale only when the decision being checked sets another value for the same topic. A wrong
pairing made by the pipeline (an old decision checked against a line, a missed candidate)
still shows up. These numbers are no evidence of real-model accuracy.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.progress import ProgressTracker
from decision_agent.desktop.providers import ProviderBundle
from decision_agent.desktop.settings import AppSettings
from decision_agent.desktop.state import StateDB
from decision_agent.llm.base import LLMProvider
from decision_agent.llm.batch import answer_batch, is_batch
from decision_agent.llm.mock_provider import MockProvider

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_demo_capstone import mail_documents

SCOPE = {
    "scope_kind": "GLOBAL", "release": None, "module": None, "user_group": None,
    "environment": None, "unmapped_terms": [],
}


def field(user: str, name: str) -> str:
    match = re.search(rf"^{name}=(.*)$", user, re.MULTILINE)
    return match.group(1).strip() if match else ""


class DecisionAwareSim(LLMProvider):
    """Perfect model for the capstone key. Counts calls per task type."""

    def __init__(self, key: dict) -> None:
        self.mock = MockProvider()
        self.lock = threading.Lock()
        self.by_task: Counter = Counter()
        self.decisions = {d["text"]: (d["topic"], d["value"]) for d in key["decisions"]}
        self.lines = {line["text"]: (line["topic"], line["value"]) for line in key["artifact_lines"]}

    def complete(self, system, user, temperature=0.0, json_schema=None):
        match = re.search(r"TASK:([A-Z_]+)", system)
        name = match.group(1) if match else "OTHER"
        with self.lock:
            self.by_task[name] += 1
        if is_batch(system):
            def single(sys_, usr, temp, schema):
                return self._answer(re.search(r"TASK:([A-Z_]+)", sys_).group(1), usr, sys_, temp, schema)
            return answer_batch(single, system, user, temperature)
        return self._answer(name, user, system, temperature, json_schema)

    def _answer(self, name, user, system, temperature, json_schema):
        if name == "DECISION_EXTRACT":
            text = user.split("TEXT=", 1)[1].strip()
            found = self.decisions.get(text)
            return json.dumps({"is_decision": found is not None, "text": text,
                               "topic": found[0] if found else "misc", "scope": SCOPE})
        if name == "RELATION_CLASSIFY":
            old = self.decisions.get(field(user, "OLD"))
            new = self.decisions.get(field(user, "NEW"))
            same = old is not None and new is not None and old[0] == new[0]
            return json.dumps({"relation_type": "SUPERSEDE" if same else "NONE", "affected_scope": SCOPE, "evidence": "sim"})
        if name == "IMPACT_SELECT":
            return json.dumps({"artifact_ids": re.findall(r"^ID=(\S+) KIND=", user, re.MULTILINE)})
        if name == "CONSISTENCY":
            decision = self.decisions.get(field(user, "DECISION"))
            line = self.lines.get(field(user, "ARTIFACT"))
            stale = decision is not None and line is not None and decision[0] == line[0] and decision[1] != line[1]
            return json.dumps({"label": "INCONSISTENT" if stale else "CONSISTENT", "evidence": "sim",
                               "expected_action": "check"})
        return self.mock.complete(system, user, temperature, json_schema)


def run(source: Path, key: dict, profile: str = "balanced", echo=print) -> list[dict]:
    """Run the steps on a copy of source. Returns one result per step."""
    work = Path(tempfile.mkdtemp(prefix="bench_demo_")) / source.name
    shutil.copytree(source, work)
    settings = AppSettings(allowed_folders=[str(work)], cost_profile=profile, max_llm_calls_per_cycle=100000)
    sim = DecisionAwareSim(key)
    bundle = ProviderBundle(sim, "sim", False, "simulated", True)
    db = StateDB(work.parent / "state.db")
    engine = ProjectEngine(work, settings, db, bundle, ProgressTracker())
    stale = {line["text"]: line for line in key["artifact_lines"] if line["expected"] == "INCONSISTENT"}
    files_only = {t for t, line in stale.items() if line["expected_files_only"] == "INCONSISTENT"}
    results: list[dict] = []

    def step(label: str, truth: set[str], action=None) -> None:
        before = dict(sim.by_task)
        if action:
            action()
        started = time.perf_counter()
        engine.sync()
        report = engine.analyze()
        gate = ""
        if report.pending_confirm:
            gate = f" GATE(est {report.pending_confirm['estimated_calls']})"
            report = engine.analyze(confirm=True)
        elapsed = time.perf_counter() - started
        delta = {k: v - before.get(k, 0) for k, v in sim.by_task.items() if v - before.get(k, 0)}
        found = {r["text"] for r in db.list_findings(include_hidden=True) if r["label"] == "INCONSISTENT"}
        built = engine.last_build
        result = {
            "step": label, "found": len(found & truth), "expected": len(truth), "false": sorted(found - set(stale)),
            "missed": sorted(truth - found), "lines": len(built.meetings) + len(built.artifacts) if built else 0,
            "calls": sum(delta.values()), "seconds": round(elapsed, 1), "gate": gate, "by_task": delta,
        }
        results.append(result)
        echo(f"{label:34s} found={result['found']}/{result['expected']} false={len(result['false'])} "
             f"lines={result['lines']} calls={result['calls']} time={elapsed:.1f}s{gate} {delta}")
        for text in result["false"]:
            echo(f"    false finding: {text}")
        for text in result["missed"]:
            echo(f"    missed: {stale[text]['file']}: {text}")

    step("1  files only", files_only)
    step("2  files and the 17 mail messages", set(stale),
         lambda: engine.set_external("gmail", mail_documents()))
    step("3  nothing changed", set(stale))
    return results


def main() -> None:
    key = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    run(Path(sys.argv[1]), key, sys.argv[3] if len(sys.argv) > 3 else "balanced")


if __name__ == "__main__":
    main()
