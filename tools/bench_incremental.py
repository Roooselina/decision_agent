"""Incremental cost benchmark: how many model calls does a small change cost?

Usage:  python tools/bench_incremental.py <demo_folder> <answer_key.json> [profile]

It copies the demo folder to a temporary directory first, so the original folder is never touched.
A simulated perfect model counts real calls per task (cache hits are not counted). The default
confirm gate stays on; a step that hit it is marked GATE and then confirmed. The run does
one full scan, then applies one small change at a time and prints the new calls and the number of
topics that were checked again.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path

from decision_agent.connectors.base import message_document
from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.progress import ProgressTracker
from decision_agent.desktop.providers import ProviderBundle
from decision_agent.desktop.settings import AppSettings
from decision_agent.desktop.state import StateDB
from decision_agent.llm.base import LLMProvider
from decision_agent.llm.batch import answer_batch, is_batch
from decision_agent.llm.mock_provider import MockProvider

SCOPE = {
    "scope_kind": "GLOBAL", "release": None, "module": None, "user_group": None,
    "environment": None, "unmapped_terms": [],
}


class Sim(LLMProvider):
    """Perfect model for the demo key. Counts calls per task type."""

    def __init__(self, key: dict) -> None:
        self.mock = MockProvider()
        self.lock = threading.Lock()
        self.by_task: Counter = Counter()
        self.text_topic = {d["text"]: d["topic"] for d in key["decisions"]}
        self.expect: dict[str, str] = {}
        for line in key["artifact_lines"]:
            self.expect.setdefault(line["text"], line["expected"])

    def complete(self, system, user, temperature=0.0, json_schema=None):
        match = re.search(r"TASK:([A-Z_]+)", system)
        name = match.group(1) if match else "OTHER"
        with self.lock:
            self.by_task[name] += 1
        if is_batch(system):
            def single(sys_, usr, temp, schema):
                task = re.search(r"TASK:([A-Z_]+)", sys_).group(1)
                return self._answer(task, usr, sys_, temp, schema)
            return answer_batch(single, system, user, temperature)
        return self._answer(name, user, system, temperature, json_schema)

    def _answer(self, name, user, system, temperature, json_schema):
        if name == "DECISION_EXTRACT":
            text = user.split("TEXT=", 1)[1].strip()
            topic = self.text_topic.get(text)
            if topic is None:
                return json.dumps({"is_decision": False, "text": text, "topic": "misc", "scope": SCOPE})
            return json.dumps({"is_decision": True, "text": text, "topic": topic, "scope": SCOPE})
        if name == "RELATION_CLASSIFY":
            old = re.search(r"OLD=(.*)", user).group(1).strip()
            new = re.search(r"NEW=(.*)", user).group(1).strip()
            same = self.text_topic.get(old) and self.text_topic.get(old) == self.text_topic.get(new)
            return json.dumps({"relation_type": "SUPERSEDE" if same else "NONE", "affected_scope": SCOPE, "evidence": "sim"})
        if name == "IMPACT_SELECT":
            return json.dumps({"artifact_ids": re.findall(r"^ID=(\S+) KIND=", user, re.MULTILINE)})
        if name == "CONSISTENCY":
            text = re.search(r"^ARTIFACT=(.*)$", user, re.MULTILINE).group(1).strip()
            expect = self.expect.get(text, "CONSISTENT")
            label = {"HISTORICAL": "UNCERTAIN"}.get(expect, expect)
            return json.dumps({"label": label, "evidence": "sim", "expected_action": "check"})
        return self.mock.complete(system, user, temperature, json_schema)


def main() -> None:
    source = Path(sys.argv[1])
    key = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    profile = sys.argv[3] if len(sys.argv) > 3 else "balanced"
    work = Path(tempfile.mkdtemp(prefix="bench_inc_")) / "project"
    shutil.copytree(source, work)
    settings = AppSettings(
        allowed_folders=[str(work)], cost_profile=profile,
        max_llm_calls_per_cycle=100000,
    )
    sim = Sim(key)
    truth = {(line["file"], line["text"]) for line in key["artifact_lines"] if line["expected"] == "INCONSISTENT"}
    bundle = ProviderBundle(sim, "sim", False, "simulated", True)
    db = StateDB(work.parent / "state.db")
    engine = ProjectEngine(work, settings, db, bundle, ProgressTracker())
    state = {"engine": engine}

    def step(label: str, action=None, new_engine: ProjectEngine | None = None) -> None:
        if new_engine is not None:
            state["engine"] = new_engine
        current = state["engine"]
        before = dict(sim.by_task)
        if action:
            action()
        current.sync()
        report = current.analyze()
        gate = ""
        if report.pending_confirm:
            # The default confirm gate held the check back; note it and confirm, as a user would.
            gate = f" GATE(est {report.pending_confirm['estimated_calls']})"
            report = current.analyze(confirm=True)
        delta = {k: v - before.get(k, 0) for k, v in sim.by_task.items() if v - before.get(k, 0)}
        found = {
            (r["source_doc"], r["text"]) for r in db.list_findings(include_hidden=True)
            if r["label"] == "INCONSISTENT"
        }
        print(
            f"{label:62s} calls={sum(delta.values()):4d} topics={len(report.topics_checked):2d} "
            f"detected={len(found & truth)}/{len(truth)}{gate} {delta}"
        )

    def write(rel: str, text: str) -> None:
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    step("0  full first scan")
    step("0b nothing changed")
    step("1  add an unrelated note (sorts last)", lambda: write("zz_note.md", "# memo\n\n- server logs are kept 30 days\n"))
    step("3  add a requirement file with one line about an old value",
         lambda: write("requirements/requirements_new.md", "# 요구사항\n\n- 교재 사진은 최대 3장까지 업로드한다\n"))
    text = "CI는 CircleCI로 바꾸기로 한다"
    sim.text_topic[text] = "ci"
    step("4  add a meeting with one new decision on an existing topic",
         lambda: write("meetings/2026-10-26_meeting_new.md", f"## 2026-10-26 회의\n\n### 결정 사항\n- {text}\n"))
    # demo_big has requirements_auth.md; other demos append to their first requirement file.
    target = "requirements/requirements_auth.md"
    if not (work / target).is_file():
        target = min(p.relative_to(work).as_posix() for p in (work / "requirements").glob("*.md"))
    step("5  append one line to an existing requirement file",
         lambda: write(target, (work / target).read_text(encoding="utf-8") + "- 로그인 실패는 5회까지 허용한다\n"))
    step("6  one mail delivered by a connector (set_external)",
         lambda: engine.set_external("gmail", {"mail": message_document(
             "회의 결과", "2026-10-27", "a@b.c", ["오늘 회의에서 CI는 CircleCI로 바꾸기로 한다"])}))
    other = ProjectEngine(work, settings, db, ProviderBundle(sim, "sim+small", False, "simulated", True), ProgressTracker())
    step("7  only the cheap model setting changed (provider id changes)", new_engine=other)
    precise = settings.model_copy(update={"cost_profile": "precise" if profile != "precise" else "balanced"})
    step(f"8  profile {profile} -> {precise.cost_profile}",
         new_engine=ProjectEngine(work, precise, db, bundle, ProgressTracker()))
    step(f"9  profile back to {profile} (should reuse earlier results)",
         new_engine=ProjectEngine(work, settings, db, bundle, ProgressTracker()))


main()
