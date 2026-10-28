"""The mail view: list, filter, group and read indexed mail. Local only, no model call.

List responses carry snippets only; a body is returned only for one mail at a time.
"""

from __future__ import annotations

import datetime
import json
import re
import sqlite3

from sklearn.metrics.pairwise import cosine_similarity

from decision_agent.connectors.base import unique_name
from decision_agent.connectors.mailfilter import sender_matches
from decision_agent.desktop import quick as quick_module
from decision_agent.desktop.maildb import MailDB, thread_uid
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
    item["thread"] = thread_uid(row["thread_key"]) if row["thread_key"] else ""
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
    rows = maildb.all_rows()
    if exclude:
        # Mail of the same conversation is not "similar mail", it is the conversation itself.
        own = maildb.get(exclude)
        key = own["thread_key"] if own is not None else ""
        rows = [r for r in rows if r["uid"] != exclude and not (key and r["thread_key"] == key)]
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


# Conversations: one row per thread, newest first. Local only, no model is called.

THREAD_PAGE = 50
BOXES = ("all", "inbox", "sent")


def _chips(thread: sqlite3.Row, sources: dict[str, MailSource], local: dict[str, dict],
           thread_links: dict[str, set[str]], source_links: dict[str, set[str]]) -> list[dict]:
    """Label chips of a thread: team and purpose of its sources, Gmail label names, local labels."""
    chips: list[dict] = []

    def add(text: str, kind: str, color: str = "", key: str = "") -> None:
        if text and not any(c["text"] == text for c in chips):
            chips.append({"text": text, "kind": kind, "color": color, "key": key})

    source_ids = json.loads(thread["source_ids"] or "[]")
    # My own labels first: they are what the user chose to see; then team, purpose, Gmail.
    ids = set(thread_links.get(thread["thread_key"], set()))
    for sid in source_ids:
        ids |= source_links.get(sid, set())
    for label_id in sorted(ids, key=lambda i: local.get(i, {}).get("name", "")):
        label = local.get(label_id)
        if label is not None:
            add(label["name"], "local", label["color"], f"local:{label_id}")
    for sid in source_ids:
        source = sources.get(sid)
        if source is not None:
            add(source.team, "team", key=f"team:{source.team}")
            add(PURPOSE_LABELS.get(source.purpose, ""), "purpose", key=f"purpose:{source.purpose}")
    for name in json.loads(thread["labels"] or "[]"):
        add(name, "gmail", key=f"gmail:{name}")
    return chips


def _analysis_badge(source_ids: list[str], sources: dict[str, MailSource]) -> str:
    known = [sources[s] for s in source_ids if s in sources]
    if known and all(s.effective_analysis == "off" for s in known):
        return "점검 안 함"
    return "점검됨"


def mail_doc_path(source: MailSource, row: sqlite3.Row) -> str:
    """The analysis document path of one stored mail (as mail_document names it)."""
    name = unique_name(f"{row['date']} {row['subject'].strip()}", row["msg_id"])
    return f"@{source.connector}.{source.id}/{name}"


def _thread_decisions(maildb: MailDB, thread: sqlite3.Row, sources: dict[str, MailSource],
                      decisions: dict[str, list[dict]]) -> list[dict]:
    """Decided topics whose latest decision came from a mail of this thread."""
    found: dict[str, dict] = {}
    if not decisions:
        return []
    for row in maildb.thread_rows_by_key(thread["thread_key"]):
        source = sources.get(row["source_id"])
        if source is None:
            continue
        for item in decisions.get(mail_doc_path(source, row), []):
            found.setdefault(item["topic"], item)
    return list(found.values())


def _thread_item(thread: sqlite3.Row, sources: dict[str, MailSource], local: dict[str, dict],
                 links: tuple[dict[str, set[str]], dict[str, set[str]]]) -> dict:
    source_ids = json.loads(thread["source_ids"] or "[]")
    return {
        "id": thread["tid"],
        "subject": thread["subject"],
        "participants": json.loads(thread["participants"] or "[]"),
        "count": thread["count"],
        "unread": thread["unread"],
        "has_sent": bool(thread["has_sent"]),
        "date": thread["last_date"],
        "time": thread["last_time"],
        "snippet": thread["snippet"],
        "chips": _chips(thread, sources, local, *links),
        "analysis": _analysis_badge(source_ids, sources),
    }


def list_threads(maildb: MailDB, sources: list[MailSource], params: dict,
                 decisions: dict[str, list[dict]] | None = None) -> dict:
    """Threads for the mail page and the home card, with the counts of the label sidebar.

    decisions maps an analysis document path to the topics it decided (latest decision);
    a thread whose mail decided something shows "결정 N건".
    """
    by_id = {s.id: s for s in sources}
    local = {label["id"]: label for label in maildb.labels()}
    links = maildb.label_links()
    rows = maildb.threads()
    deadlines = maildb.deadline_thread_keys()
    items = []
    for row in rows:
        item = _thread_item(row, by_id, local, links)
        item["deadline"] = row["thread_key"] in deadlines
        found = _thread_decisions(maildb, row, by_id, decisions or {})
        if found:
            item["analysis"] = f"결정 {len(found)}건"
        items.append(item)
    received = {r["tid"] for r in rows if r["count"] > 0 and _has_received(maildb, r)}
    box = str(params.get("box") or "all")
    label = str(params.get("label") or "")
    q = str(params.get("q") or "").strip().casefold()
    since = str(params.get("since") or "")
    until = str(params.get("until") or "")
    sender = str(params.get("sender") or "").strip().casefold()

    def keep(item: dict) -> bool:
        if box == "inbox" and item["id"] not in received:
            return False
        if box == "sent" and not item["has_sent"]:
            return False
        if label and not any(c["key"] == label for c in item["chips"]):
            return False
        if since and item["date"] < since:
            return False
        if until and item["date"] > until:
            return False
        if sender and not any(sender in p.casefold() for p in item["participants"]):
            return False
        if q:
            haystack = " ".join([item["subject"], item["snippet"], *item["participants"]]).casefold()
            return q in haystack or _body_matches(maildb, item["id"], q)
        return True

    chosen = [i for i in items if keep(i)]
    try:
        limit = max(1, min(THREAD_PAGE, int(params.get("limit") or THREAD_PAGE)))
        offset = max(0, int(params.get("offset") or 0))
    except ValueError:
        limit, offset = THREAD_PAGE, 0
    counts: dict[str, list[int]] = {}

    def count(key: str, item: dict) -> None:
        slot = counts.setdefault(key, [0, 0])
        slot[0] += 1
        slot[1] += int(item["unread"] > 0)

    for item in items:
        count("box:all", item)
        if item["id"] in received:
            count("box:inbox", item)
        if item["has_sent"]:
            count("box:sent", item)
        for chip in item["chips"]:
            count(chip["key"], item)
    groups = {"team": [], "purpose": [], "gmail": [], "local": []}
    for key, (total, unread) in counts.items():
        kind, _, value = key.partition(":")
        if kind in groups:
            name = local[value]["name"] if kind == "local" and value in local else (
                PURPOSE_LABELS.get(value, value) if kind == "purpose" else value)
            color = local[value]["color"] if kind == "local" and value in local else ""
            groups[kind].append({"key": key, "name": name, "count": total, "unread": unread, "color": color})
    for label_id, label in local.items():
        if f"local:{label_id}" not in counts:
            groups["local"].append({"key": f"local:{label_id}", "name": label["name"], "count": 0, "unread": 0,
                                    "color": label["color"]})
    for values in groups.values():
        values.sort(key=lambda g: g["name"])
    boxes = {b: {"count": counts.get(f"box:{b}", [0, 0])[0], "unread": counts.get(f"box:{b}", [0, 0])[1]}
             for b in BOXES}
    return {
        "total": len(chosen), "offset": offset, "limit": limit, "items": chosen[offset: offset + limit],
        "boxes": boxes, "groups": groups, "labels": list(local.values()),
    }


def _has_received(maildb: MailDB, thread: sqlite3.Row) -> bool:
    if not thread["has_sent"]:
        return True
    return any(r["direction"] != "sent" for r in maildb.thread_rows_by_key(thread["thread_key"]))


def _body_matches(maildb: MailDB, tid: str, needle: str) -> bool:
    thread = maildb.thread(tid)
    if thread is None:
        return False
    return any(needle in (r["body"] or "").casefold() for r in maildb.thread_rows_by_key(thread["thread_key"]))


def read_thread(maildb: MailDB, sources: list[MailSource], tid: str,
                decisions: dict[str, list[dict]] | None = None) -> dict | None:
    """One conversation, oldest first, with quoted text and signatures kept apart."""
    thread = maildb.thread(tid)
    if thread is None:
        return None
    by_id = {s.id: s for s in sources}
    book = ContactIndex(maildb.contacts())
    local = {label["id"]: label for label in maildb.labels()}
    links = maildb.label_links()
    item = _thread_item(thread, by_id, local, links)
    messages = []
    for row in maildb.thread_rows_by_key(thread["thread_key"]):
        contact = book.find(row["sender"])
        mine = row["direction"] == "sent"
        messages.append({
            "id": row["uid"],
            "mine": mine,
            "sender": "나" if mine else (contact or {}).get("name") or row["sender_name"] or row["sender"],
            "sender_address": row["sender"],
            "date": row["date"],
            "time": row["time"],
            "body": row["body"],
            "quoted": row["quoted"],
            "signature": row["signature"],
            "unread": bool(row["unread"]),
            "included": bool(row["include"]),
            "uid": row["uid"],
            "hints": maildb.hints(row["uid"]),
            "source": by_id[row["source_id"]].name if row["source_id"] in by_id else "",
        })
    item["messages"] = messages
    item["local_labels"] = sorted(links[0].get(thread["thread_key"], set()))
    item["decisions"] = _thread_decisions(maildb, thread, by_id, decisions or {})
    item["deadline"] = thread["thread_key"] in maildb.deadline_thread_keys()
    return item
