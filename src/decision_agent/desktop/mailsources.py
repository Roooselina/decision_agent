"""Mail sources: fetching each named source into the analysis of its project folder.

The account login stays one per connector. Each source keeps its own documents under the
external path prefix @<connector>.<source id>/, so sources are fetched, kept and removed
independently. Nothing here logs subjects, bodies or addresses; only counts.
"""

from __future__ import annotations

import datetime
import hashlib
import json

from decision_agent.connectors.base import FetchOutcome
from decision_agent.connectors.mailfilter import MailQuery
from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.settings import AppSettings, MailSource
from decision_agent.desktop.state import StateDB

KEEP_DAYS = 180
# Message ids of a source that were listed but produced no document (filtered, no decision
# wording, unreadable), so their bodies are not requested again on the next fetch.
MAX_SKIPPED = 2000
PURPOSE_LABELS = {
    "decision": "결정 근거",
    "task": "할 일 요청",
    "requirement": "요구사항 변경",
    "schedule": "일정",
}
CONNECTOR_LABELS = {"gmail": "Gmail", "outlook_mail": "Outlook 메일", "imap_mail": "메일 (IMAP)"}


def source_key(source: MailSource) -> str:
    """External source name: documents live under @<connector>.<source id>/."""
    return f"{source.connector}.{source.id}"


def status_key(source: MailSource) -> str:
    return f"connector_status:{source.connector}:{source.id}"


def _fingerprint_key(source: MailSource) -> str:
    return f"mail_source_fp:{source_key(source)}"


def _skipped_key(source: MailSource) -> str:
    return f"mail_skipped:{source_key(source)}"


def query_for(source: MailSource, settings: AppSettings) -> MailQuery:
    return MailQuery(
        source_id=source.id,
        name=source.name,
        purpose=source.purpose,
        team=source.team,
        labels=list(source.labels),
        senders=list(source.senders),
        subject_keywords=list(source.subject_keywords),
        exclude_senders=list(source.exclude_senders),
        exclude_subject_keywords=list(source.exclude_subject_keywords),
        gmail_query=source.gmail_query,
        days_back=source.days_back,
        max_messages=source.max_messages,
        workers=max(1, min(4, settings.max_parallel_calls)),
        prefilter=settings.mail_prefilter,
        folder_word="라벨" if source.connector == "gmail" else "폴더",
    )


def fingerprint(source: MailSource, settings: AppSettings) -> str:
    """Everything that decides which mails become which documents. A change refetches all."""
    data = source.model_dump(exclude={"enabled", "days_back", "max_messages", "target"})
    data["prefilter"] = settings.mail_prefilter
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def filter_summary(source: MailSource) -> str:
    """One line describing the filter (counts for senders, so no address is repeated)."""
    word = "라벨" if source.connector == "gmail" else "폴더"
    parts = []
    if source.labels:
        parts.append(f"{word} " + ", ".join(source.labels))
    if source.senders:
        parts.append(f"보낸 사람 {len(source.senders)}개")
    if source.subject_keywords:
        parts.append("제목 " + ", ".join(source.subject_keywords))
    if source.exclude_senders or source.exclude_subject_keywords:
        parts.append(f"제외 {len(source.exclude_senders) + len(source.exclude_subject_keywords)}개")
    if source.gmail_query:
        parts.append("검색식 사용")
    parts.append(f"최근 {source.days_back}일")
    return " · ".join(parts)


def remove_documents(engine: ProjectEngine, db: StateDB, source: MailSource) -> bool:
    """Drop every document, status and fetch memory of one source. Returns True on change."""
    changed = engine.set_external(source_key(source), {})
    for key in (status_key(source), _fingerprint_key(source), _skipped_key(source)):
        db.meta_delete(key)
    return changed


def known_ids(engine: ProjectEngine, db: StateDB, source: MailSource, settings: AppSettings) -> set[str]:
    """Message ids that need no body request: stored documents plus earlier skipped mails.

    When the source's filter or purpose changed, its documents are dropped first so every mail
    is read again under the new rules.
    """
    current = fingerprint(source, settings)
    if db.meta_get(_fingerprint_key(source)) != current:
        remove_documents(engine, db, source)
        db.meta_put(_fingerprint_key(source), current)
        return set()
    stored = {str(meta.get("msg_id")) for meta in engine.external_meta(source_key(source)).values() if meta.get("msg_id")}
    try:
        skipped = set(json.loads(db.meta_get(_skipped_key(source)) or "[]"))
    except ValueError:
        skipped = set()
    return stored | skipped


def store_outcome(
    engine: ProjectEngine,
    db: StateDB,
    source: MailSource,
    outcome: FetchOutcome,
    today: datetime.date,
) -> bool:
    """Merge one fetch into the engine. Returns True when the documents changed."""
    seen = set(outcome.seen_ids or [])
    capped = outcome.stats.get("matched", 0) >= source.max_messages
    # When the listing hit max_messages, older mail inside the window was not listed because
    # of the cap, not because it is gone, so only the keep_days rule removes anything.
    since = None if capped else (today - datetime.timedelta(days=source.days_back)).isoformat()
    changed = engine.merge_external(
        source_key(source), outcome.docs, since, KEEP_DAYS, seen=seen, today=today
    )
    stored = {
        str(meta.get("msg_id")) for meta in engine.external_meta(source_key(source)).values()
    }
    skipped = sorted(seen - stored)[:MAX_SKIPPED]
    db.meta_put(_skipped_key(source), json.dumps(skipped))
    return changed


def failure(message: str) -> dict:
    return {
        "at": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "ok": False, "message": message, "summary": "",
    }


def save_status(db: StateDB, source: MailSource, status: dict) -> None:
    db.meta_put(status_key(source), json.dumps(status, ensure_ascii=False))


def read_status(db: StateDB | None, source: MailSource) -> dict | None:
    if db is None:
        return None
    raw = db.meta_get(status_key(source))
    return json.loads(raw) if raw else None


__all__ = [
    "CONNECTOR_LABELS", "KEEP_DAYS", "PURPOSE_LABELS", "failure", "filter_summary",
    "fingerprint", "known_ids", "query_for", "read_status", "remove_documents", "save_status",
    "source_key", "status_key", "store_outcome",
]
