"""Field report: a sender-only Gmail source showed no mail although the poller ran.

Reproduction: the 17 demo messages sent by the seed script (no Gmail label, INBOX and SENT),
a source with senders only, days back 14, an empty index, the real poller and the real thread
endpoint. Cause: sender entries were stored as typed. "민재 <a@b.c>" or "a@b.c, d@e.f" made a
Gmail search that matches nothing, and a plain name made Gmail return mail that the local check
then dropped (and remembered as skipped). The status still said the check was fine.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient
from gmail_search_fake import SearchGmail
from test_desktop_features import make_app
from test_mail_sources import logged_in

from decision_agent.connectors.mailfilter import sender_matches
from decision_agent.desktop import mailsources
from decision_agent.desktop.maildb import MailDB
from decision_agent.desktop.settings import ConnectorSettings, MailSource
from decision_agent.desktop.webapp import create_web_app

ME, OTHER = "eunjlee.work@gmail.com", "other.demo@gmail.com"
DATA = json.loads((Path(__file__).resolve().parents[1] / "demo" / "mail_threads.json").read_text(encoding="utf-8"))
TOKEN = "t" * 32


@pytest.fixture
def mailbox(tmp_path):
    gmail = SearchGmail(ME)
    gmail.add_threads(DATA, DATA["people"], ME, OTHER, datetime.datetime.now() - datetime.timedelta(hours=3))
    controller = make_app(dh.make_watch_dir(tmp_path), http=gmail, secrets=logged_in())
    controller.maildb = MailDB()
    controller.settings.connectors["gmail"] = ConnectorSettings(enabled=True, options={"client_id": "cid"})
    controller.start()
    assert controller.wait_idle(60)
    yield controller, gmail
    controller.stop()


def add_source(controller, senders, analysis=None) -> MailSource:
    saved = controller.save_mail_source({"name": "팀 메일", "connector": "gmail", "senders": senders,
                                         "days_back": 14, "analysis": analysis})
    return next(s for s in controller.settings.mail_sources if s.id == saved["id"])


@pytest.mark.parametrize("analysis", [None, "off"])
@pytest.mark.parametrize(("senders", "expected"), [
    ([OTHER], 17),
    ([f"  {OTHER.upper()}  "], 17),
    ([f"민재 <{OTHER}>"], 17),
    ([f"x@y.com, {OTHER}"], 17),
    (["x@y.com", OTHER], 17),
    (["민재"], 4),
])
def test_unlabeled_seeded_threads_appear_after_the_first_check(mailbox, senders, analysis, expected):
    controller, gmail = mailbox
    source = add_source(controller, senders, analysis)
    poll = controller.mail_poller.check(source)
    assert poll.ok and controller.maildb.count() == expected
    listing = controller.thread_list({})
    assert listing["total"] == (5 if expected == 17 else 1)
    # One listing call with a query Gmail can answer, and my own replies came through the threads.
    assert len(gmail.list_calls()) == 1
    assert listing["boxes"]["sent"]["count"] == listing["total"]


def test_sender_entries_are_stored_as_plain_addresses():
    source = MailSource(id="a", name="a", connector="gmail",
                        senders=["민재 <Other.Demo@Gmail.com>", "x@y.com; z@w.com", "민재", "@team.kr", "x@y.com"])
    assert source.senders == ["other.demo@gmail.com", "x@y.com", "z@w.com", "민재", "@team.kr"]


def test_a_name_matches_the_display_name_like_gmail_does():
    assert sender_matches("민재 <other@x.com>", ["민재"])
    assert not sender_matches("하은 <other@x.com>", ["민재"])
    assert sender_matches("kim@team.kr", ["team.kr"]) and sender_matches("kim@team.kr", ["@team.kr"])


def test_the_source_status_explains_the_last_check(mailbox):
    controller, _ = mailbox
    source = add_source(controller, ["nobody@example.com"])
    controller.mail_poller.check(source)
    row = next(r for r in controller.mail_source_rows("gmail") if r["id"] == source.id)
    lines = row["check_lines"]
    assert lines[0].startswith("마지막 확인 ")
    assert lines[1] == "검색 조건: newer_than:14d from:(nobody@example.com)"
    assert lines[2].startswith("조건에 맞는 메일 0통")
    controller.settings.mail_sources = [source.model_copy(update={"senders": [OTHER]})]
    controller.mail_poller.check(controller.settings.mail_sources[0])
    record = mailsources.read_check(controller.maildb, controller.settings.mail_sources[0])
    assert (record["matched"], record["loaded"], record["thread"], record["indexed"]) == (10, 17, 7, 17)
    assert "같은 대화에서 함께 읽은 메일 7통" in " ".join(mailsources.check_lines(record))


def test_sidebar_lists_boxes_and_source_labels_before_any_mail(mailbox):
    controller, _ = mailbox
    controller.save_mail_source({"name": "팀 메일", "connector": "gmail", "labels": ["BookSwap"], "team": "5조",
                                 "purpose": "decision"})
    controller.label_create("발표", "red")
    listing = controller.thread_list({})
    assert listing["total"] == 0 and set(listing["boxes"]) == {"all", "inbox", "sent"}
    names = {kind: [g["name"] for g in groups] for kind, groups in listing["groups"].items()}
    assert names == {"team": ["5조"], "purpose": ["결정 근거"], "gmail": ["BookSwap"], "local": ["발표"]}


def test_thread_endpoint_and_state_after_one_check(mailbox):
    controller, _ = mailbox
    source = add_source(controller, [f"민재 <{OTHER}>"])
    controller.mail_poller.check(source)
    http = TestClient(create_web_app(controller, TOKEN, 8765), base_url="http://127.0.0.1:8765")
    listing = http.get("/api/mail/threads", headers={"X-DA-Token": TOKEN}).json()
    assert listing["total"] == 5
    rows = listing["state"]["status"]["sources"]
    assert rows[0]["lines"][1].startswith("검색 조건: newer_than:14d from:(other.demo@gmail.com)")


def test_diag_gmail_prints_the_source_numbers_and_stores_nothing(capsys):
    import importlib.util

    from test_mail_stage1 import manager_with

    from decision_agent.desktop.settings import AppSettings

    path = Path(__file__).resolve().parents[1] / "tools" / "diag_gmail.py"
    spec = importlib.util.spec_from_file_location("diag_gmail", path)
    diag = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diag)
    gmail = SearchGmail(ME)
    gmail.add_threads(DATA, DATA["people"], ME, OTHER, datetime.datetime.now() - datetime.timedelta(hours=3))
    settings = AppSettings(
        connectors={"gmail": ConnectorSettings(enabled=True, options={"client_id": "cid"})},
        mail_sources=[MailSource(id="team", name="팀 메일", connector="gmail", senders=[f"민재 <{OTHER}>"], days_back=14)],
    )
    db = MailDB()
    assert diag.run_source(manager_with(gmail, settings), "team", db) == 0
    out = capsys.readouterr().out
    assert "query            : newer_than:14d from:(other.demo@gmail.com)" in out
    assert "matched          : 10" in out and "would read       : 17 (of which from the same threads: 7)" in out
    assert db.count() == 0 and "TOKEN-SECRET" not in out
