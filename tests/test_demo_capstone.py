"""The BookSwap capstone demo: authored content, clean parsing, exact answer key, honest bench."""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

from decision_agent.connectors.base import clean_message_text
from decision_agent.desktop.mailhints import extract_hints

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import bench_demo
import make_demo_capstone as demo

UNDATED = "meetings/회의메모_날짜없음.md"
MEETING_FOLDERS = ("meetings/", "standups/", "status/", "ideas/")
try:
    import playwright.sync_api  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


@pytest.fixture(scope="module")
def folders(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    made = {}
    for variant, (name, _) in demo.VARIANTS.items():
        root = out / name
        # The PDF counts: the candidate list grows with the number of artifacts, and the
        # bench below must run on the folder exactly as users get it.
        demo.write_folder(variant, root, pdf=HAS_PLAYWRIGHT)
        made[variant] = root
    return made


def source_lines(value) -> set[str]:
    if isinstance(value, list):
        text = "\n".join(f"{e['summary']}\n{e['description']}\n{e['location']}" for e in value)
    elif isinstance(value, dict):
        text = f"{value['subject']}\n{value['body']}"
    else:
        text = value
    return {line.strip() for line in text.splitlines() if line.strip()}


@pytest.mark.parametrize("variant", ["capstone", "full"])
def test_folders_parse_without_warnings_except_the_undated_memo(folders, variant):
    built = demo.build_folder(folders[variant])
    assert built.unclassified == []
    warned = {s.path: s.warnings for s in built.summaries if s.warnings}
    assert list(warned) == [UNDATED]
    assert warned[UNDATED][0].startswith("날짜를 알 수 없어 건너뛴")


def test_file_counts_match_the_spec(folders):
    docs, _ = demo.content("capstone")
    full, _ = demo.content("full")
    assert 38 <= len(docs) <= 45 and 120 <= len(full) <= 150
    assert len([p for p in full if p.startswith("standups/")]) == 25
    assert len([p for p in full if p.endswith(".eml")]) == 15
    assert {Path(p).suffix for p in docs} >= {".md", ".txt", ".docx", ".xlsx", ".csv", ".pdf", ".pptx", ".ics"}


def test_every_meeting_type_file_has_at_least_fifteen_lines():
    docs, _ = demo.content("full")
    short = {p: len(source_lines(v)) for p, v in docs.items()
             if p.startswith(MEETING_FOLDERS) and len(source_lines(v)) < 15}
    assert short == {}


def test_no_two_files_share_more_than_forty_percent_of_their_lines():
    docs, _ = demo.content("full")
    lines = {p: source_lines(v) for p, v in docs.items()}
    worst = []
    for a, b in itertools.combinations(lines, 2):
        shared = len(lines[a] & lines[b]) / min(len(lines[a]), len(lines[b]))
        if shared > 0.4:
            worst.append((a, b, round(shared, 2)))
    assert worst == []


@pytest.mark.parametrize("variant", ["capstone", "full"])
def test_answer_key_has_exactly_ten_stale_lines(folders, variant):
    key = demo.answer_key(folders[variant])
    stale = [line for line in key["artifact_lines"] if line["expected"] == "INCONSISTENT"]
    assert sorted(line["item"] for line in stale) == list(range(1, 11))
    assert sorted(line["item"] for line in stale if line["expected_files_only"] == "INCONSISTENT") == list(range(1, 7))
    assert [d["mail"] for d in key["decisions"] if d.get("mail")] == ["T1/3", "T2/4"]
    # The reverted photo decision: the latest value is 5 again, so only the "3" line is stale.
    photos = next(t for t in key["topics"] if t["key"] == "photos")
    assert photos["values"] == ["5장", "3장", "5장"]


def test_mail_threads_keep_the_required_sentences_and_drop_quoted_text():
    data = demo.threads()
    assert len(data["threads"]) == 5 and sum(len(t["messages"]) for t in data["threads"]) == 17
    bodies = {t["id"]: [m["body"] for m in t["messages"]] for t in data["threads"]}
    assert "최종 발표는 11월 17일 오후 2시, 리허설은 11월 16일로 확정합니다. 중간 점검은 10월 29일 그대로입니다." in bodies["T1"][2]
    assert "그럼 배포는 Render 유료 플랜으로 하자. Lightsail은 이번 주 안에 정리할게." in bodies["T2"][3]
    assert "금요일 회의에서 정하자" in bodies["T4"][2]
    assert data["threads"][0]["subject"] == "최종 발표 일정 확정 부탁드립니다"
    assert "토스페이먼츠" in bodies["T3"][1]
    assert not any("토스페이먼츠" in line for line in clean_message_text(bodies["T3"][1]))
    # Every mail in the files and the thread data uses example addresses only.
    assert all(p["example"].split("@")[1].startswith("example.") for p in data["people"].values())
    t5 = data["threads"][4]
    hints = {h["date"] for h in extract_hints(t5["subject"], t5["messages"][0]["body"], "2026-10-28")}
    assert {"2026-10-28", "2026-10-29"} <= hints


def test_mail_documents_carry_the_two_mail_decisions():
    texts = [b.text for doc in demo.mail_documents().values() for b in doc.sections[0].blocks]
    assert any("최종 발표는 11월 17일 오후 2시" in t for t in texts)
    assert any("배포는 Render 유료 플랜으로 하자" in t for t in texts)
    assert not any(t.startswith(">") for t in texts)


def test_calendar_import_file_is_built_from_one_base_date():
    events = {e["summary"]: e["start"] for e in demo.calendar_import_events()}
    assert events["중간 점검 자료 마감"].startswith("2026-10-28") and events["중간 점검"] == "2026-10-29T14:00"
    assert events["최종 발표"] == "2026-11-10T14:00" and events["리허설"].startswith("2026-11-09")
    assert events["교수님 면담"].startswith("2026-11-04") and events["주간 회의"] == "2026-10-30T18:00"
    text = demo.ics_text(demo.calendar_import_events(), "x")
    assert "RRULE:FREQ=WEEKLY;BYDAY=FR" in text and text.count("BEGIN:VEVENT") == 6


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="the PDF of the demo needs Playwright")
def test_simulated_bench_finds_six_from_files_and_ten_with_mail(folders):
    key = demo.answer_key(folders["capstone"])
    results = bench_demo.run(folders["capstone"], key, echo=lambda *_: None)
    files, mail, again = results
    assert (files["found"], files["expected"], files["false"]) == (6, 6, [])
    assert (mail["found"], mail["expected"], mail["false"]) == (10, 10, [])
    assert again["calls"] == 0


def test_the_key_stays_outside_the_watched_folder(tmp_path):
    pack = demo.write_pack("capstone", tmp_path, pdf=False)
    root = pack / pack.name
    assert (pack / f"{pack.name}_answer_key.json").is_file() and (pack / "google_calendar_import.ics").is_file()
    assert not any("answer_key" in p.name or p.name == "google_calendar_import.ics" for p in root.rglob("*"))
    assert json.loads((root / ".decision_agent.json").read_text(encoding="utf-8"))["current_release"] == "mvp"
