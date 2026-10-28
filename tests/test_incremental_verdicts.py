"""Line verdict store: a small change re-judges only what changed, results stay identical."""

from __future__ import annotations

import re
import threading

import desktop_helpers as dh

from decision_agent.agent.pipeline import run_pipeline
from decision_agent.config import DEFAULT_SETTINGS, Settings, settings_for_profile
from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.providers import ProviderBundle, task_of
from decision_agent.desktop.state import StateDB
from decision_agent.desktop.verdicts import TopicVerdicts, sampling_key
from decision_agent.llm.mock_provider import MockProvider
from decision_agent.models import ArtifactKind, ConsistencyLabel, ConsistencyResult
from decision_agent.workflow import ingest_project

ARTIFACT_RE = re.compile(r"ARTIFACT=([^|\n]*)")


class Recording(MockProvider):
    """The deterministic mock judge, recording every prompt it answers."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def complete(self, system, user, temperature=0.0, json_schema=None):
        with self._lock:
            self.prompts.append((task_of(system), user))
        return super().complete(system, user, temperature, json_schema)

    def judged_texts(self) -> list[str]:
        texts = []
        for task, user in self.prompts:
            if task.startswith("CONSISTENCY"):
                texts.extend(t.strip() for t in ARTIFACT_RE.findall(user))
        return texts


def bundle(provider, provider_id: str = "anthropic:big", local_only: bool = False, external: bool = False):
    return ProviderBundle(provider, provider_id, local_only, "test", external)


def engine_for(root, db, the_bundle, **overrides) -> ProjectEngine:
    return ProjectEngine(root, dh.make_settings(root, **overrides), db, the_bundle)


def consistency_calls(report) -> int:
    return sum(n for task, n in report.calls_by_task.items() if task.startswith("CONSISTENCY"))


def findings(db: StateDB) -> list[tuple]:
    return sorted(
        (r["topic"], r["artifact_id"], r["label"], r["evidence"], r["action"], r["decision_id"], r["confidence"])
        for r in db.list_findings(include_hidden=True)
    )


def test_second_identical_run_makes_no_consistency_calls_and_findings_are_identical(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    db = StateDB(":memory:")
    engine = engine_for(root, db, bundle(Recording()))
    first = engine.process()
    assert consistency_calls(first) > 0 and first.judged_lines > 0
    fresh = findings(db)
    again = engine.analyze(force=True)
    assert sorted(again.topics_checked) == sorted(first.topics_checked)
    assert consistency_calls(again) == 0 and again.llm_calls == 0
    assert again.judged_lines == 0 and again.reused_lines == first.judged_lines
    assert findings(db) == fresh
    # A separate fresh run on a new database produces the same findings as the cached run.
    other = StateDB(":memory:")
    engine_for(root, other, bundle(Recording())).process()
    assert findings(other) == fresh


def test_changing_one_line_rejudges_only_that_line(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    db = StateDB(":memory:")
    provider = Recording()
    engine = engine_for(root, db, bundle(provider))
    engine.process()
    tasks = root / "tasks.csv"
    tasks.write_text(
        tasks.read_text(encoding="utf-8").replace("Vue 대시보드 화면 개발", "Vue 대시보드 화면 개발 착수"),
        encoding="utf-8",
    )
    provider.prompts.clear()
    report = engine.process([tasks])
    judged = provider.judged_texts()
    assert judged and all("착수" in text for text in judged)
    assert report.judged_lines == len(set(judged)) >= 1
    assert report.reused_lines > 0


def test_changed_decision_text_rejudges_the_topic(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    db = StateDB(":memory:")
    provider = Recording()
    engine = engine_for(root, db, bundle(provider))
    engine.process()
    meeting = root / "meetings" / "meeting.md"
    meeting.write_text(
        meeting.read_text(encoding="utf-8").replace("- Vue로 변경한다.", "- Svelte로 변경한다."),
        encoding="utf-8",
    )
    provider.prompts.clear()
    report = engine.process([meeting])
    assert "frontend_framework" in report.topics_checked
    judged = set(provider.judged_texts())
    # The lines of the changed topic are judged again; lines of other topics are not.
    assert "React 로그인 화면 개발" in judged
    assert not any("이메일" in text for text in judged)
    assert report.judged_lines >= 1


def test_demo_verdicts_are_never_reused_as_model_verdicts(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    db = StateDB(":memory:")
    engine_for(root, db, bundle(MockProvider(), "mock", local_only=True)).process()
    # Same id, but now presented as a real model: the demo rows must not be used.
    real = engine_for(root, db, bundle(Recording(), "mock", local_only=False))
    report = real.analyze(force=True)
    assert consistency_calls(report) > 0 and report.reused_lines == 0


def test_cheap_model_change_keeps_the_judging_cache_and_main_change_rejudges(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    db = StateDB(":memory:")
    engine_for(root, db, bundle(Recording(), "anthropic:big")).process()
    cheap = engine_for(root, db, bundle(Recording(), "anthropic:big+small")).process()
    assert cheap.llm_calls == 0 and cheap.topics_checked == []
    assert bundle(None, "anthropic:big+small").cheap_model_id == "anthropic:small"
    assert bundle(None, "anthropic:big+small").judge_model_id == "anthropic:big"
    main = engine_for(root, db, bundle(Recording(), "anthropic:other")).process()
    assert consistency_calls(main) > 0 and main.topics_checked


def test_profile_switch_and_back_reuses_the_first_profile_verdicts(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    db = StateDB(":memory:")
    first = engine_for(root, db, bundle(Recording()), cost_profile="balanced").process()
    precise = engine_for(root, db, bundle(Recording()), cost_profile="precise").process()
    assert consistency_calls(precise) > 0
    back = engine_for(root, db, bundle(Recording()), cost_profile="balanced").process()
    assert back.topics_checked and consistency_calls(back) == 0
    assert back.reused_lines == first.judged_lines


def test_estimate_ignores_stored_lines_and_a_small_change_skips_the_gate(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    db = StateDB(":memory:")
    engine = engine_for(root, db, bundle(Recording(), external=True), confirm_over_calls=5)
    held = engine.process()
    assert held.pending_confirm is not None
    assert engine.analyze(confirm=True).pending_confirm is None
    tasks = root / "tasks.csv"
    tasks.write_text(
        tasks.read_text(encoding="utf-8").replace("Vue 대시보드 화면 개발", "Vue 대시보드 화면 개발 착수"),
        encoding="utf-8",
    )
    report = engine.process([tasks])
    assert report.pending_confirm is None and report.judged_lines >= 1


def test_missing_lines_counts_only_lines_without_a_stored_verdict(demo):
    _, project = demo
    db = StateDB(":memory:")
    store = TopicVerdicts(db, "fp", "m", "c", sampling_key(DEFAULT_SETTINGS), "model", "2026-10-01T00:00:00")
    lines = project.artifacts[:3]
    assert store.missing_lines(lines) == 3
    from decision_agent.consistency import PreparedCheck

    first = PreparedCheck(lines[0], user="prompt")
    result = ConsistencyResult(
        artifact_id=lines[0].id, label=ConsistencyLabel.CONSISTENT, confidence=1.0, evidence="e",
        expected_action="a",
    )
    store.put_verdicts([(first, result)])
    assert store.missing_lines(lines) == 2
    # Another origin does not count as stored.
    demo_store = TopicVerdicts(db, "fp", "m", "c", sampling_key(DEFAULT_SETTINGS), "demo", "2026-10-01T00:00:00")
    assert demo_store.missing_lines(lines) == 3
    assert demo_store.get_verdicts([first]) == [None]
    assert store.get_verdicts([first])[0].label == ConsistencyLabel.CONSISTENT


def test_core_pipeline_without_a_store_is_unchanged():
    project = ingest_project(dh.DEMO_DIR, MockProvider())
    plain = run_pipeline(project.store, MockProvider(), "frontend_framework", project.artifacts, "9999-12-31")
    again = ingest_project(dh.DEMO_DIR, MockProvider())
    db = StateDB(":memory:")
    store = TopicVerdicts(db, "fp", "m", "m", sampling_key(DEFAULT_SETTINGS), "model", "2026-10-01T00:00:00")
    stored = run_pipeline(
        again.store, MockProvider(), "frontend_framework", again.artifacts, "9999-12-31",
        verdict_store=store,
    )
    assert [r.model_dump() for r in plain.final_results] == [r.model_dump() for r in stored.final_results]
    assert plain.reused_lines == 0 and plain.judged_lines == 0
    assert stored.judged_lines == len(stored.final_results)
    # The research defaults stay as they were.
    assert Settings().k == 8 and Settings().n == 5 and Settings().tau_conf == 0.8
    assert Settings().routing == "llm" and Settings().prefilter is False
    assert settings_for_profile("precise").routing == "llm"


def test_pruning_respects_age_limit_and_rows_of_the_current_cycle():
    db = StateDB(":memory:")

    def rows(prefix: str, count: int) -> list[dict]:
        return [
            {
                "key": f"{prefix}{i}", "line_key": f"L{prefix}{i}", "label": "CONSISTENT",
                "evidence": "", "expected_action": "", "certainty": 1.0, "decision_id": None,
                "origin": "model",
            }
            for i in range(count)
        ]

    db.verdicts_put(rows("old", 3), "2026-01-01T00:00:00")
    db.verdicts_put(rows("mid", 4), "2026-09-20T00:00:00")
    db.selections_put({"sel-old": True}, "2026-01-01T00:00:00")
    stamp = "2026-10-01T00:00:00"
    db.verdicts_put(rows("now", 6), stamp)
    removed = db.prune_verdicts(stamp, max_age_days=60, limit=5)
    remaining = {r["key"] for r in db.conn.execute("SELECT key FROM line_verdicts")}
    # Too old rows go; the limit then removes older rows, but never the current cycle's rows,
    # even though they alone exceed the limit.
    assert remaining == {f"now{i}" for i in range(6)}
    assert removed == 3 + 4 + 1
    assert db.conn.execute("SELECT COUNT(*) FROM impact_selections").fetchone()[0] == 0
    # Reading a row marks it as used by the cycle that read it.
    db.verdicts_get(["now0"], "2026-12-30T00:00:00")
    db.prune_verdicts("2026-12-30T00:00:00", max_age_days=60, limit=100)
    assert {r["key"] for r in db.conn.execute("SELECT key FROM line_verdicts")} == {"now0"}


def test_artifact_kind_is_part_of_the_line_key(demo):
    _, project = demo
    store = TopicVerdicts(StateDB(":memory:"), "fp", "m", "c", "s", "model", "2026-10-01T00:00:00")
    line = project.artifacts[0]
    other = line.model_copy(update={"kind": ArtifactKind.TEST if line.kind != ArtifactKind.TEST else ArtifactKind.TASK})
    assert store.line_key(line) != store.line_key(other)


def test_completion_record_and_home_line_show_reuse(tmp_path):
    from decision_agent.desktop.webpage import all_sources

    root = dh.make_watch_dir(tmp_path)
    db = StateDB(":memory:")
    engine = engine_for(root, db, bundle(Recording()))
    first = engine.process()
    record = engine.completion()
    assert record["judged_lines"] == first.judged_lines > 0
    assert record["reused_lines"] == first.reused_lines
    engine.analyze(force=True)
    assert engine.completion()["judged_lines"] == 0 and engine.completion()["reused_lines"] > 0
    app_js = all_sources()["app.js"]
    for text in ("새로 판단한 줄 ", "이전 결과 재사용 ", ", 모델 호출 "):
        assert text in app_js, text


def test_cheap_model_change_refreshes_the_completion_record(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    db = StateDB(":memory:")
    engine_for(root, db, bundle(Recording(), "anthropic:big")).process()
    cheap = engine_for(root, db, bundle(Recording(), "anthropic:big+small"))
    report = cheap.process()
    assert report.llm_calls == 0
    record = cheap.completion()
    # The status compares this record with the current provider; it must not stay stale.
    assert record["provider_id"] == "anthropic:big+small" and record["verified"]
    assert record["judged_lines"] == 0 and record["reused_lines"] > 0
