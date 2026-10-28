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

from decision_agent.connectors.base import MailMessage, clean_message_text
from decision_agent.connectors.mailfilter import address_of, sender_matches

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
CREATE TABLE IF NOT EXISTS contacts (
    pattern TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', team TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '기타', note TEXT NOT NULL DEFAULT ''
);
"""

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
            self.conn.commit()

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
        """Delete every indexed mail, fetch memory and contact."""
        with self.lock:
            for table in ("mail_index", "mail_meta", "contacts"):
                self.conn.execute(f"DELETE FROM {table}")
            self.conn.commit()

    # mail index

    def upsert(self, source_id: str, messages: Iterable[MailMessage]) -> int:
        stamp = datetime.datetime.now().replace(microsecond=0).isoformat()
        rows = []
        for m in messages:
            text = "\n".join(clean_message_text(m.body, BODY_LIMIT))
            snippet = " ".join(text.split())[:SNIPPET]
            rows.append((
                mail_uid(source_id, m.msg_id), source_id, m.msg_id, m.date, m.subject,
                address_of(m.sender), m.sender_name or _display_name(m.sender), snippet, text,
                m.thread_id, json.dumps(m.labels, ensure_ascii=False), stamp,
            ))
        with self.lock:
            self.conn.executemany(
                "INSERT INTO mail_index(uid,source_id,msg_id,date,subject,sender,sender_name,snippet,"
                "body,thread_id,labels,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET date=excluded.date, subject=excluded.subject, "
                "sender=excluded.sender, sender_name=excluded.sender_name, snippet=excluded.snippet, "
                "body=excluded.body, thread_id=excluded.thread_id, labels=excluded.labels, "
                "fetched_at=excluded.fetched_at",
                rows,
            )
            self.conn.commit()
        return len(rows)

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
                "SELECT uid, msg_id FROM mail_index WHERE source_id=? AND date>=?", (source_id, since)
            ).fetchall()
            gone = [(r["uid"],) for r in rows if r["msg_id"] not in seen]
            self.conn.executemany("DELETE FROM mail_index WHERE uid=?", gone)
            self.conn.commit()
        return len(gone)

    def prune(self, oldest: str, source_id: str | None = None) -> int:
        """Remove mail older than the retention date (one source, or all)."""
        query = "DELETE FROM mail_index WHERE date<?"
        params: tuple = (oldest,)
        if source_id is not None:
            query += " AND source_id=?"
            params = (oldest, source_id)
        with self.lock:
            removed = self.conn.execute(query, params).rowcount
            self.conn.commit()
        return removed

    def clear_source(self, source_id: str) -> int:
        with self.lock:
            removed = self.conn.execute(
                "DELETE FROM mail_index WHERE source_id=?", (source_id,)
            ).rowcount
            self.conn.commit()
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
    return MailMessage(
        msg_id=row["msg_id"], date=row["date"], subject=row["subject"], sender=row["sender"],
        body=row["body"], thread_id=row["thread_id"], labels=json.loads(row["labels"] or "[]"),
        sender_name=row["sender_name"],
    )
