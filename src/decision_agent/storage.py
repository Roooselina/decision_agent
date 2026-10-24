from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from decision_agent.models import Artifact, Decision, DecisionRelation, DecisionStatus


class SQLiteStore:
    """Small SQLite repository used by resolver and UI."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        # Streamlit reruns scripts on different threads, so the connection must be shareable.
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.create_schema()

    def create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, old_id TEXT, new_id TEXT, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, artifact_id TEXT, text TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def save_decision(self, decision: Decision) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO decisions(id,payload) VALUES(?,?)",
            (decision.id, decision.model_dump_json()),
        )
        self.conn.commit()

    def save_artifact(self, artifact: Artifact) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO artifacts(id,payload) VALUES(?,?)",
            (artifact.id, artifact.model_dump_json()),
        )
        self.conn.commit()

    def save_relation(self, relation: DecisionRelation) -> None:
        self.conn.execute(
            "INSERT INTO relations(old_id,new_id,payload) VALUES(?,?,?)",
            (relation.old_id, relation.new_id, relation.model_dump_json()),
        )
        self.conn.commit()

    def list_decisions(self, topic: str | None = None) -> list[Decision]:
        rows = self.conn.execute("SELECT payload FROM decisions").fetchall()
        items = [Decision.model_validate_json(r["payload"]) for r in rows]
        if topic is not None:
            items = [d for d in items if d.topic == topic]
        return sorted(items, key=lambda d: (d.date, d.id))

    def get_decision(self, decision_id: str) -> Decision:
        row = self.conn.execute(
            "SELECT payload FROM decisions WHERE id=?", (decision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return Decision.model_validate_json(row["payload"])

    def update_status(self, decision_id: str, status: DecisionStatus) -> None:
        d = self.get_decision(decision_id)
        d.status = status
        self.save_decision(d)

    def list_relations(self, topic: str | None = None) -> list[DecisionRelation]:
        rows = self.conn.execute("SELECT payload FROM relations ORDER BY id").fetchall()
        rels = [DecisionRelation.model_validate_json(r["payload"]) for r in rows]
        if topic is None:
            return rels
        ids = {d.id for d in self.list_decisions(topic)}
        return [r for r in rels if r.old_id in ids or r.new_id in ids]

    def list_artifacts(self) -> list[Artifact]:
        rows = self.conn.execute("SELECT payload FROM artifacts").fetchall()
        return [Artifact.model_validate_json(r["payload"]) for r in rows]

    def get_artifact(self, artifact_id: str) -> Artifact:
        row = self.conn.execute(
            "SELECT payload FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return Artifact.model_validate_json(row["payload"])

    def add_evidence(self, topic: str, text: str, artifact_id: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO evidence(topic,artifact_id,text) VALUES(?,?,?)",
            (topic, artifact_id, text),
        )
        self.conn.commit()

    def clear_evidence(self, topic: str | None = None) -> None:
        """Remove user-provided evidence so every analysis run starts clean."""
        if topic is None:
            self.conn.execute("DELETE FROM evidence")
        else:
            self.conn.execute("DELETE FROM evidence WHERE topic=?", (topic,))
        self.conn.commit()

    def list_evidence(self, topic: str, artifact_id: str | None = None) -> list[str]:
        if artifact_id is None:
            rows = self.conn.execute("SELECT text FROM evidence WHERE topic=?", (topic,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT text FROM evidence WHERE topic=? AND (artifact_id IS NULL OR artifact_id=?)",
                (topic, artifact_id),
            ).fetchall()
        return [r["text"] for r in rows]

    def export_json(self) -> str:
        return json.dumps(
            {
                "decisions": [d.model_dump(mode="json") for d in self.list_decisions()],
                "relations": [r.model_dump(mode="json") for r in self.list_relations()],
                "artifacts": [a.model_dump(mode="json") for a in self.list_artifacts()],
            },
            ensure_ascii=False,
            indent=2,
        )
