"""A fake Gmail account (HTTP level) holding the demo mail threads, for UI checks.

It answers the read-only Gmail API calls the connector makes: labels, profile, messages list
and get, threads get and history. Nothing leaves the process.
"""

from __future__ import annotations

import base64
import datetime
import json
from pathlib import Path

from decision_agent.connectors.http import HttpResponse

API = "https://gmail.googleapis.com/gmail/v1/users/me"
THREADS = Path(__file__).resolve().parents[1] / "demo" / "mail_threads.json"
ME = "eunjung.demo@example.com"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")


class FakeGmailHttp:
    """Serves the five demo threads; my own messages carry SENT, the others INBOX and a label."""

    def __init__(self, label: str = "BookSwap", today: datetime.date | None = None) -> None:
        self.label = label
        self.messages: dict[str, dict] = {}
        self.history_id = 500
        data = json.loads(THREADS.read_text(encoding="utf-8"))
        people = data["people"]
        today = today or datetime.date.today()
        # The story's "this week" ends today: shift the thread dates so the last mail is today.
        last = max(datetime.datetime.fromisoformat(m["date"]) for t in data["threads"] for m in t["messages"])
        shift = datetime.datetime.combine(today, datetime.time()) - datetime.datetime.combine(last.date(), datetime.time())
        for thread in data["threads"]:
            ids: list[str] = []
            for number, mail in enumerate(thread["messages"], start=1):
                msg_id = f"{thread['id'].lower()}m{number}"
                sent = mail["from"] == "me"
                sender = people[mail["from"]]
                address = ME if sent else sender["example"]
                to = people[mail["to"]]["example"] if sent else ME
                subject = thread["subject"] if number == 1 or thread["subject"].startswith("Re:") else f"Re: {thread['subject']}"
                headers = [
                    {"name": "Subject", "value": subject}, {"name": "From", "value": f"{sender['name']} <{address}>"},
                    {"name": "To", "value": to}, {"name": "Message-ID", "value": f"<{msg_id}@example.com>"},
                ]
                if ids:
                    headers += [{"name": "In-Reply-To", "value": ids[-1]}, {"name": "References", "value": " ".join(ids)}]
                stamp = datetime.datetime.fromisoformat(mail["date"]) + shift
                self.messages[msg_id] = {
                    "id": msg_id, "threadId": thread["id"],
                    "labelIds": ["SENT"] if sent else ["INBOX", "L1", "UNREAD"],
                    "internalDate": str(int(stamp.timestamp() * 1000)),
                    "payload": {"mimeType": "text/plain", "body": {"data": _b64(mail["body"])}, "headers": headers},
                }
                ids.append(f"<{msg_id}@example.com>")

    def request(self, method, url, *, headers=None, params=None, form=None, json_body=None, timeout=30.0):
        params = params or {}
        if url == f"{API}/labels":
            body = {"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"},
                               {"id": "L1", "name": self.label, "type": "user"}]}
        elif url == f"{API}/profile":
            body = {"emailAddress": ME, "historyId": str(self.history_id)}
        elif url == f"{API}/history":
            body = {"history": [], "historyId": str(self.history_id)}
        elif url == f"{API}/messages":
            wanted = params.get("labelIds")
            body = {"messages": [{"id": i, "threadId": m["threadId"]} for i, m in sorted(self.messages.items(), reverse=True)
                                 if wanted is None or wanted in m["labelIds"]]}
        elif url.startswith(f"{API}/messages/"):
            body = self.messages.get(url.rsplit("/", 1)[-1])
        elif url.startswith(f"{API}/threads/"):
            tid = url.rsplit("/", 1)[-1]
            body = {"id": tid, "messages": [m for m in self.messages.values() if m["threadId"] == tid]}
        else:
            body = None
        if body is None:
            return HttpResponse(404, b"{}")
        return HttpResponse(200, json.dumps(body).encode("utf-8"))


class MemorySecrets:
    def __init__(self, **values: str) -> None:
        self.values = dict(values)

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def logged_in() -> MemorySecrets:
    token = {"refresh_token": "R", "access_token": "A", "expires_at": 4e9,
             "scopes": ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]}
    return MemorySecrets(**{"connector.gmail.oauth": json.dumps(token)})
