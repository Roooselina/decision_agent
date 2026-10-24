from __future__ import annotations

import hashlib
from pathlib import Path

import desktop_helpers as dh
import pytest

import decision_agent.desktop.engine as engine_module
from decision_agent.desktop.state import StateDB


def tree_hash(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_initial_analysis_reproduces_demo_scenarios(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, db, _ = dh.make_engine(root)
    report = engine.process()
    labels = dh.findings_by_text(db)
    assert labels["MVP 이메일 인증 개발 진행 중"] == "INCONSISTENT"
    assert labels["Production 이메일 인증 구현"] == "CONSISTENT"
    assert labels["Vue 대시보드 화면 개발"] == "INCONSISTENT"
    assert labels["발표 일정"] == "UNCERTAIN"
    assert [q["topic"] for q in db.list_questions()] == ["presentation_schedule"]
    assert report.rebuilt and sorted(report.topics_checked) == [
        "email_auth", "frontend_framework", "presentation_schedule",
    ]


def test_second_run_is_free(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, _, _ = dh.make_engine(root)
    engine.process()
    again = engine.process()
    assert not again.rebuilt and again.topics_checked == [] and again.llm_calls == 0


def test_irrelevant_new_artifact_rechecks_nothing(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, _db, _ = dh.make_engine(root)
    engine.process()
    with (root / "tasks.csv").open("a", encoding="utf-8") as f:
        f.write("TASK_09,Zebra qrs xyz,TODO,C,,,,\n")
    report = engine.process([root / "tasks.csv"])
    assert report.rebuilt and report.topics_checked == []


def test_only_the_changed_topic_is_rechecked(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, _db, _ = dh.make_engine(root)
    engine.process()
    with (root / "meetings" / "meeting.md").open("a", encoding="utf-8") as f:
        f.write("\n## 2026-09-25 회의\n- 발표를 취소한다.\n")
    report = engine.process([root / "meetings" / "meeting.md"])
    assert report.topics_checked == ["presentation_schedule"]


def test_new_relevant_task_is_flagged_once(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, _db, _ = dh.make_engine(root)
    engine.process()
    with (root / "tasks.csv").open("a", encoding="utf-8") as f:
        f.write("TASK_05,MVP 이메일 인증 UI 개발,TODO,C,mvp,login,,\n")
    report = engine.process([root / "tasks.csv"])
    new = [i for o in report.outcomes for i in o.new_inconsistent]
    assert [i["text"] for i in new] == ["MVP 이메일 인증 UI 개발"]
    assert engine.process([root / "tasks.csv"]).outcomes == []


def test_deleting_and_moving_files_updates_the_index(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, db, _ = dh.make_engine(root)
    engine.process()
    (root / "sub").mkdir()
    (root / "tasks.csv").rename(root / "sub" / "tasks.csv")
    report = engine.process([root / "tasks.csv", root / "sub"])
    assert "tasks.csv" in report.sync.removed
    paths = {r["path"] for r in db.list_files()}
    assert "sub/tasks.csv" in paths and "tasks.csv" not in paths
    (root / "sub" / "tasks.csv").unlink()
    (root / "sub").rmdir()
    engine.process([root / "sub"])
    assert not any(t.startswith("MVP 이메일 인증 개발") for t in dh.findings_by_text(db))


def test_full_scan_detects_deletions_and_edits_made_while_not_watching(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, _db, _ = dh.make_engine(root)
    engine.process()
    (root / "schedule.csv").unlink()
    with (root / "tasks.csv").open("a", encoding="utf-8") as f:
        f.write("TASK_06,MVP 이메일 인증 문서화,TODO,C,mvp,login,,\n")
    report = engine.process()
    assert "schedule.csv" in report.sync.removed and "tasks.csv" in report.sync.changed


def test_answering_resolves_the_pending_question(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, db, _ = dh.make_engine(root)
    engine.process()
    report = engine.answer("presentation_schedule", "발표 일정만 변경됩니다.")
    assert db.list_questions() == []
    labels = {r["label"] for r in db.list_findings() if r["topic"] == "presentation_schedule"}
    assert "UNCERTAIN" not in labels
    assert report.topics_checked == ["presentation_schedule"]
    assert "email_auth" not in report.topics_checked


def test_llm_budget_defers_and_next_cycle_continues(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    engine, db, settings = dh.make_engine(root, max_llm_calls_per_cycle=5)
    first = engine.process()
    assert first.error and "한도" in first.error and first.needs_followup
    settings.max_llm_calls_per_cycle = 100000
    engine.counter.budget = 100000
    second = engine.analyze()
    assert second.error is None
    assert dh.findings_by_text(db)["MVP 이메일 인증 개발 진행 중"] == "INCONSISTENT"


def test_locked_file_is_retried_not_indexed(tmp_path, monkeypatch):
    root = dh.make_watch_dir(tmp_path)
    engine, db, _ = dh.make_engine(root)

    def locked(path):
        raise PermissionError("locked")

    monkeypatch.setattr(engine_module, "sha256_file", locked)
    result = engine.sync()
    assert len(result.retry) == 5 and db.list_files() == []


def test_broken_file_is_recorded_and_not_reparsed(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    (root / "broken.docx").write_bytes(b"not a zip")
    (root / "old.hwp").write_bytes(b"x")
    engine, db, _ = dh.make_engine(root)
    report = engine.process()
    rows = {r["path"]: r for r in db.list_files()}
    assert rows["broken.docx"]["status"] == "error" and report.sync.errors
    assert rows["old.hwp"]["status"] == "unsupported" and "hwpx" in rows["old.hwp"]["message"]
    parsed_at = rows["broken.docx"]["parsed_at"]
    again = engine.sync()
    assert again.errors == [] and db.get_file("broken.docx")["parsed_at"] == parsed_at
    assert dh.findings_by_text(db)["MVP 이메일 인증 개발 진행 중"] == "INCONSISTENT"


def test_watched_folder_is_never_modified(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    before = tree_hash(root)
    engine, _db, _ = dh.make_engine(root)
    engine.process()
    engine.answer("presentation_schedule", "발표만 변경됩니다.")
    assert tree_hash(root) == before
    assert not Path(root, ".decision_agent").exists()


def test_profile_file_in_folder_changes_scope_vocabulary(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    (root / ".decision_agent.json").write_text(
        '{"current_release": "mvp", "scope_aliases": {"release": {"mvp": ["MVP", "알파"]}}}',
        encoding="utf-8",
    )
    engine, _db, _ = dh.make_engine(root)
    engine.process()
    assert any("scope" in n or ".decision_agent" in n for n in engine.profile_notes)


def test_state_database_lives_outside_the_watched_folder(tmp_path):
    from decision_agent.desktop.paths import state_db_path

    root = dh.make_watch_dir(tmp_path)
    assert not state_db_path(root).is_relative_to(root)
    StateDB(state_db_path(root)).close()


@pytest.mark.parametrize("mode", ["agent", "pipeline"])
def test_both_analysis_modes_find_the_scoped_inconsistency(tmp_path, mode):
    root = dh.make_watch_dir(tmp_path)
    engine, db, _ = dh.make_engine(root, analysis_mode=mode)
    engine.process()
    labels = dh.findings_by_text(db)
    assert labels["MVP 이메일 인증 개발 진행 중"] == "INCONSISTENT"
    assert labels["Production 이메일 인증 구현"] == "CONSISTENT"
