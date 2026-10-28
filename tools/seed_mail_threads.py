"""Send the 17 demo thread messages of demo/mail_threads.json between two or three Gmail accounts.

Usage:
  python tools/seed_mail_threads.py --me ME@gmail.com --other OTHER@gmail.com [--third THIRD@gmail.com]
  python tools/seed_mail_threads.py ... --send
  python tools/seed_mail_threads.py --kit     (rewrites tools/mail_thread_kit.html from the JSON)

Without --send it only prints the plan: who sends which subject to whom, no bodies. With --send
it asks for an app password per sending account (getpass, kept in memory only, never written
or logged), asks you to type the word send, and sends the messages in order over SMTP SSL
(smtp.gmail.com:465), waiting 2 seconds between messages. Message-ID, In-Reply-To and References
make Gmail group each thread. Gmail may show the account's own name instead of the persona name
in the From header. The demo's mail dates are the send time.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

THREADS = Path(__file__).resolve().parents[1] / "demo" / "mail_threads.json"
KIT = Path(__file__).resolve().parent / "mail_thread_kit.html"
KIT_TEMPLATE = Path(__file__).resolve().parent / "mail_thread_kit.template.html"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
DELAY_SECONDS = 2.0
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def account_for(role: str, accounts: dict[str, str]) -> str:
    """The real address that plays a persona: the professor uses THIRD when it is given."""
    if role == "me":
        return accounts["me"]
    if role == "third" and accounts.get("third"):
        return accounts["third"]
    return accounts["other"]


def build_plan(data: dict, accounts: dict[str, str]) -> list[dict]:
    """One entry per message, in sending order, with the headers that thread it."""
    people = data["people"]
    plan: list[dict] = []
    for thread in data["threads"]:
        ids: list[str] = []
        for number, message in enumerate(thread["messages"], start=1):
            sender, receiver = people[message["from"]], people[message["to"]]
            from_address = account_for(sender["account"], accounts)
            to_address = account_for(receiver["account"], accounts)
            subject = thread["subject"]
            if number > 1 and not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            msg_id = make_msgid(idstring=f"{thread['id'].lower()}-{number}", domain=from_address.split("@")[1])
            plan.append({
                "thread": thread["id"], "number": number, "subject": subject,
                "from_name": sender["name"], "from": from_address,
                "to_name": receiver["name"], "to": to_address,
                "message_id": msg_id, "in_reply_to": ids[-1] if ids else "", "references": " ".join(ids),
                "body": message["body"],
            })
            ids.append(msg_id)
    return plan


def to_message(entry: dict) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((entry["from_name"], entry["from"]))
    message["To"] = formataddr((entry["to_name"], entry["to"]))
    message["Subject"] = entry["subject"]
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = entry["message_id"]
    if entry["in_reply_to"]:
        message["In-Reply-To"] = entry["in_reply_to"]
        message["References"] = entry["references"]
    message.set_content(entry["body"])
    return message


def describe(plan: list[dict]) -> list[str]:
    """The dry-run lines: who sends what to whom. Bodies are never printed."""
    return [
        f"{e['thread']}-{e['number']}  {e['from_name']} ({e['from']}) -> {e['to_name']} ({e['to']})  제목: {e['subject']}"
        for e in plan
    ]


def send(plan: list[dict], passwords: dict[str, str], smtp_factory=smtplib.SMTP_SSL, sleep=time.sleep,
         echo=print) -> int:
    """Send in order with one SMTP session per sending account. Returns the number sent."""
    sessions: dict[str, smtplib.SMTP] = {}
    sent = 0
    try:
        for index, entry in enumerate(plan):
            if entry["from"] not in sessions:
                session = smtp_factory(SMTP_HOST, SMTP_PORT, timeout=30)
                try:
                    session.login(entry["from"], passwords[entry["from"]])
                except smtplib.SMTPAuthenticationError:
                    raise SystemExit(f"{entry['from']} 로그인에 실패했습니다. 앱 비밀번호를 확인하세요.") from None
                sessions[entry["from"]] = session
            sessions[entry["from"]].send_message(to_message(entry))
            sent += 1
            echo(f"보냄 {sent}/{len(plan)}: {entry['thread']}-{entry['number']}")
            if index + 1 < len(plan):
                sleep(DELAY_SECONDS)
    finally:
        for session in sessions.values():
            try:
                session.quit()
            except smtplib.SMTPException:
                pass
    return sent


def write_kit(data: dict, target: Path = KIT) -> None:
    """The manual sending kit: the template with the thread data embedded as JSON."""
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = KIT_TEMPLATE.read_text(encoding="utf-8").replace("/*THREADS_JSON*/null", payload)
    target.write_bytes(html.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    if "--kit" in (sys.argv[1:] if argv is None else argv):
        write_kit(json.loads(THREADS.read_text(encoding="utf-8")))
        print(f"{KIT}를 만들었습니다.")
        return 0
    parser.add_argument("--me", required=True, help="the mailbox the app reads")
    parser.add_argument("--other", required=True, help="plays the teammates (and the professor without --third)")
    parser.add_argument("--third", default="", help="optional, plays the professor")
    parser.add_argument("--send", action="store_true", help="really send (asks for app passwords)")
    parser.add_argument("--threads", default=str(THREADS))
    args = parser.parse_args(argv)
    accounts = {"me": args.me.strip(), "other": args.other.strip(), "third": args.third.strip()}
    for name, address in accounts.items():
        if address and not _ADDRESS.match(address):
            parser.error(f"--{name} 주소 형식이 올바르지 않습니다.")
    if len({a.lower() for a in accounts.values() if a}) != len([a for a in accounts.values() if a]):
        parser.error("계정 주소는 서로 달라야 합니다.")
    plan = build_plan(json.loads(Path(args.threads).read_text(encoding="utf-8")), accounts)
    print(f"메일 {len(plan)}통을 아래 순서로 보냅니다.")
    for line in describe(plan):
        print("  " + line)
    if not args.send:
        print("미리보기만 했습니다. 실제로 보내려면 --send를 붙여 다시 실행하세요.")
        return 0
    passwords = {}
    for address in dict.fromkeys(e["from"] for e in plan):
        passwords[address] = getpass.getpass(f"{address}의 앱 비밀번호: ")
    if input("보내려면 send를 입력하세요: ").strip() != "send":
        print("보내지 않았습니다.")
        return 1
    count = send(plan, passwords)
    passwords.clear()
    print(f"{count}통을 보냈습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
