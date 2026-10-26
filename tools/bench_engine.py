"""Engine-level benchmark: counts model calls per task type with a simulated perfect model."""

from __future__ import annotations

import json
import os
import re
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

ROOT = Path(sys.argv[1])
KEY = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
MODE = sys.argv[3] if len(sys.argv) > 3 else "agent"

TEXT_TOPIC = {d["text"]: d["topic"] for d in KEY["decisions"]}
EXPECT = {}
for line in KEY["artifact_lines"]:
    EXPECT.setdefault(line["text"], line["expected"])


class Sim(LLMProvider):
    def __init__(self) -> None:
        self.mock = MockProvider()
        self.lock = threading.Lock()
        self.by_task: Counter = Counter()

    def complete(self, system, user, temperature=0.0, json_schema=None):
        task = re.search(r"TASK:([A-Z_]+)", system)
        name = task.group(1) if task else "OTHER"
        with self.lock:
            self.by_task[name] += 1
        latency = float(os.getenv("SIM_LATENCY", "0") or 0)
        if latency:
            time.sleep(latency)
        if is_batch(system):
            def single(sys_, usr, temp, schema):
                t = re.search(r"TASK:([A-Z_]+)", sys_).group(1)
                return self._answer(t, usr, sys_, temp, schema)
            return answer_batch(single, system, user, temperature)
        return self._answer(name, user, system, temperature, json_schema)

    def _answer(self, name, user, system, temperature, json_schema):
        scope = {"scope_kind": "GLOBAL", "release": None, "module": None, "user_group": None, "environment": None, "unmapped_terms": []}
        if name == "DECISION_EXTRACT":
            text = user.split("TEXT=", 1)[1].strip()
            topic = TEXT_TOPIC.get(text)
            if topic is None:
                return json.dumps({"is_decision": False, "text": text, "topic": "misc", "scope": scope})
            return json.dumps({"is_decision": True, "text": text, "topic": topic, "scope": scope})
        if name == "RELATION_CLASSIFY":
            old = re.search(r"OLD=(.*)", user).group(1).strip()
            new = re.search(r"NEW=(.*)", user).group(1).strip()
            same = TEXT_TOPIC.get(old) and TEXT_TOPIC.get(old) == TEXT_TOPIC.get(new)
            return json.dumps({"relation_type": "SUPERSEDE" if same else "NONE", "affected_scope": scope, "evidence": "sim"})
        if name == "IMPACT_SELECT":
            ids = re.findall(r"^ID=(\S+) KIND=", user, re.MULTILINE)
            return json.dumps({"artifact_ids": ids})
        if name == "CONSISTENCY":
            text = re.search(r"^ARTIFACT=(.*)$", user, re.MULTILINE).group(1).strip()
            expect = EXPECT.get(text, "CONSISTENT")
            label = {"HISTORICAL": "UNCERTAIN"}.get(expect, expect)
            return json.dumps({"label": label, "evidence": "sim", "expected_action": "check"})
        return self.mock.complete(system, user, temperature, json_schema)


def main() -> None:
    settings = AppSettings(allowed_folders=[str(ROOT)], analysis_mode=MODE, max_llm_calls_per_cycle=100000)
    extra = {}
    for item in sys.argv[4:]:
        k, _, v = item.partition("=")
        extra[k] = json.loads(v)
    if extra:
        settings = settings.model_copy(update=extra)
    sim = Sim()
    bundle = ProviderBundle(sim, "sim", False, "simulated", True)
    with tempfile.TemporaryDirectory() as tmp:
        db = StateDB(Path(tmp) / "state.db")
        engine = ProjectEngine(ROOT, settings, db, bundle, ProgressTracker())
        t0 = time.perf_counter()
        engine.sync()
        report = engine.analyze()
        elapsed = time.perf_counter() - t0
        if report.pending_confirm:
            print("GATE:", report.pending_confirm, "status phase:", engine.progress.phase)
            before = sum(sim.by_task.values())
            report = engine.analyze(confirm=True)
            print(f"after confirm: judge-stage actual calls={sum(sim.by_task.values()) - before} (estimated {report.pending_confirm or 'n/a'})")
        rows = db.list_findings(include_hidden=True)
        found = {(r["source_doc"], r["text"]) for r in rows if r["label"] == "INCONSISTENT"}
        truth = {(l["file"], l["text"]) for l in KEY["artifact_lines"] if l["expected"] == "INCONSISTENT"}
        hit = len(found & truth)
        false_pos = len(found - truth)
        local = [r for r in rows if "AI 확인 없이" in (r["evidence"] or "")]
        wrong = [r for r in local if (r["source_doc"], r["text"]) in truth]
        comp = engine.completion() or {}
        print(f"local_answers={comp.get('local_answers')} local_findings={len(local)} local_but_truth_inconsistent={len(wrong)}")
        for r in wrong[:5]:
            print("  WRONG:", r["topic"], r["text"])
        total_calls = sum(sim.by_task.values())
        print(f"mode={MODE} extra={extra} elapsed={elapsed:.1f}s counted_calls={report.llm_calls} sim_calls={total_calls}")
        print("by_task=", dict(sim.by_task))
        print(f"INCONSISTENT truth={len(truth)} detected={hit} false_positive={false_pos} topics={len(report.topics_checked)}")
        # second scan, nothing changed
        before = total_calls
        engine.analyze()
        print("second scan extra calls:", sum(sim.by_task.values()) - before)
        # Windows cannot remove the temporary folder while the database file is still open.
        db.close()


main()
