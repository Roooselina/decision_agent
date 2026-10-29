"""`state --json`: what the app has stored, read without changing anything.

Every database is opened with mode=ro, so this works while the app runs and never writes.
Tokens, keys and passwords are never read into the output: connections report only whether a
login exists and which scopes it was granted.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from urllib.request import pathname2url

from decision_agent.desktop.about import about
from decision_agent.desktop.paths import mail_db_path, settings_path, state_db_path
from decision_agent.desktop.settings import AppSettings

# Settings keys whose values are never printed, even though none should hold a secret.
_SECRET_KEY = re.compile(r"secret|token|password|passwd|api_key|apikey|credential", re.IGNORECASE)
REDACTED = "(숨김)"


def open_readonly(path: Path) -> sqlite3.Connection | None:
    """Open an SQLite file read-only, or None when it does not exist."""
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{pathname2url(str(path))}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def read_settings(path: Path | None = None) -> AppSettings:
    """Parse the settings file without the repair step load_settings does (it renames files)."""
    target = path or settings_path()
    try:
        return AppSettings.model_validate_json(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppSettings()


def redact(value):
    """Copy of a settings structure with every secret-looking key's value hidden."""
    if isinstance(value, dict):
        return {k: REDACTED if _SECRET_KEY.search(str(k)) and v not in ("", None) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _meta(conn: sqlite3.Connection, key: str) -> dict | str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except ValueError:
        return row["value"]


def folder_state(conn: sqlite3.Connection) -> dict:
    """The stored part of the folder state (running and queued are known only to the app)."""
    if _meta(conn, "folder_paused") in (1, "1"):
        return {"state": "paused", "label": "일시 중지"}
    if _meta(conn, "pending_confirm"):
        return {"state": "awaiting_confirm", "label": "확인 대기"}
    failure = _meta(conn, "last_failure")
    if isinstance(failure, dict):
        kind = "failed" if failure.get("fatal") else "partial" if failure.get("partial") else "retrying"
        return {"state": kind, "label": {"failed": "실패", "partial": "일부만 점검", "retrying": "실패, 다시 시도 중"}[kind]}
    record = _meta(conn, "completion")
    if not isinstance(record, dict):
        return {"state": "incomplete", "label": "점검 전"}
    if record.get("verified"):
        return {"state": "verified", "label": "검사 완료"}
    return {"state": "demo", "label": "데모로만 점검"}


def folder_dump(root: str) -> dict:
    path = state_db_path(Path(root))
    item: dict = {"root": root, "exists": Path(root).is_dir(), "db": str(path)}
    conn = open_readonly(path)
    if conn is None:
        item.update(state="incomplete", label="점검 전", findings={}, open_findings={}, files={}, last_cycle=None)
        return item
    try:
        item.update(folder_state(conn))
        item["findings"] = {r["label"]: r["n"] for r in conn.execute(
            "SELECT label, COUNT(*) AS n FROM findings GROUP BY label ORDER BY label")}
        item["open_findings"] = {r["label"]: r["n"] for r in conn.execute(
            "SELECT label, COUNT(*) AS n FROM findings WHERE status='open' GROUP BY label ORDER BY label")}
        item["files"] = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM files GROUP BY status ORDER BY status")}
        record = _meta(conn, "completion")
        item["last_cycle"] = None if not isinstance(record, dict) else {
            "at": record.get("at"),
            "provider_id": record.get("provider_id"),
            "verified": record.get("verified"),
            "requests": record.get("llm_calls", 0),
            "reused_lines": record.get("reused_lines", 0),
            "judged_lines": record.get("judged_lines", 0),
            "topics": record.get("topics", 0),
            "topics_rechecked": record.get("topics_rechecked", 0),
            "phase_seconds": record.get("phase_seconds", {}),
            "cache_hits": record.get("cache_hits", 0),
            "cache_misses": record.get("cache_misses", 0),
        }
    finally:
        conn.close()
    return item


def mail_dump(settings: AppSettings) -> dict:
    """Stored mails per connection and per mail source."""
    connector_of = {s.id: s.connector for s in settings.mail_sources}
    conn = open_readonly(mail_db_path())
    if conn is None:
        return {"total": 0, "by_connection": {}, "by_source": {}}
    try:
        rows = conn.execute("SELECT source_id, COUNT(*) AS n FROM mail_index GROUP BY source_id ORDER BY source_id").fetchall()
    finally:
        conn.close()
    by_source = {r["source_id"]: r["n"] for r in rows}
    by_connection: dict[str, int] = {}
    for source_id, count in by_source.items():
        name = connector_of.get(source_id, "gmail" if source_id.startswith("gmail") else "other")
        by_connection[name] = by_connection.get(name, 0) + count
    return {"total": sum(by_source.values()), "by_connection": by_connection, "by_source": by_source}


def connections_dump(settings: AppSettings, secrets=None) -> list[dict]:
    """Connected or not, scopes granted, send granted. Token values are never part of this."""
    from decision_agent.connectors.google import GMAIL_SEND_SCOPE
    from decision_agent.connectors.registry import CONNECTORS
    from decision_agent.desktop.connector_manager import ConnectorManager

    manager = ConnectorManager(lambda: settings, secrets=secrets)
    rows = []
    for connector_id, saved in settings.connectors.items():
        if connector_id not in CONNECTORS:
            continue
        cls = CONNECTORS[connector_id]
        row: dict = {"id": connector_id, "enabled": saved.enabled, "read_only": connector_id != "gmail"}
        try:
            connector = manager.instance(connector_id)
            if cls.uses_oauth:
                row["connected"] = manager.oauth.connected(connector_id, connector.oauth_app())
                scopes = manager.oauth.granted_scopes(connector_id)
                row["scopes"] = sorted(scopes) if scopes is not None else None
                row["send_granted"] = connector_id == "gmail" and GMAIL_SEND_SCOPE in (scopes or [])
            else:
                row["connected"] = all(bool(manager.secrets.get(connector.secret_key(s.name))) for s in cls.secrets)
        except Exception as exc:  # noqa: BLE001 - a broken credential store must not stop the dump
            row["error"] = type(exc).__name__
        rows.append(row)
    return rows


def dump_state(secrets=None, settings_loader: Callable[[], AppSettings] = read_settings) -> dict:
    settings = settings_loader()
    return {
        "about": about(),
        "folders": [folder_dump(root) for root in settings.allowed_folders],
        "mail": mail_dump(settings),
        "connections": connections_dump(settings, secrets),
        "settings": redact(settings.model_dump(mode="json")),
    }
