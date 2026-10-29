"""A fake Gmail account that applies the search syntax the connector sends.

Unlike the simpler fakes, messages.list honours q (newer_than, after, from, subject, label,
negation) the way Gmail does: from: matches the address or the display name, case-insensitive,
and a list without labelIds searches all mail (INBOX and SENT). Used to reproduce field reports.
"""

from __future__ import annotations

import base64
import datetime
import json
import re

from decision_agent.connectors.http import HttpResponse

API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")


def _terms(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    return [t.strip().strip('"').casefold() for t in re.split(r"\s+OR\s+", value) if t.strip()]


def _tokens(q: str) -> list[str]:
    return re.findall(r'-?\w+:\([^)]*\)|-?\w+:"[^"]*"|\S+', q)


class SearchGmail:
    def __init__(self, me: str, now: datetime.datetime | None = None, labels: dict[str, str] | None = None) -> None:
        self.me = me
        self.now = now or datetime.datetime.now()
        self.labels = labels or {}
        self.messages: dict[str, dict] = {}
        self.history: list[tuple[int, dict]] = []
        self.history_id = 1000
        self.calls: list[dict] = []
        self.sent: list[dict] = []

    # building the mailbox

    def add(self, msg_id: str, thread_id: str, sender: str, to: str, subject: str, body: str,
            when: datetime.datetime, sent: bool, labels: tuple[str, ...] = (), reply_to: str = "",
            refs: tuple[str, ...] = ()) -> None:
        headers = [{"name": "Subject", "value": subject}, {"name": "From", "value": sender},
                   {"name": "To", "value": to}, {"name": "Message-ID", "value": f"<{msg_id}@example.com>"}]
        if reply_to:
            headers += [{"name": "In-Reply-To", "value": reply_to}, {"name": "References", "value": " ".join(refs)}]
        label_ids = ["SENT"] if sent else ["INBOX", "UNREAD"]
        label_ids += [lid for lid, name in self.labels.items() if name in labels]
        message = {
            "id": msg_id, "threadId": thread_id, "labelIds": label_ids,
            "internalDate": str(int(when.timestamp() * 1000)),
            "payload": {"mimeType": "text/plain", "body": {"data": _b64(body)}, "headers": headers},
        }
        self.messages[msg_id] = message
        self.history_id += 1
        self.history.append((self.history_id, message))

    def add_threads(self, threads: dict, people: dict, me: str, other: str, start: datetime.datetime) -> None:
        """The demo threads as the seed script sends them: no Gmail label at all."""
        minute = 0
        for thread in threads["threads"]:
            ids: list[str] = []
            for number, mail in enumerate(thread["messages"], start=1):
                minute += 7
                msg_id = f"{thread['id'].lower()}m{number}"
                sent = mail["from"] == "me"
                name = people[mail["from"]]["name"]
                sender = f"{name} <{me if sent else other}>"
                to = other if sent else me
                subject = thread["subject"] if number == 1 or thread["subject"].startswith("Re:") else f"Re: {thread['subject']}"
                self.add(msg_id, thread["id"], sender, to, subject, mail["body"], start + datetime.timedelta(minutes=minute),
                         sent, reply_to=ids[-1] if ids else "", refs=tuple(ids))
                ids.append(f"<{msg_id}@example.com>")

    # search

    def _header(self, message: dict, name: str) -> str:
        return next((h["value"] for h in message["payload"]["headers"] if h["name"].lower() == name), "")

    def matches(self, message: dict, q: str) -> bool:
        stamp = datetime.datetime.fromtimestamp(int(message["internalDate"]) / 1000)
        for token in _tokens(q):
            negate = token.startswith("-")
            key, _, value = token.lstrip("-").partition(":")
            key = key.casefold()
            if key == "newer_than":
                ok = stamp >= self.now - datetime.timedelta(days=int(value.rstrip("d")))
            elif key == "after":
                ok = stamp.date() >= datetime.date(*map(int, value.split("/")))
            elif key == "from":
                sender = self._header(message, "from").casefold()
                ok = any(term and term in sender for term in _terms(value))
            elif key == "subject":
                subject = self._header(message, "subject").casefold()
                ok = any(term in subject for term in _terms(value))
            elif key == "label":
                wanted = {t for t in _terms(value)}
                ok = any(self.labels.get(i, i).casefold() in wanted for i in message["labelIds"])
            elif token.startswith("(") or not key:
                ok = True
            else:
                ok = True
            if ok == negate:
                return False
        return True

    # HTTP

    def request(self, method, url, *, headers=None, params=None, form=None, json_body=None, timeout=30.0):
        params = params or {}
        self.calls.append({"method": method, "url": url, "params": dict(params), "json": json_body})
        if method == "POST" and url == f"{API}/messages/send":
            if self.send_status != 200:
                return HttpResponse(self.send_status, b"{}")
            return HttpResponse(200, json.dumps(self._send(json_body)).encode("utf-8"))
        body = self._answer(url, params)
        if body is None:
            return HttpResponse(404, b"{}")
        return HttpResponse(200, json.dumps(body).encode("utf-8"))

    def _answer(self, url: str, params: dict):
        if url == f"{API}/labels":
            labels = [{"id": "INBOX", "name": "INBOX", "type": "system"}, {"id": "SENT", "name": "SENT", "type": "system"}]
            labels += [{"id": i, "name": n, "type": "user"} for i, n in self.labels.items()]
            return {"labels": labels}
        if url == f"{API}/profile":
            return {"emailAddress": self.me, "historyId": str(self.history_id)}
        if url == f"{API}/history":
            start = int(params["startHistoryId"])
            entries = [{"id": str(h), "messagesAdded": [{"message": {"id": m["id"], "threadId": m["threadId"],
                                                                       "labelIds": m["labelIds"]}}]}
                       for h, m in self.history if h > start]
            return {"history": entries, "historyId": str(self.history_id)}
        if url == f"{API}/messages":
            q = params.get("q", "")
            wanted = params.get("labelIds")
            found = [m for m in self.messages.values()
                     if (wanted is None or wanted in m["labelIds"]) and self.matches(m, q)]
            found.sort(key=lambda m: int(m["internalDate"]), reverse=True)
            return {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in found]}
        if url.startswith(f"{API}/messages/"):
            return self.messages.get(url.rsplit("/", 1)[-1])
        if url.startswith(f"{API}/threads/"):
            tid = url.rsplit("/", 1)[-1]
            return {"id": tid, "messages": [m for m in self.messages.values() if m["threadId"] == tid]}
        return None

    send_status = 200

    def _send(self, body: dict) -> dict:
        """messages.send: the raw message becomes a SENT message of its thread."""
        import email
        import email.policy

        raw = base64.urlsafe_b64decode(body["raw"] + "=" * (-len(body["raw"]) % 4))
        parsed = email.message_from_bytes(raw, policy=email.policy.default)
        msg_id = f"sent{len(self.sent) + 1}"
        thread_id = body.get("threadId") or f"T-{msg_id}"
        headers = [{"name": k, "value": str(v)} for k, v in parsed.items()]
        message = {
            "id": msg_id, "threadId": thread_id, "labelIds": ["SENT"],
            "internalDate": str(int(self.now.timestamp() * 1000)),
            "payload": {"mimeType": "text/plain", "body": {"data": _b64(parsed.get_content())}, "headers": headers},
        }
        self.messages[msg_id] = message
        self.sent.append({"raw": raw, "threadId": body.get("threadId", ""), "parsed": parsed})
        self.history_id += 1
        self.history.append((self.history_id, message))
        return {"id": msg_id, "threadId": thread_id, "labelIds": ["SENT"]}

    def send_calls(self) -> list[dict]:
        return [c for c in self.calls if c["method"] == "POST" and c["url"].endswith("/messages/send")]

    def list_calls(self) -> list[dict]:
        return [c for c in self.calls if c["url"] == f"{API}/messages"]
