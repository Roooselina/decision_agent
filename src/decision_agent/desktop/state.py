from __future__ import annotations

import datetime
import hashlib
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER, sha256 TEXT, fmt TEXT,
    status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', payload TEXT, parsed_at TEXT
);
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topics (
    topic TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, decision_fp TEXT NOT NULL,
    state TEXT NOT NULL, checked_at TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
    judge TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS findings (
    topic TEXT NOT NULL, artifact_id TEXT NOT NULL, kind TEXT, text TEXT, source_doc TEXT,
    location TEXT, label TEXT NOT NULL, confidence REAL, evidence TEXT, action TEXT,
    decision_id TEXT, owner TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', snooze_until TEXT,
    first_seen TEXT, last_seen TEXT, notified INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (topic, artifact_id)
);
CREATE TABLE IF NOT EXISTS questions (
    topic TEXT PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, text TEXT NOT NULL, decision_fp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

CACHE_LIMIT = 20000


def now_iso() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class FindingInput:
    artifact_id: str
    kind: str
    text: str
    source_doc: str
    location: str
    label: str
    confidence: float
    evidence: str
    action: str
    decision_id: str | None
    owner: str = ""


class StateDB:
    """Per-folder SQLite state: file index, LLM cache, topic fingerprints, findings, questions."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            if str(path) != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self) -> None:
        """Add columns that older databases lack, so an installed copy keeps its history."""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(topics)")}
        if "judge" not in columns:
            self.conn.execute("ALTER TABLE topics ADD COLUMN judge TEXT NOT NULL DEFAULT ''")
        found = {row["name"] for row in self.conn.execute("PRAGMA table_info(findings)")}
        if "owner" not in found:
            self.conn.execute("ALTER TABLE findings ADD COLUMN owner TEXT NOT NULL DEFAULT ''")

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # file index
    def upsert_file(
        self,
        path: str,
        mtime_ns: int,
        size: int,
        sha: str,
        fmt: str,
        status: str,
        message: str = "",
        payload: str | None = None,
    ) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO files(path,mtime_ns,size,sha256,fmt,status,message,payload,parsed_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (path, mtime_ns, size, sha, fmt, status, message, payload, now_iso()),
            )
            self.conn.commit()

    def get_file(self, path: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()

    def touch_file(self, path: str, mtime_ns: int) -> None:
        with self.lock:
            self.conn.execute("UPDATE files SET mtime_ns=? WHERE path=?", (mtime_ns, path))
            self.conn.commit()

    def list_files(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute("SELECT * FROM files ORDER BY path").fetchall()

    def delete_files(self, paths: Iterable[str]) -> int:
        count = 0
        with self.lock:
            for path in paths:
                count += self.conn.execute("DELETE FROM files WHERE path=?", (path,)).rowcount
            self.conn.commit()
        return count

    # LLM cache
    def cache_get(self, key: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        return row["response"] if row else None

    def cache_put(self, key: str, response: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO llm_cache(key,response,created_at) VALUES(?,?,?)",
                (key, response, now_iso()),
            )
            count = self.conn.execute("SELECT COUNT(*) AS n FROM llm_cache").fetchone()["n"]
            if count > CACHE_LIMIT:
                self.conn.execute(
                    "DELETE FROM llm_cache WHERE key IN "
                    "(SELECT key FROM llm_cache ORDER BY created_at ASC LIMIT ?)",
                    (count - CACHE_LIMIT + CACHE_LIMIT // 10,),
                )
            self.conn.commit()

    # meta
    def meta_get(self, key: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_put(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
            self.conn.commit()

    def meta_delete(self, key: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM meta WHERE key=?", (key,))
            self.conn.commit()

    # topics
    def topic_get(self, topic: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM topics WHERE topic=?", (topic,)).fetchone()

    def topic_put(
        self,
        topic: str,
        fingerprint: str,
        decision_fp: str,
        state: str,
        summary: str = "",
        judge: str = "",
    ) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO topics(topic,fingerprint,decision_fp,state,checked_at,summary,judge)"
                " VALUES(?,?,?,?,?,?,?)",
                (topic, fingerprint, decision_fp, state, now_iso(), summary, judge),
            )
            self.conn.commit()

    def list_topics(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute("SELECT * FROM topics ORDER BY topic").fetchall()

    def drop_topic(self, topic: str) -> None:
        with self.lock:
            for table in ("topics", "findings", "questions", "answers"):
                self.conn.execute(f"DELETE FROM {table} WHERE topic=?", (topic,))
            self.conn.commit()

    # findings
    def replace_findings(self, topic: str, items: list[FindingInput]) -> list[str]:
        """Store the latest results for a topic.

        Returns the artifact ids that are new or whose label changed, so callers can notify only
        about changes. A dismissed finding stays dismissed while its label is unchanged.
        """
        changed: list[str] = []
        stamp = now_iso()
        with self.lock:
            old = {
                r["artifact_id"]: r
                for r in self.conn.execute("SELECT * FROM findings WHERE topic=?", (topic,))
            }
            keep = {item.artifact_id for item in items}
            for artifact_id in set(old) - keep:
                self.conn.execute(
                    "DELETE FROM findings WHERE topic=? AND artifact_id=?", (topic, artifact_id)
                )
            for item in items:
                previous = old.get(item.artifact_id)
                if previous is None or previous["label"] != item.label:
                    changed.append(item.artifact_id)
                    status, notified, first = "open", 0, stamp
                else:
                    status, notified, first = previous["status"], previous["notified"], previous["first_seen"]
                snooze = previous["snooze_until"] if previous is not None and status == "snoozed" else None
                self.conn.execute(
                    "INSERT OR REPLACE INTO findings(topic,artifact_id,kind,text,source_doc,location,"
                    "label,confidence,evidence,action,decision_id,owner,status,snooze_until,"
                    "first_seen,last_seen,notified) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        topic, item.artifact_id, item.kind, item.text, item.source_doc,
                        item.location, item.label, item.confidence, item.evidence, item.action,
                        item.decision_id, item.owner, status, snooze, first, stamp, notified,
                    ),
                )
            self.conn.commit()
        return changed

    def list_findings(self, include_hidden: bool = False) -> list[sqlite3.Row]:
        today = now_iso()
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM findings ORDER BY CASE label WHEN 'INCONSISTENT' THEN 0 "
                "WHEN 'UNCERTAIN' THEN 1 ELSE 2 END, topic, artifact_id"
            ).fetchall()
        if include_hidden:
            return rows
        visible = []
        for row in rows:
            if row["status"] in {"dismissed", "false_positive"}:
                continue
            if row["status"] == "snoozed" and row["snooze_until"] and row["snooze_until"] > today:
                continue
            visible.append(row)
        return visible

    def set_finding_status(
        self, topic: str, artifact_id: str, status: str, snooze_until: str | None = None
    ) -> bool:
        with self.lock:
            cursor = self.conn.execute(
                "UPDATE findings SET status=?, snooze_until=? WHERE topic=? AND artifact_id=?",
                (status, snooze_until, topic, artifact_id),
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def unnotified_findings(self) -> list[sqlite3.Row]:
        rows = self.list_findings()
        return [r for r in rows if r["label"] == "INCONSISTENT" and not r["notified"]]

    def mark_notified(self, keys: Iterable[tuple[str, str]]) -> None:
        with self.lock:
            for topic, artifact_id in keys:
                self.conn.execute(
                    "UPDATE findings SET notified=1 WHERE topic=? AND artifact_id=?",
                    (topic, artifact_id),
                )
            self.conn.commit()

    # questions and answers
    def set_question(self, topic: str, text: str) -> bool:
        """Store a pending question. Returns True when it is new or changed."""
        with self.lock:
            row = self.conn.execute("SELECT text FROM questions WHERE topic=?", (topic,)).fetchone()
            if row is not None and row["text"] == text:
                return False
            self.conn.execute(
                "INSERT OR REPLACE INTO questions(topic,text,created_at,notified) VALUES(?,?,?,0)",
                (topic, text, now_iso()),
            )
            self.conn.commit()
            return True

    def clear_question(self, topic: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM questions WHERE topic=?", (topic,))
            self.conn.commit()

    def list_questions(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute("SELECT * FROM questions ORDER BY created_at").fetchall()

    def mark_question_notified(self, topic: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE questions SET notified=1 WHERE topic=?", (topic,))
            self.conn.commit()

    def add_answer(self, topic: str, text: str, decision_fp: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO answers(topic,text,decision_fp) VALUES(?,?,?)", (topic, text, decision_fp)
            )
            self.conn.commit()

    def get_answers(self, topic: str, decision_fp: str) -> list[str]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT text FROM answers WHERE topic=? AND decision_fp=? ORDER BY id",
                (topic, decision_fp),
            ).fetchall()
        return [r["text"] for r in rows]

    def clear_stale_answers(self, topic: str, decision_fp: str) -> None:
        with self.lock:
            self.conn.execute(
                "DELETE FROM answers WHERE topic=? AND decision_fp<>?", (topic, decision_fp)
            )
            self.conn.commit()


__all__ = ["CACHE_LIMIT", "FindingInput", "StateDB", "now_iso", "sha256_file"]
