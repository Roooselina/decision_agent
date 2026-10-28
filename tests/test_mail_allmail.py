"""All-mail sources, the local mail index, analysis policies and the address book."""

from __future__ import annotations

import pytest
from test_mail_sources import FakeGmail, mail_app, mail_paths, run, source  # noqa: F401

from decision_agent.desktop.errors import UserError
from decision_agent.desktop.maildb import MailDB, clean_pattern
from decision_agent.desktop.mailsources import expand_senders, query_for
from decision_agent.desktop.settings import ALL_MAIL_NOTICE, MailSource


def all_mail(**values) -> dict:
    data = {"name": "업무 메일", "connector": "gmail", "scope": "all_mail", "labels": [], "days_back": 30}
    data.update(values)
    return data


def runtime_of(controller):
    return next(iter(controller.runtimes.values()))


def test_all_mail_needs_the_confirmation(mail_app):  # noqa: F811
    controller, _, _ = mail_app
    with pytest.raises(UserError, match="확인란"):
        controller.save_mail_source(all_mail())
    made = controller.save_mail_source(all_mail(all_mail_confirm=True))
    assert made["all_mail_confirmed_at"] and made["scope"] == "all_mail"
    # Editing keeps the earlier confirmation; a forged timestamp from the form is ignored.
    again = controller.save_mail_source({**all_mail(name="업무"), "id": made["id"], "all_mail_confirmed_at": "1999"})
    assert again["all_mail_confirmed_at"] == made["all_mail_confirmed_at"]
    with pytest.raises(ValueError, match="확인란"):
        MailSource(id="x", name="x", connector="gmail", scope="all_mail")
    assert MailSource(id="x", name="x", connector="gmail", scope="all_mail", all_mail_confirmed_at="t").effective_analysis == "off"
    assert MailSource(id="x", name="x", connector="gmail", labels=["a"]).effective_analysis == "all"
    assert "AI 분석은 따로 켭니다" in ALL_MAIL_NOTICE


def test_all_mail_with_analysis_off_stores_mail_but_never_reaches_the_model(mail_app):  # noqa: F811
    controller, gmail, _ = mail_app
    gmail.add("m01", "INBOX", "회의 결과", "- 발표를 연기하기로 결정했다")
    gmail.add("m02", "INBOX", "점심", "같이 드실 분")
    made = controller.save_mail_source(all_mail(all_mail_confirm=True))
    runtime = runtime_of(controller)
    run(controller, made["id"])
    assert controller.maildb.count(made["id"]) == 2
    assert mail_paths(controller) == []
    # The counter is reset at the start of every cycle: this cycle made no model call.
    assert runtime.engine.counter.calls == 0
    status = next(r for r in controller.connector_overview() if r["id"] == "gmail")["sources"][0]["status"]
    assert status["stats"]["stored_only"] == 2 and "분석하지 않고 저장만 2건" in status["summary"]


def test_address_book_policy_and_the_per_mail_override(mail_app):  # noqa: F811
    controller, gmail, _ = mail_app
    gmail.add("m01", "INBOX", "A 회의", "- 결제는 카드만 지원하기로 했다", sender="김 <kim@team.kr>")
    gmail.add("m02", "INBOX", "광고", "- 할인 행사를 연기하기로 했다", sender="ad@shop.com")
    made = controller.save_mail_source(all_mail(all_mail_confirm=True, analysis="address_book"))
    controller.save_contact({"pattern": "@team.kr", "name": "우리 팀", "team": "A", "role": "팀원"})
    run(controller, made["id"])
    paths = mail_paths(controller)
    assert len(paths) == 1 and "A 회의" in paths[0]
    # Including one mail by hand sends it to the analysis whatever the policy says.
    ad = next(r for r in controller.maildb.rows(made["id"]) if r["msg_id"] == "m02")
    controller.include_mail(ad["uid"])
    assert controller.wait_idle(60)
    assert len(mail_paths(controller)) == 2
    # Without a team the address book policy does not select the sender.
    controller.save_contact({"pattern": "@team.kr", "name": "우리 팀", "team": "", "role": "기타"})
    assert controller.wait_idle(60)
    assert all("A 회의" not in p for p in mail_paths(controller))


def test_incremental_all_mail_fetch_cap_and_clearing_on_delete(mail_app):  # noqa: F811
    controller, gmail, _ = mail_app
    controller.settings.mail_cycle_cap = 2
    for number in range(1, 4):
        gmail.add(f"m0{number}", "INBOX", f"메일 {number}", "내용")
    made = controller.save_mail_source(all_mail(all_mail_confirm=True))
    run(controller, made["id"])
    assert gmail.body_calls() == 2 and controller.maildb.count(made["id"]) == 2
    status = next(r for r in controller.connector_overview() if r["id"] == "gmail")["sources"][0]["status"]
    assert status["stats"]["deferred"] == 1 and "다음에 가져올 메일 1건" in status["summary"]
    gmail.http.calls.clear()
    run(controller, made["id"])
    assert gmail.body_calls() == 1 and controller.maildb.count(made["id"]) == 3
    # Every listing so far was a full window; the next one only asks for new mail.
    gmail.add("m04", "INBOX", "새 메일", "내용", days_ago=0)
    gmail.http.calls.clear()
    run(controller, made["id"])
    listing = gmail.list_calls()[0]["params"]["q"]
    assert listing.startswith("after:") and gmail.body_calls() == 1
    fetched = [c["url"].rsplit("/", 1)[-1] for c in gmail.http.calls if c["params"].get("format") == "full"]
    assert fetched == ["m04"]
    controller.delete_mail_source(made["id"])
    assert controller.maildb.count(made["id"]) == 0


def test_forget_mail_empties_the_index_and_keeps_the_source(mail_app):  # noqa: F811
    controller, gmail, _ = mail_app
    gmail.add("m01", "팀A", "A", "- 발표를 연기하기로 한다")
    made = controller.save_mail_source(source())
    run(controller, made["id"])
    assert controller.maildb.count(made["id"]) == 1 and mail_paths(controller)
    assert controller.forget_source_mail(made["id"]) == 1
    assert controller.wait_idle(60)
    assert controller.maildb.count(made["id"]) == 0 and mail_paths(controller) == []
    assert [s.id for s in controller.settings.mail_sources] == [made["id"]]


# address book


def test_team_expansion_in_senders_and_domain_patterns():
    db = MailDB()
    db.put_contact("kim@team.kr", "김", "A", "팀원")
    db.put_contact("@lab.ac.kr", "연구실", "A", "교수")
    db.put_contact("lee@other.kr", "이", "B", "외부")
    assert expand_senders(["team:A", "x@y.z", "team:없음"], db) == ["kim@team.kr", "@lab.ac.kr", "x@y.z"]
    assert db.contact_for("Prof <park@lab.ac.kr>")["name"] == "연구실"
    assert db.contact_for("kim@team.kr")["name"] == "김"
    assert db.contact_for("nobody@else.com") is None
    from decision_agent.desktop.settings import AppSettings

    source_ = MailSource(id="s", name="A팀", connector="gmail", senders=["team:A"])
    assert query_for(source_, AppSettings(), db).senders == ["kim@team.kr", "@lab.ac.kr"]
    assert clean_pattern("@x.com") == "@x.com" and clean_pattern("not an address") is None
    with pytest.raises(ValueError):
        db.put_contact("kim@team.kr", role="사장")


def test_csv_import_skips_bad_rows_and_counts_them():
    db = MailDB()
    text = "이름,주소,팀\n김,kim@team.kr,A\n잘못된 줄\n이,not-an-address,B\n박,@lab.ac.kr,\n"
    assert db.import_csv(text) == {"added": 2, "skipped": 2}
    assert {c["pattern"] for c in db.contacts()} == {"kim@team.kr", "@lab.ac.kr"}


def test_top_senders_suggests_frequent_senders_not_in_the_book():
    from decision_agent.connectors.base import MailMessage

    db = MailDB()
    mails = [MailMessage(f"m{i}", "2026-10-20", "s", f"{name} <{addr}>")
             for i, (name, addr) in enumerate([("김", "kim@a.kr")] * 3 + [("이", "lee@b.kr")] * 2 + [("박", "park@c.kr")])]
    db.upsert("src", mails)
    db.put_contact("lee@b.kr", "이", "B")
    assert [(x["address"], x["count"], x["name"]) for x in db.top_senders()] == [("kim@a.kr", 3, "김"), ("park@c.kr", 1, "박")]


def test_contacts_api_validates_and_needs_the_token(mail_app):  # noqa: F811
    from fastapi.testclient import TestClient

    from decision_agent.desktop.webapp import create_web_app

    controller, _, _ = mail_app
    client = TestClient(create_web_app(controller, "t" * 32, 8765), base_url="http://127.0.0.1:8765")
    headers = {"X-DA-Token": "t" * 32}
    assert client.get("/api/contacts").status_code == 403
    assert client.post("/api/contacts", headers=headers, json={"pattern": "bad"}).status_code == 400
    assert client.post("/api/contacts", headers=headers, json={"pattern": "a@b.kr", "evil": 1}).status_code == 400
    assert client.post("/api/contacts", headers=headers, json={"pattern": "A@B.kr", "team": "A"}).json()["pattern"] == "a@b.kr"
    assert [c["pattern"] for c in client.get("/api/contacts", headers=headers).json()["contacts"]] == ["a@b.kr"]
    imported = client.post("/api/contacts/import", headers=headers, json={"text": "김,kim@x.kr,A\nbad"}).json()
    assert imported == {"added": 1, "skipped": 1}
    assert client.delete("/api/contacts?pattern=a@b.kr", headers=headers).json() == {"ok": True}
    assert client.post("/api/mail/nope/include", headers=headers).status_code == 400


def test_settings_screen_has_the_all_mail_and_address_book_controls():
    from decision_agent.desktop.webpage import all_sources

    app_js = all_sources()["app.js"]
    for text in ("폴더의 모든 메일", ALL_MAIL_NOTICE, "all_mail_confirm", "분석 안 함 (저장과 검색만)", "주소록", "CSV 가져오기",
                 "자주 온 보낸 사람", "팀 지정", "메일 지우기", "/api/contacts"):
        assert text in app_js, text
