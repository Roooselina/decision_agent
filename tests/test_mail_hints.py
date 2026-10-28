"""Deadline hints and related mail: local only, deterministic, never part of the analysis."""

from __future__ import annotations

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient
from test_desktop_features import make_app

from decision_agent.connectors.base import MailMessage
from decision_agent.desktop import mailsources
from decision_agent.desktop.maildb import MailDB, mail_uid
from decision_agent.desktop.mailhints import extract_hints
from decision_agent.desktop.mailview import MIN_RELATED, day_summary, related_mail
from decision_agent.desktop.settings import AppSettings, MailSource
from decision_agent.desktop.webapp import create_web_app

TOKEN = "t" * 32


def dates(subject: str, body: str, mail_date: str) -> list[tuple[str, str]]:
    return [(h["date"], h["kind"]) for h in extract_hints(subject, body, mail_date)]


def test_day_only_dates_resolve_against_the_mail_date():
    assert dates("", "보고서는 27일까지 제출해 주세요.", "2026-10-20") == [("2026-10-27", "deadline")]
    # After the 27th of the mail's month, the nearest following 27th is next month.
    assert dates("", "보고서는 27일까지 제출해 주세요.", "2026-10-28") == [("2026-11-27", "deadline")]
    assert dates("", "신청은 ~15일 까지", "2026-12-20") == [("2027-01-15", "deadline")]


def test_month_day_and_other_forms_parse():
    assert dates("", "10월 31일 마감입니다", "2026-10-20") == [("2026-10-31", "deadline")]
    assert dates("", "1월 5일까지 마감", "2026-12-20") == [("2027-01-05", "deadline")]
    assert dates("제3차 과학기술 60년 미래포럼 행사 안내(26.11.4. 서울 글래드 여의도)", "", "2026-10-20") == [("2026-11-04", "date")]
    assert dates("", "draft due 11/3. kickoff 2026-12-01", "2026-10-20") == [("2026-11-03", "deadline"), ("2026-12-01", "date")]
    hint = extract_hints("", "회의는 끝났습니다. 보고서는 27일까지 제출. 감사합니다!", "2026-10-20")[0]
    assert hint["sentence"] == "보고서는 27일까지 제출." and hint["phrase"] == "27일까지"


def test_a_sentence_without_a_date_gives_no_hint():
    assert extract_hints("", "마감을 꼭 지켜 주세요. 오늘 회의 잘 마쳤습니다.", "2026-10-20") == []
    assert extract_hints("", "버전은 v1.2.3 입니다", "2026-10-20") == []
    assert extract_hints("", "27일까지", "not a date") == []


def indexed() -> MailDB:
    db = MailDB()
    db.upsert("s", [
        MailMessage("old", "2026-10-02", "중간 보고서 제출 안내", "김 <kim@team.kr>", "중간 보고서는 27일까지 마감입니다. 양식은 첨부를 보세요."),
        MailMessage("mid", "2026-10-15", "보고서 양식 변경", "이 <lee@team.kr>", "보고서 양식을 새 양식으로 바꾸기로 했습니다."),
        MailMessage("lunch", "2026-10-16", "점심", "박 <park@team.kr>", "오늘 점심 같이 드실 분 계신가요"),
        MailMessage("twin1", "2026-10-10", "서버 점검 공지", "ops@x.kr", "서버 점검이 있습니다"),
        MailMessage("twin2", "2026-10-11", "서버 점검 공지", "ops@x.kr", "서버 점검이 있습니다"),
    ])
    return db


def test_related_mail_finds_the_earlier_mail_and_is_deterministic():
    db = indexed()
    found = related_mail(db, "중간 보고서 27일까지 마감")
    assert found[0]["id"] == mail_uid("s", "old")
    assert "27일까지" in found[0]["snippet"] and "body" not in found[0]
    assert all(row["score"] >= MIN_RELATED for row in found)
    assert mail_uid("s", "lunch") not in {row["id"] for row in found}
    assert related_mail(db, "중간 보고서 27일까지 마감") == found
    # Equal scores: the newest mail comes first.
    twins = related_mail(db, "서버 점검 공지", limit=2)
    assert [t["id"] for t in twins] == [mail_uid("s", "twin2"), mail_uid("s", "twin1")]
    assert related_mail(db, "전혀 관계없는 영어 문장 xyz qq") == []


def test_related_timeline_is_in_date_order_and_excludes_the_mail_itself():
    db = indexed()
    timeline = related_mail(db, "보고서 양식 제출", limit=10, timeline=True)
    assert [t["date"] for t in timeline] == sorted(t["date"] for t in timeline)
    assert mail_uid("s", "mid") not in {t["id"] for t in related_mail(db, "보고서 양식 변경", exclude=mail_uid("s", "mid"))}


def test_hints_are_stored_cleared_and_shown_per_day():
    db = indexed()
    old = mail_uid("s", "old")
    assert db.hints(old)[0]["date"] == "2026-10-27" and old in db.deadline_uids()
    summary = day_summary(db, "2026-10-26", "2026-11-01")
    assert summary["2026-10-27"]["deadlines"][0]["id"] == old and summary["2026-10-27"]["count"] == 0
    db.clear_source("s")
    assert db.hints(old) == [] and db.deadline_uids() == set()


def test_hints_never_change_documents_or_fingerprints(tmp_path):
    db = indexed()
    source = MailSource(id="s", name="보고", connector="gmail", labels=["보고"])
    settings = AppSettings(mail_prefilter=False)
    with_hints = mailsources.build_documents(source, db.rows("s"), settings, db, {})
    with db.lock:
        db.conn.execute("DELETE FROM mail_hints")
    without = mailsources.build_documents(source, db.rows("s"), settings, db, {})
    assert {k: v.model_dump_json() for k, v in with_hints.items()} == {k: v.model_dump_json() for k, v in without.items()}
    assert not any("hint" in json for json in (d.model_dump_json() for d in with_hints.values()))
    controller = make_app(dh.make_watch_dir(tmp_path))
    controller.start()
    try:
        assert controller.wait_idle(60)
        runtime = next(iter(controller.runtimes.values()))
        before = runtime.db.meta_get("input_fp")
        controller.maildb.upsert("x", [MailMessage("m", "2026-10-02", "제출", "a@b.kr", "27일까지 마감")])
        assert controller.maildb.deadline_uids()
        report = runtime.engine.analyze()
        assert runtime.db.meta_get("input_fp") == before and report.llm_calls == 0
    finally:
        controller.stop()


@pytest.fixture
def client(tmp_path):
    controller = make_app(dh.make_watch_dir(tmp_path))
    controller.start()
    assert controller.wait_idle(60)
    controller.maildb.upsert("s", [MailMessage("old", "2026-10-02", "중간 보고서 제출 안내", "kim@team.kr",
                                               "중간 보고서는 27일까지 마감입니다. " + "가" * 300 + " 끝 문장")])
    yield controller, TestClient(create_web_app(controller, TOKEN, 8765), base_url="http://127.0.0.1:8765")
    controller.stop()


def test_related_endpoint_needs_the_token_and_returns_snippets_only(client):
    controller, http = client
    runtime = next(iter(controller.runtimes.values()))
    assert http.post("/api/related", json={"text": "보고서"}).status_code == 403
    assert http.post("/api/related", headers={"X-DA-Token": TOKEN}, json={"text": ""}).status_code == 422
    rows = http.post("/api/related", headers={"X-DA-Token": TOKEN}, json={"text": "중간 보고서 마감", "limit": 5}).json()
    assert rows and "끝 문장" not in str(rows) and set(rows[0]) == {"id", "date", "subject", "sender", "snippet", "score"}
    # Nothing here reaches a model: the engine counter of the next cycle stays at zero.
    assert runtime.engine.analyze().llm_calls == 0
    mail = http.get(f"/api/mail/{rows[0]['id']}", headers={"X-DA-Token": TOKEN}).json()
    assert mail["hints"][0]["date"] == "2026-10-27" and mail["deadline"] is True


def test_screens_show_hints_and_related_mail_as_text():
    import re

    from decision_agent.desktop.webpage import all_sources

    app_js = all_sources()["app.js"]
    for text in ("메일 속 날짜", "비슷한 메일", "관련 메일", "과거 메일에서 같은 내용", "이 결정과 관련된 메일 흐름",
                 "이 날 마감으로 보이는 메일", "/api/related", "열기"):
        assert text in app_js, text
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", app_js)
