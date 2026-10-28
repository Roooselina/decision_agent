"""Local mail index and address book.

One database in the app's own folder (never inside a watched folder) for every source: the
mail view searches across sources, and the address book is shared. Nothing here is sent to a
model or logged. Bodies are the cleaned text (at most 6000 characters), snippets 160.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from decision_agent.connectors.base import MailMessage, normalize_subject, split_message
from decision_agent.connectors.mailfilter import address_of, sender_matches
from decision_agent.desktop.mailhints import extract_hints

SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_index (
    uid TEXT PRIMARY KEY, source_id TEXT NOT NULL, msg_id TEXT NOT NULL, date TEXT NOT NULL,
    subject TEXT NOT NULL, sender TEXT NOT NULL, sender_name TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL, body TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '',
    labels TEXT NOT NULL DEFAULT '[]', fetched_at TEXT NOT NULL, include INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS mail_index_source_msg ON mail_index(source_id, msg_id);
CREATE INDEX IF NOT EXISTS mail_index_date ON mail_index(date);
CREATE TABLE IF NOT EXISTS mail_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mail_hints (
    uid TEXT NOT NULL, date TEXT NOT NULL, phrase TEXT NOT NULL, sentence TEXT NOT NULL,
    kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS mail_hints_uid ON mail_hints(uid);
CREATE INDEX IF NOT EXISTS mail_hints_date ON mail_hints(date);
CREATE TABLE IF NOT EXISTS contacts (
    pattern TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', team TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '기타', note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mail_threads (
    tid TEXT PRIMARY KEY, thread_key TEXT NOT NULL UNIQUE, subject TEXT NOT NULL,
    participants TEXT NOT NULL, count INTEGER NOT NULL, unread INTEGER NOT NULL,
    has_sent INTEGER NOT NULL, last_date TEXT NOT NULL, last_time TEXT NOT NULL,
    snippet TEXT NOT NULL, source_ids TEXT NOT NULL, labels TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS mail_threads_last ON mail_threads(last_date, last_time);
CREATE TABLE IF NOT EXISTS mail_labels (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS thread_labels (
    thread_key TEXT NOT NULL, label_id TEXT NOT NULL, PRIMARY KEY (thread_key, label_id)
);
CREATE TABLE IF NOT EXISTS source_labels (
    source_id TEXT NOT NULL, label_id TEXT NOT NULL, PRIMARY KEY (source_id, label_id)
);
"""
# Columns added after the first release of the index; added in place on older databases.
COLUMNS = (
    ("time", "TEXT NOT NULL DEFAULT ''"),
    ("recipients", "TEXT NOT NULL DEFAULT ''"),
    ("header_id", "TEXT NOT NULL DEFAULT ''"),
    ("in_reply_to", "TEXT NOT NULL DEFAULT ''"),
    ("refs", "TEXT NOT NULL DEFAULT '[]'"),
    ("direction", "TEXT NOT NULL DEFAULT ''"),
    ("thread_key", "TEXT NOT NULL DEFAULT ''"),
    ("quoted", "TEXT NOT NULL DEFAULT ''"),
    ("signature", "TEXT NOT NULL DEFAULT ''"),
    ("unread", "INTEGER NOT NULL DEFAULT 0"),
    ("via_thread", "INTEGER NOT NULL DEFAULT 0"),
)
# Colors a local label may take (the UI shows them as dots).
LABEL_COLORS = ("blue", "green", "orange", "red", "purple", "gray")
MAX_LABELS = 50

SNIPPET = 160
BODY_LIMIT = 6000
ROLES = ("팀원", "교수", "외부", "기타")
MAX_CONTACTS = 5000


def mail_uid(source_id: str, msg_id: str) -> str:
    """Opaque id used by the web API; it reveals nothing about the mail."""
    return hashlib.sha1(f"{source_id}\x1f{msg_id}".encode()).hexdigest()[:16]


def clean_pattern(value: str) -> str | None:
    """kim@x.com or @x.com, lower case; None when it is neither."""
    text = value.strip().casefold()
    if text.startswith("@"):
        domain = text[1:]
        return text if domain and "." in domain and "@" not in domain and " " not in domain else None
    if text.count("@") == 1 and " " not in text:
        local, domain = text.split("@")
        return text if local and "." in domain else None
    return None


class MailDB:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            if str(path) != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            present = {r["name"] for r in self.conn.execute("PRAGMA table_info(mail_index)")}
            for name, spec in COLUMNS:
                if name not in present:
                    self.conn.execute(f"ALTER TABLE mail_index ADD COLUMN {name} {spec}")
            self.conn.execute("CREATE INDEX IF NOT EXISTS mail_index_thread ON mail_index(thread_key)")
            self.conn.commit()
        self.ensure_hints()
        self.ensure_threads()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # fetch memory per source

    def meta_get(self, key: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT value FROM mail_meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_put(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO mail_meta(key,value) VALUES(?,?)", (key, value))
            self.conn.commit()

    def meta_delete(self, key: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM mail_meta WHERE key=?", (key,))
            self.conn.commit()

    def purge(self) -> None:
        """Delete every indexed mail, fetch memory, contact and local label."""
        with self.lock:
            for table in (
                "mail_index", "mail_hints", "mail_meta", "contacts", "mail_threads", "mail_labels",
                "thread_labels", "source_labels",
            ):
                self.conn.execute(f"DELETE FROM {table}")
            self.conn.commit()
        self.bump()

    # change counter: the open pages poll it to learn about new mail without any network call

    def change_seq(self) -> int:
        return int(self.meta_get("change_seq") or 0)

    def bump(self) -> int:
        with self.lock:
            value = self.change_seq() + 1
            self.meta_put("change_seq", str(value))
        return value

    # mail index

    def upsert(
        self,
        source_id: str,
        messages: Iterable[MailMessage],
        accounts: Iterable[str] = (),
        unread: bool = False,
    ) -> int:
        """Store mails of one source. accounts are the connected addresses (for direction);
        unread marks mail that arrived after the source's first fetch as not read yet.
        Returns the number of mails that were not stored before."""
        stamp = datetime.datetime.now().replace(microsecond=0).isoformat()
        mine = {a.strip().casefold() for a in accounts if a and a.strip()}
        rows = []
        for m in messages:
            text, quoted, signature = split_message(m.body, BODY_LIMIT)
            snippet = " ".join(text.split())[:SNIPPET]
            sender = address_of(m.sender)
            direction = m.direction or ("sent" if sender and sender in mine else "received")
            is_unread = int(direction != "sent" and (unread or "UNREAD" in m.labels))
            rows.append((
                mail_uid(source_id, m.msg_id), source_id, m.msg_id, m.date, m.subject,
                sender, m.sender_name or _display_name(m.sender), snippet, text,
                m.thread_id, json.dumps(m.labels, ensure_ascii=False), stamp,
                m.time, m.to[:500], m.header_id[:300], m.in_reply_to[:300],
                json.dumps(m.references[-20:]), direction, thread_key_of(m), quoted, signature,
                is_unread, int(m.via_thread),
            ))
        with self.lock:
            before = {
                r["uid"] for r in self.conn.execute(
                    f"SELECT uid FROM mail_index WHERE uid IN ({','.join('?' * len(rows))})",
                    [r[0] for r in rows],
                )
            } if rows else set()
            self.conn.executemany(
                "INSERT INTO mail_index(uid,source_id,msg_id,date,subject,sender,sender_name,snippet,"
                "body,thread_id,labels,fetched_at,time,recipients,header_id,in_reply_to,refs,direction,"
                "thread_key,quoted,signature,unread,via_thread) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET date=excluded.date, subject=excluded.subject, "
                "sender=excluded.sender, sender_name=excluded.sender_name, snippet=excluded.snippet, "
                "body=excluded.body, thread_id=excluded.thread_id, labels=excluded.labels, "
                "fetched_at=excluded.fetched_at, time=excluded.time, recipients=excluded.recipients, "
                "header_id=excluded.header_id, in_reply_to=excluded.in_reply_to, refs=excluded.refs, "
                "direction=excluded.direction, thread_key=excluded.thread_key, quoted=excluded.quoted, "
                "signature=excluded.signature, via_thread=MIN(mail_index.via_thread, excluded.via_thread)",
                rows,
            )
            self.conn.commit()
        for row in rows:
            self._store_hints(row[0], row[4], row[8], row[3])
        self.refresh_threads({row[18] for row in rows})
        new = len([r for r in rows if r[0] not in before])
        if rows:
            self.bump()
        return new

    # threads: one row per conversation, rebuilt from its mails after every change

    def ensure_threads(self) -> None:
        """Give mail stored before threads existed a thread key, once."""
        if self.meta_get("threads_v1"):
            return
        with self.lock:
            rows = self.conn.execute("SELECT * FROM mail_index WHERE thread_key=''").fetchall()
            for row in rows:
                self.conn.execute(
                    "UPDATE mail_index SET thread_key=? WHERE uid=?", (thread_key_of(message_of(row)), row["uid"])
                )
            self.conn.commit()
            keys = {r["thread_key"] for r in self.conn.execute("SELECT DISTINCT thread_key FROM mail_index")}
        self.refresh_threads(keys)
        self.meta_put("threads_v1", "1")

    def refresh_threads(self, keys: set[str]) -> None:
        with self.lock:
            for key in keys:
                rows = self.thread_rows_by_key(key)
                if not rows:
                    self.conn.execute("DELETE FROM mail_threads WHERE thread_key=?", (key,))
                    continue
                last = rows[-1]
                people: list[str] = []
                for row in rows:
                    name = "나" if row["direction"] == "sent" else (row["sender_name"] or row["sender"])
                    if name and name not in people:
                        people.append(name)
                labels: list[str] = []
                for row in rows:
                    for label in json.loads(row["labels"] or "[]"):
                        if label not in labels and label not in SYSTEM_LABEL_IDS:
                            labels.append(label)
                self.conn.execute(
                    "INSERT OR REPLACE INTO mail_threads(tid,thread_key,subject,participants,count,unread,"
                    "has_sent,last_date,last_time,snippet,source_ids,labels) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        thread_uid(key), key, normalize_subject(rows[0]["subject"]) or rows[0]["subject"],
                        json.dumps(people, ensure_ascii=False), len(rows), sum(r["unread"] for r in rows),
                        int(any(r["direction"] == "sent" for r in rows)), last["date"], last["time"],
                        last["snippet"], json.dumps(sorted({r["source_id"] for r in rows})),
                        json.dumps(labels, ensure_ascii=False),
                    ),
                )
            self.conn.commit()

    def thread_rows_by_key(self, key: str) -> list[sqlite3.Row]:
        """The mails of a thread, oldest first; a mail stored by two sources appears once."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM mail_index WHERE thread_key=? ORDER BY date, time, uid", (key,)
            ).fetchall()
        seen: set[str] = set()
        out = []
        for row in rows:
            ident = row["header_id"] or row["msg_id"]
            if ident in seen:
                continue
            seen.add(ident)
            out.append(row)
        return out

    def thread(self, tid: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM mail_threads WHERE tid=?", (tid,)).fetchone()

    def threads(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM mail_threads ORDER BY last_date DESC, last_time DESC, tid"
            ).fetchall()

    def mark_read(self, tid: str, read: bool = True) -> bool:
        """Local read state only; nothing is written to the mail account."""
        row = self.thread(tid)
        if row is None:
            return False
        with self.lock:
            self.conn.execute("UPDATE mail_index SET unread=? WHERE thread_key=?", (int(not read), row["thread_key"]))
            self.conn.commit()
        self.refresh_threads({row["thread_key"]})
        self.bump()
        return True

    def thread_keys(self, source_id: str) -> set[str]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT DISTINCT thread_id FROM mail_index WHERE source_id=? AND thread_id!=''", (source_id,)
            ).fetchall()
        return {r["thread_id"] for r in rows}

    # local labels (stored here only; nothing is written to Gmail)

    def labels(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM mail_labels ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def create_label(self, name: str, color: str = "blue") -> dict:
        cleaned = " ".join(name.split())[:30]
        if not cleaned:
            raise ValueError("라벨 이름을 입력하세요.")
        if color not in LABEL_COLORS:
            raise ValueError("고를 수 없는 색입니다.")
        with self.lock:
            if self.conn.execute("SELECT 1 FROM mail_labels WHERE name=?", (cleaned,)).fetchone():
                raise ValueError("같은 이름의 라벨이 이미 있습니다.")
            if self.conn.execute("SELECT COUNT(*) FROM mail_labels").fetchone()[0] >= MAX_LABELS:
                raise ValueError(f"라벨은 {MAX_LABELS}개까지 만들 수 있습니다.")
            label_id = hashlib.sha1(f"{cleaned}{datetime.datetime.now().isoformat()}".encode()).hexdigest()[:10]
            self.conn.execute("INSERT INTO mail_labels(id,name,color) VALUES(?,?,?)", (label_id, cleaned, color))
            self.conn.commit()
        self.bump()
        return {"id": label_id, "name": cleaned, "color": color}

    def delete_label(self, label_id: str) -> bool:
        with self.lock:
            removed = self.conn.execute("DELETE FROM mail_labels WHERE id=?", (label_id,)).rowcount
            self.conn.execute("DELETE FROM thread_labels WHERE label_id=?", (label_id,))
            self.conn.execute("DELETE FROM source_labels WHERE label_id=?", (label_id,))
            self.conn.commit()
        self.bump()
        return removed > 0

    def set_thread_labels(self, tid: str, label_ids: list[str]) -> bool:
        row = self.thread(tid)
        if row is None:
            return False
        known = {label["id"] for label in self.labels()}
        with self.lock:
            self.conn.execute("DELETE FROM thread_labels WHERE thread_key=?", (row["thread_key"],))
            self.conn.executemany(
                "INSERT INTO thread_labels(thread_key,label_id) VALUES(?,?)",
                [(row["thread_key"], i) for i in dict.fromkeys(label_ids) if i in known],
            )
            self.conn.commit()
        self.bump()
        return True

    def set_source_labels(self, source_id: str, label_ids: list[str]) -> None:
        known = {label["id"] for label in self.labels()}
        with self.lock:
            self.conn.execute("DELETE FROM source_labels WHERE source_id=?", (source_id,))
            self.conn.executemany(
                "INSERT INTO source_labels(source_id,label_id) VALUES(?,?)",
                [(source_id, i) for i in dict.fromkeys(label_ids) if i in known],
            )
            self.conn.commit()
        self.bump()

    def label_links(self) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """(thread key -> label ids, source id -> label ids)."""
        with self.lock:
            threads = self.conn.execute("SELECT * FROM thread_labels").fetchall()
            sources = self.conn.execute("SELECT * FROM source_labels").fetchall()
        by_thread: dict[str, set[str]] = {}
        by_source: dict[str, set[str]] = {}
        for row in threads:
            by_thread.setdefault(row["thread_key"], set()).add(row["label_id"])
        for row in sources:
            by_source.setdefault(row["source_id"], set()).add(row["label_id"])
        return by_thread, by_source

    # date hints (local only; never used by the analysis)

    def _store_hints(self, uid: str, subject: str, body: str, date: str) -> None:
        hints = extract_hints(subject, body, date)
        with self.lock:
            self.conn.execute("DELETE FROM mail_hints WHERE uid=?", (uid,))
            self.conn.executemany(
                "INSERT INTO mail_hints(uid,date,phrase,sentence,kind) VALUES(?,?,?,?,?)",
                [(uid, h["date"], h["phrase"], h["sentence"], h["kind"]) for h in hints],
            )
            self.conn.commit()

    def ensure_hints(self) -> None:
        """Compute hints once for mail indexed before hints existed."""
        if self.meta_get("hints_v1"):
            return
        for row in self.all_rows():
            self._store_hints(row["uid"], row["subject"], row["body"], row["date"])
        self.meta_put("hints_v1", "1")

    def hints(self, uid: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT date, phrase, sentence, kind FROM mail_hints WHERE uid=? ORDER BY date, phrase", (uid,)
            ).fetchall()
        return [dict(r) for r in rows]

    def deadline_uids(self) -> set[str]:
        with self.lock:
            rows = self.conn.execute("SELECT DISTINCT uid FROM mail_hints WHERE kind='deadline'").fetchall()
        return {r["uid"] for r in rows}

    def deadlines_between(self, start: str, end: str) -> list[sqlite3.Row]:
        """Deadline hints dated in the range, with the mail's subject and sender."""
        with self.lock:
            return self.conn.execute(
                "SELECT h.date AS due, h.phrase, h.sentence, m.uid, m.subject, m.sender, m.sender_name, m.date "
                "FROM mail_hints h JOIN mail_index m ON m.uid = h.uid "
                "WHERE h.kind='deadline' AND h.date BETWEEN ? AND ? ORDER BY h.date, m.date, m.uid",
                (start, end),
            ).fetchall()

    def _drop_orphan_hints(self) -> None:
        self.conn.execute("DELETE FROM mail_hints WHERE uid NOT IN (SELECT uid FROM mail_index)")

    def ids(self, source_id: str) -> set[str]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT msg_id FROM mail_index WHERE source_id=?", (source_id,)
            ).fetchall()
        return {r["msg_id"] for r in rows}

    def rows(self, source_id: str) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM mail_index WHERE source_id=? ORDER BY date DESC, uid", (source_id,)
            ).fetchall()

    def get(self, uid: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM mail_index WHERE uid=?", (uid,)).fetchone()

    def set_include(self, uid: str, include: bool = True) -> bool:
        with self.lock:
            changed = self.conn.execute(
                "UPDATE mail_index SET include=? WHERE uid=?", (int(include), uid)
            ).rowcount
            self.conn.commit()
        return changed > 0

    def drop_unlisted(self, source_id: str, since: str, seen: set[str]) -> int:
        """Remove mail of the fetched window that the server no longer lists."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT uid, msg_id FROM mail_index WHERE source_id=? AND date>=? AND via_thread=0",
                (source_id, since),
            ).fetchall()
            gone = [(r["uid"],) for r in rows if r["msg_id"] not in seen]
            self.conn.executemany("DELETE FROM mail_index WHERE uid=?", gone)
            self._drop_orphan_hints()
            self.conn.commit()
        if gone:
            self._resync_threads()
        return len(gone)

    def _resync_threads(self) -> None:
        """Drop thread rows whose mails are gone and rebuild the rest."""
        with self.lock:
            keys = {r["thread_key"] for r in self.conn.execute("SELECT thread_key FROM mail_threads")}
            keys |= {r["thread_key"] for r in self.conn.execute("SELECT DISTINCT thread_key FROM mail_index")}
        self.refresh_threads(keys)
        self.bump()

    def prune(self, oldest: str, source_id: str | None = None) -> int:
        """Remove mail older than the retention date (one source, or all)."""
        query = "DELETE FROM mail_index WHERE date<?"
        params: tuple = (oldest,)
        if source_id is not None:
            query += " AND source_id=?"
            params = (oldest, source_id)
        with self.lock:
            removed = self.conn.execute(query, params).rowcount
            self._drop_orphan_hints()
            self.conn.commit()
        if removed:
            self._resync_threads()
        return removed

    def clear_source(self, source_id: str) -> int:
        with self.lock:
            removed = self.conn.execute(
                "DELETE FROM mail_index WHERE source_id=?", (source_id,)
            ).rowcount
            self._drop_orphan_hints()
            self.conn.commit()
        self._resync_threads()
        return removed

    def count(self, source_id: str | None = None) -> int:
        with self.lock:
            if source_id is None:
                return self.conn.execute("SELECT COUNT(*) FROM mail_index").fetchone()[0]
            return self.conn.execute(
                "SELECT COUNT(*) FROM mail_index WHERE source_id=?", (source_id,)
            ).fetchone()[0]

    def all_rows(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute("SELECT * FROM mail_index ORDER BY date DESC, uid").fetchall()

    # address book

    def contacts(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM contacts ORDER BY team, name, pattern").fetchall()
        return [dict(r) for r in rows]

    def put_contact(self, pattern: str, name: str = "", team: str = "", role: str = "기타", note: str = "") -> dict:
        cleaned = clean_pattern(pattern)
        if cleaned is None:
            raise ValueError("주소는 kim@example.com 또는 @example.com 형식이어야 합니다.")
        if role not in ROLES:
            raise ValueError("역할은 팀원, 교수, 외부, 기타 중 하나여야 합니다.")
        row = {
            "pattern": cleaned, "name": " ".join(name.split())[:40], "team": " ".join(team.split())[:40],
            "role": role, "note": note.strip()[:200],
        }
        with self.lock:
            exists = self.conn.execute("SELECT 1 FROM contacts WHERE pattern=?", (cleaned,)).fetchone()
            total = self.conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
            if exists is None and total >= MAX_CONTACTS:
                raise ValueError(f"주소록에는 {MAX_CONTACTS}명까지 넣을 수 있습니다.")
            self.conn.execute(
                "INSERT OR REPLACE INTO contacts(pattern,name,team,role,note) VALUES(?,?,?,?,?)",
                (row["pattern"], row["name"], row["team"], row["role"], row["note"]),
            )
            self.conn.commit()
        return row

    def delete_contact(self, pattern: str) -> bool:
        with self.lock:
            removed = self.conn.execute(
                "DELETE FROM contacts WHERE pattern=?", (pattern.strip().casefold(),)
            ).rowcount
            self.conn.commit()
        return removed > 0

    def import_csv(self, text: str) -> dict:
        """Rows of name,address,team. A header row is allowed. Bad rows are skipped and counted."""
        added = skipped = 0
        for number, row in enumerate(csv.reader(io.StringIO(text))):
            if not row or not any(cell.strip() for cell in row):
                continue
            if number == 0 and len(row) >= 2 and row[1].strip().casefold() in {"address", "주소", "email", "이메일"}:
                continue
            try:
                name, address = row[0], row[1]
                team = row[2] if len(row) > 2 else ""
                self.put_contact(address, name, team, "팀원" if team.strip() else "기타")
                added += 1
            except (IndexError, ValueError):
                skipped += 1
        return {"added": added, "skipped": skipped}

    def contact_for(self, sender: str) -> dict | None:
        """The most specific contact matching a sender address: exact address first, then domain."""
        address = address_of(sender) if "<" in sender or " " in sender else sender.strip().casefold()
        best: dict | None = None
        for contact in self.contacts():
            if not sender_matches(address, [contact["pattern"]]):
                continue
            if not contact["pattern"].startswith("@"):
                return contact
            if best is None or len(contact["pattern"]) > len(best["pattern"]):
                best = contact
        return best

    def team_patterns(self, team: str) -> list[str]:
        wanted = team.strip().casefold()
        return [c["pattern"] for c in self.contacts() if c["team"].strip().casefold() == wanted]

    def top_senders(self, limit: int = 20) -> list[dict]:
        """Frequent senders in the index that no contact covers yet."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT sender, MAX(sender_name) AS name, COUNT(*) AS n FROM mail_index "
                "WHERE sender != '' GROUP BY sender ORDER BY n DESC, sender LIMIT 500"
            ).fetchall()
        found = []
        for row in rows:
            if self.contact_for(row["sender"]) is not None:
                continue
            found.append({"address": row["sender"], "name": row["name"] or "", "count": row["n"]})
            if len(found) >= limit:
                break
        return found


def _display_name(sender: str) -> str:
    import email.utils

    return email.utils.parseaddr(sender)[0].strip()


def message_of(row: sqlite3.Row) -> MailMessage:
    """An indexed mail as a MailMessage (body is the stored cleaned text)."""
    keys = row.keys()

    def value(name: str, default):
        return row[name] if name in keys else default

    return MailMessage(
        msg_id=row["msg_id"], date=row["date"], subject=row["subject"], sender=row["sender"],
        body=row["body"], thread_id=row["thread_id"], labels=json.loads(row["labels"] or "[]"),
        sender_name=row["sender_name"], time=value("time", ""), to=value("recipients", ""),
        header_id=value("header_id", ""), in_reply_to=value("in_reply_to", ""),
        references=json.loads(value("refs", "[]") or "[]"), direction=value("direction", ""),
        via_thread=bool(value("via_thread", 0)),
    )


# Gmail system labels are not shown as chips.
SYSTEM_LABEL_IDS = frozenset({
    "INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "STARRED", "IMPORTANT", "UNREAD", "CHAT",
    "CATEGORY_PERSONAL", "CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS",
})


def thread_uid(key: str) -> str:
    """Opaque thread id for the web API."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def thread_key_of(message: MailMessage) -> str:
    """Which conversation a mail belongs to.

    Gmail and Outlook give a thread id. Otherwise the first References id (the start of the
    chain), else In-Reply-To, else the mail's own Message-ID, so replies meet their original.
    Without any of these, the normalized subject plus the set of participants.
    """
    if message.thread_id:
        return "t:" + message.thread_id
    root = (message.references[0] if message.references else "") or message.in_reply_to or message.header_id
    if root:
        return "r:" + root.strip().casefold()
    people = sorted({address_of(message.sender)} | {address_of(a) for a in message.to.split(",") if a.strip()})
    basis = normalize_subject(message.subject).casefold() + "|" + ",".join(p for p in people if p)
    return "s:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
