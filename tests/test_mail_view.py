"""The mail view API and screens: snippets only, filters, grouping, paging, auth."""

from __future__ import annotations

import datetime
import re

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient
from test_desktop_features import make_app

from decision_agent.connectors.base import MailMessage
from decision_agent.desktop.settings import MailSource
from decision_agent.desktop.webapp import create_web_app
from decision_agent.desktop.webpage import all_sources

TOKEN = "t" * 32
HEADERS = {"X-DA-Token": TOKEN}
TODAY = datetime.date.today()


def day(n: int) -> str:
    return (TODAY - datetime.timedelta(days=n)).isoformat()


@pytest.fixture
def viewer(tmp_path):
    controller = make_app(dh.make_watch_dir(tmp_path))
    controller.start()
    assert controller.wait_idle(60)
    controller.settings.mail_sources = [
        MailSource(id="a", name="팀A 회의", connector="gmail", labels=["팀A"], team="A"),
        MailSource(id="t", name="요청", connector="gmail", labels=["할일"], purpose="task"),
    ]
    controller.maildb.upsert("a", [
        MailMessage("m1", day(1), "주간 회의 결과", "김 <kim@team.kr>", "발표는 27일까지 마감입니다\n" + "가" * 200 + "\n비밀 본문"),
        MailMessage("m2", day(2), "예산 변경", "이 <lee@team.kr>", "예산을 바꾸기로 했다"),
        MailMessage("m3", day(40), "옛날 공지", "박 <park@other.kr>", "오래된 공지"),
    ])
    controller.maildb.upsert("t", [
        MailMessage(f"t{i}", day(3), f"요청 {i}", "boss@corp.kr", "로그인 화면 개발 요청") for i in range(60)
    ])
    controller.maildb.put_contact("@team.kr", "우리 팀", "A", "팀원")
    client = TestClient(create_web_app(controller, TOKEN, 8765), base_url="http://127.0.0.1:8765")
    yield controller, client
    controller.stop()


def get(client, **params) -> dict:
    response = client.get("/api/mail", headers=HEADERS, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_list_needs_the_token_and_never_returns_bodies(viewer):
    _, client = viewer
    assert client.get("/api/mail").status_code == 403
    assert client.get("/api/mail/x").status_code == 403
    data = get(client, since=day(90))
    assert data["total"] == 63 and len(data["items"]) == 50
    assert all("body" not in item for item in data["items"])
    assert "비밀 본문" not in str(data)
    first = next(i for i in data["items"] if i["subject"] == "주간 회의 결과")
    assert first["sender"] == "우리 팀" and first["team"] == "A" and first["in_book"]
    one = client.get(f"/api/mail/{first['id']}", headers=HEADERS).json()
    assert "비밀 본문" in one["body"] and one["source"] == "팀A 회의"
    assert client.get("/api/mail/unknown", headers=HEADERS).status_code == 404


def test_paging_filters_and_search(viewer):
    _, client = viewer
    second = get(client, since=day(90), offset=50)
    assert second["offset"] == 50 and len(second["items"]) == 13
    # A mail's team is the sender's team in the address book, else the team of its source.
    assert {i["subject"] for i in get(client, team="A", since=day(90))["items"]} == {"주간 회의 결과", "예산 변경", "옛날 공지"}
    assert get(client, purpose="task", limit=5)["total"] == 60
    assert get(client, source="a", since=day(10))["total"] == 2
    assert get(client, book="out", source="a")["total"] == 1
    assert get(client, sender="kim")["total"] == 1
    assert [i["subject"] for i in get(client, q="27일까지")["items"]] == ["주간 회의 결과"]
    assert get(client, since=day(2), until=day(2))["total"] == 1
    assert client.get("/api/mail", headers=HEADERS, params={"since": "어제"}).status_code == 400
    assert client.get("/api/mail", headers=HEADERS, params={"group": "body"}).status_code == 400


def test_group_by_team_sender_purpose_source_and_date(viewer):
    _, client = viewer
    by_team = get(client, group="team", since=day(90))
    assert by_team["groups"] == [{"key": "A", "count": 3}, {"key": "(없음)", "count": 60}]
    assert [i["group"] for i in by_team["items"][:4]] == ["A", "A", "A", "(없음)"]
    by_purpose = get(client, group="purpose", since=day(90), limit=50)
    assert {g["key"] for g in by_purpose["groups"]} == {"결정 근거", "할 일 요청"}
    by_date = get(client, group="date", source="a", since=day(90))
    assert [g["key"] for g in by_date["groups"]] == [day(1), day(2), day(40)]
    by_source = get(client, group="source", since=day(90))
    assert {g["key"]: g["count"] for g in by_source["groups"]} == {"요청": 60, "팀A 회의": 3}
    assert get(client, group="sender", source="a")["groups"][0]["key"] in {"우리 팀", "박"}


def test_calendar_week_counts_mail_per_day(viewer):
    controller, _ = viewer
    week = controller.calendar_week(day(1))
    if week["start"] <= day(1) <= week["end"]:
        assert week["mail_days"][day(1)]["count"] == 1
    # Only counts and the deadline hints travel with the week; never a body.
    assert all(set(v) == {"count", "deadlines"} for v in week["mail_days"].values())
    assert "비밀 본문" not in str(week)


def test_screens_have_the_mail_view_and_render_text_only():
    sources = all_sources()
    app_js = sources["app.js"]
    for text in ('"캘린더"', '"메일"', "da.home.panel", "localStorage", "이 메일을 분석에 포함", "이 날 받은 메일 ",
                 "주소록에 있는 사람", "묶지 않음", "마감 있음", "/api/mail?"):
        assert text in app_js, text
    # Storage is optional: every access is wrapped so the page works without it.
    assert "try { return localStorage.getItem" in app_js and "try { localStorage.setItem" in app_js
    for name in ("app.js", "common.js"):
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", sources[name]), name
