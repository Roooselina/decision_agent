"""The demo mail sender: dry run by default, threaded headers, passwords never kept or printed."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import seed_mail_threads as seed

DATA = json.loads(seed.THREADS.read_text(encoding="utf-8"))
ACCOUNTS = {"me": "me.demo@gmail.com", "other": "other.demo@gmail.com", "third": ""}


def test_plan_threads_every_reply_and_uses_persona_names():
    plan = seed.build_plan(DATA, ACCOUNTS)
    assert len(plan) == 17
    t1 = [e for e in plan if e["thread"] == "T1"]
    assert t1[0]["in_reply_to"] == "" and t1[0]["subject"] == "최종 발표 일정 확정 부탁드립니다"
    assert t1[2]["in_reply_to"] == t1[1]["message_id"]
    assert t1[3]["references"].split() == [e["message_id"] for e in t1[:3]]
    assert all(e["subject"] == "Re: 결제 방식 확인" for e in plan if e["thread"] == "T3")
    message = seed.to_message(t1[0])
    assert message["From"].startswith("=?utf-8?") or "박서연" in message["From"]
    assert message["Message-ID"] == t1[0]["message_id"] and "other.demo@gmail.com" in message["From"]
    # The professor uses the third account when it is given; teammates always use OTHER.
    with_third = seed.build_plan(DATA, {**ACCOUNTS, "third": "prof.demo@gmail.com"})
    assert {e["from"] for e in with_third if e["from_name"] == "박서연 교수님"} == {"prof.demo@gmail.com"}
    assert {e["from"] for e in with_third if e["from_name"] == "민재"} == {"other.demo@gmail.com"}


def test_dry_run_prints_the_plan_without_bodies_and_sends_nothing(capsys, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("no SMTP connection in a dry run")

    monkeypatch.setattr(seed.smtplib, "SMTP_SSL", refuse)
    assert seed.main(["--me", ACCOUNTS["me"], "--other", ACCOUNTS["other"]]) == 0
    out = capsys.readouterr().out
    assert out.count("제목:") == 17 and "그럼 배포는 Render" not in out and "미리보기만" in out


def test_send_needs_the_typed_word_and_never_prints_passwords(capsys, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            assert (host, port) == ("smtp.gmail.com", 465)

        def login(self, user, password):
            assert password == f"pw-{user}"

        def send_message(self, message):
            sent.append(message)

        def quit(self):
            pass

    monkeypatch.setattr(seed.getpass, "getpass", lambda prompt: "pw-" + prompt.split("의 ")[0])
    monkeypatch.setattr(seed.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(seed.time, "sleep", lambda s: None)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert seed.main(["--me", ACCOUNTS["me"], "--other", ACCOUNTS["other"], "--send"]) == 1 and sent == []
    monkeypatch.setattr("builtins.input", lambda prompt: "send")
    pauses = []
    plan = seed.build_plan(DATA, ACCOUNTS)
    assert seed.send(plan, {a: f"pw-{a}" for a in {e["from"] for e in plan}}, smtp_factory=FakeSMTP,
                     sleep=pauses.append, echo=lambda *_: None) == 17
    assert pauses == [2.0] * 16 and next(m["Subject"] for m in sent) == "최종 발표 일정 확정 부탁드립니다"
    assert "pw-" not in capsys.readouterr().out


def test_bad_or_equal_addresses_are_refused():
    with pytest.raises(SystemExit):
        seed.main(["--me", "same@gmail.com", "--other", "SAME@gmail.com"])
    with pytest.raises(SystemExit):
        seed.main(["--me", "not-an-address", "--other", "b@gmail.com"])


def test_kit_is_generated_from_the_json_and_writes_text_only(tmp_path):
    target = tmp_path / "kit.html"
    seed.write_kit(DATA, target)
    html = target.read_text(encoding="utf-8")
    assert "/*THREADS_JSON*/" not in html and "그럼 배포는 Render 유료 플랜으로 하자." in html
    script = html.split("<script>")[1]
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|localStorage|sessionStorage", script)
    assert "mail.google.com/mail/?" in script and 'aria-disabled", "true"' in script
    # The checked-in kit matches the JSON.
    assert (TOOLS / "mail_thread_kit.html").read_text(encoding="utf-8") == html
