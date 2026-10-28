"""The mail view: list, filter, group and read indexed mail. Local only, no model call.

List responses carry snippets only; a body is returned only for one mail at a time.
"""

from __future__ import annotations

import json
import sqlite3

from decision_agent.connectors.mailfilter import sender_matches
from decision_agent.desktop import quick as quick_module
from decision_agent.desktop.maildb import MailDB
from decision_agent.desktop.mailsources import CONNECTOR_LABELS, PURPOSE_LABELS
from decision_agent.desktop.settings import MailSource

PAGE_SIZE = 50
GROUPS = ("date", "team", "sender", "purpose", "source")
NO_GROUP = "(없음)"


class ContactIndex:
    """Fast lookup of the address book: exact addresses first, then the longest domain."""

    def __init__(self, contacts: list[dict]) -> None:
        self.exact = {c["pattern"]: c for c in contacts if not c["pattern"].startswith("@")}
        self.domains = sorted(
            (c for c in contacts if c["pattern"].startswith("@")), key=lambda c: -len(c["pattern"])
        )

    def find(self, address: str) -> dict | None:
        found = self.exact.get(address)
        if found is not None:
            return found
        return next((c for c in self.domains if sender_matches(address, [c["pattern"]])), None)


def _item(row: sqlite3.Row, sources: dict[str, MailSource], book: ContactIndex) -> dict:
    source = sources.get(row["source_id"])
    contact = book.find(row["sender"])
    team = (contact or {}).get("team") or (source.team if source else "")
    return {
        "id": row["uid"],
        "date": row["date"],
        "subject": row["subject"],
        "snippet": row["snippet"],
        "sender": (contact or {}).get("name") or row["sender_name"] or row["sender"],
        "sender_address": row["sender"],
        "in_book": contact is not None,
        "team": team,
        "source_id": row["source_id"],
        "source": source.name if source else "",
        "connector": CONNECTOR_LABELS.get(source.connector, "") if source else "",
        "purpose": source.purpose if source else "",
        "purpose_label": PURPOSE_LABELS.get(source.purpose, "") if source else "",
        "included": bool(row["include"]),
    }


def _group_key(item: dict, group: str) -> str:
    if group == "date":
        return item["date"]
    if group == "team":
        return item["team"] or NO_GROUP
    if group == "sender":
        return item["sender"] or NO_GROUP
    if group == "purpose":
        return item["purpose_label"] or NO_GROUP
    if group == "source":
        return item["source"] or NO_GROUP
    return ""


def list_mail(maildb: MailDB, sources: list[MailSource], params: dict) -> dict:
    """Filtered, optionally grouped page of mail. Bodies are never included."""
    by_id = {s.id: s for s in sources}
    book = ContactIndex(maildb.contacts())
    q = str(params.get("q") or "").strip()
    rows = maildb.all_rows()
    items = [_item(r, by_id, book) for r in rows]
    bodies = {r["uid"]: r["body"] for r in rows} if q else {}
    team = str(params.get("team") or "")
    sender = str(params.get("sender") or "").strip().casefold()
    in_book = str(params.get("book") or "")
    purpose = str(params.get("purpose") or "")
    source = str(params.get("source") or "")
    since = str(params.get("since") or "")
    until = str(params.get("until") or "")

    def keep(item: dict) -> bool:
        if team and item["team"] != team:
            return False
        if sender and sender not in item["sender"].casefold() and sender not in item["sender_address"]:
            return False
        if in_book == "in" and not item["in_book"]:
            return False
        if in_book == "out" and item["in_book"]:
            return False
        if purpose and item["purpose"] != purpose:
            return False
        if source and item["source_id"] != source:
            return False
        if since and item["date"] < since:
            return False
        if until and item["date"] > until:
            return False
        if q:
            needle = q.casefold()
            haystack = " ".join((item["subject"], item["sender"], item["sender_address"], bodies[item["id"]]))
            return needle in haystack.casefold()
        return True

    chosen = [item for item in items if keep(item)]
    if q and chosen:
        # Local TF-IDF order for the search; equal scores keep the newest first.
        texts = [f"{i['subject']} {bodies[i['id']]}" for i in chosen]
        order = [index for index, _ in quick_module.rank_similar(q, texts, limit=len(texts), minimum=0.0)]
        rest = [i for i in range(len(chosen)) if i not in set(order)]
        chosen = [chosen[i] for i in order + rest]
    group = str(params.get("group") or "")
    groups: list[dict] = []
    if group in GROUPS:
        counts: dict[str, int] = {}
        for item in chosen:
            item["group"] = _group_key(item, group)
            counts[item["group"]] = counts.get(item["group"], 0) + 1
        keys = sorted(counts, reverse=True) if group == "date" else sorted(counts, key=lambda k: (k == NO_GROUP, k))
        rank = {key: position for position, key in enumerate(keys)}
        chosen.sort(key=lambda item: rank[item["group"]])
        groups = [{"key": key, "count": counts[key]} for key in keys]
    try:
        limit = max(1, min(PAGE_SIZE, int(params.get("limit") or PAGE_SIZE)))
        offset = max(0, int(params.get("offset") or 0))
    except ValueError:
        limit, offset = PAGE_SIZE, 0
    facets = {
        "teams": sorted({i["team"] for i in items if i["team"]}),
        "sources": [{"id": s.id, "name": s.name} for s in sources if s.id in {i["source_id"] for i in items}],
        "purposes": [[k, v] for k, v in PURPOSE_LABELS.items()],
    }
    return {
        "total": len(chosen), "offset": offset, "limit": limit, "items": chosen[offset: offset + limit],
        "groups": groups, "facets": facets,
    }


def read_mail(maildb: MailDB, sources: list[MailSource], uid: str) -> dict | None:
    row = maildb.get(uid)
    if row is None:
        return None
    by_id = {s.id: s for s in sources}
    item = _item(row, by_id, ContactIndex(maildb.contacts()))
    source = by_id.get(row["source_id"])
    item["body"] = row["body"]
    item["labels"] = json.loads(row["labels"] or "[]")
    item["analysis"] = source.effective_analysis if source else "off"
    return item


def day_summary(maildb: MailDB, start: str, end: str) -> dict[str, dict]:
    """Mails received per day of a date range (counts only)."""
    found: dict[str, dict] = {}
    for row in maildb.all_rows():
        if start <= row["date"] <= end:
            found.setdefault(row["date"], {"count": 0})["count"] += 1
    return found
