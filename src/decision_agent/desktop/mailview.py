"""The mail view: list, filter, group and read indexed mail. Local only, no model call.

List responses carry snippets only; a body is returned only for one mail at a time.
"""

from __future__ import annotations

import datetime
import json
import re
import sqlite3

from sklearn.metrics.pairwise import cosine_similarity

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
    deadlines = maildb.deadline_uids()
    for item in items:
        item["deadline"] = item["id"] in deadlines
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
    # Dates found in the mail ("메일 속 날짜"): hints only, never used by the analysis.
    item["hints"] = maildb.hints(uid)
    item["deadline"] = any(h["kind"] == "deadline" for h in item["hints"])
    return item


def day_summary(maildb: MailDB, start: str, end: str) -> dict[str, dict]:
    """Per day of a date range: mails received, and mails that seem to be due that day."""
    found: dict[str, dict] = {}
    for row in maildb.all_rows():
        if start <= row["date"] <= end:
            found.setdefault(row["date"], {"count": 0, "deadlines": []})["count"] += 1
    for row in maildb.deadlines_between(start, end):
        day = found.setdefault(row["due"], {"count": 0, "deadlines": []})
        if any(d["id"] == row["uid"] for d in day["deadlines"]):
            continue
        day["deadlines"].append({
            "id": row["uid"], "subject": row["subject"], "sender": row["sender_name"] or row["sender"],
            "phrase": row["phrase"], "sentence": row["sentence"], "date": row["date"],
        })
    return found


# Related mail: character TF-IDF over subject and body, like the candidate ranking of the
# analysis. Ties keep the newest mail first, then the id, so the order never changes by chance.
MIN_RELATED = 0.1
UNITS_PER_MAIL = 40
_WORD = re.compile(r"\w{2,}")


def match_snippet(body: str, text: str, width: int = 160) -> str:
    """A short piece of the body around the first word it shares with the text."""
    flat = " ".join(body.split())
    lowered = flat.casefold()
    for word in sorted(set(_WORD.findall(text.casefold())), key=len, reverse=True):
        position = lowered.find(word)
        if position >= 0:
            start = max(0, position - 50)
            return ("..." if start else "") + flat[start: start + width]
    return flat[:width]


def related_mail(
    maildb: MailDB,
    text: str,
    limit: int = 5,
    exclude: str = "",
    timeline: bool = False,
) -> list[dict]:
    """Indexed mails most similar to the text, above a minimum score. No model is called.

    timeline=True returns them by date ascending, to show how a topic developed by mail.
    """
    rows = [r for r in maildb.all_rows() if r["uid"] != exclude]
    if not text.strip() or not rows:
        return []
    # A mail scores by its best unit (the subject or one body line): a long mail that holds
    # the matching sentence is not diluted by the rest of its text.
    units: list[tuple[int, str]] = []
    for index, row in enumerate(rows):
        units.append((index, row["subject"]))
        lines = [line for line in (row["body"] or "").splitlines() if line.strip()]
        units.extend((index, line) for line in lines[:UNITS_PER_MAIL])
    matrix = quick_module._vectorizer().fit_transform([u[1] for u in units] + [text])
    unit_sims = cosine_similarity(matrix[-1], matrix[:-1]).ravel()
    sims = [0.0] * len(rows)
    best = [""] * len(rows)
    for (index, unit), score in zip(units, unit_sims, strict=True):
        if score > sims[index]:
            sims[index], best[index] = float(score), unit

    def newest(row: sqlite3.Row) -> int:
        try:
            return -datetime.date.fromisoformat(row["date"][:10]).toordinal()
        except ValueError:
            return 0

    order = sorted(range(len(rows)), key=lambda i: (-round(float(sims[i]), 9), newest(rows[i]), rows[i]["uid"]))
    picked = [i for i in order if sims[i] >= MIN_RELATED][:limit]
    if timeline:
        picked.sort(key=lambda i: (rows[i]["date"], rows[i]["uid"]))
    book = ContactIndex(maildb.contacts())
    return [
        {
            "id": rows[i]["uid"], "date": rows[i]["date"], "subject": rows[i]["subject"],
            "sender": (book.find(rows[i]["sender"]) or {}).get("name") or rows[i]["sender_name"] or rows[i]["sender"],
            "snippet": match_snippet(best[i] or rows[i]["body"] or rows[i]["subject"], text),
            "score": round(float(sims[i]), 3),
        }
        for i in picked
    ]
