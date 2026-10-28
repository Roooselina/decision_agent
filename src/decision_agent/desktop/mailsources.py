"""Mail sources: fetching each named source into the local mail index and the analysis.

The account login stays one per connector. Every fetched mail that matches the source's filter
is kept in the local mail index (MailDB): it can be shown and searched without any model.
The documents the engine analyses are rebuilt from the index after each fetch, chosen by the
source's analysis policy, so changing the policy, the address book or a per-mail override
needs no new fetch. Each source keeps its documents under @<connector>.<source id>/.
Nothing here logs subjects, bodies or addresses; only counts.
"""

from __future__ import annotations

import datetime
import hashlib
import json

from decision_agent.connectors.base import FetchOutcome, clean_message_text, mail_document
from decision_agent.connectors.mailfilter import (
    MailFilter,
    MailQuery,
    collect_mail,
    looks_like_decision,
)
from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.maildb import MailDB, message_of
from decision_agent.desktop.settings import AppSettings, MailSource
from decision_agent.desktop.state import StateDB
from decision_agent.sources.model import ParsedDocument

# Message ids that were listed but did not match the source's filter, so their bodies are not
# requested again on the next fetch.
MAX_SKIPPED = 4000
# An all-mail source lists up to this many ids per fetch (ids only; bodies obey the cycle cap).
ALL_MAIL_LISTING = 4000
PURPOSE_LABELS = {
    "decision": "결정 근거",
    "task": "할 일 요청",
    "requirement": "요구사항 변경",
    "schedule": "일정",
}
ANALYSIS_LABELS = {
    "off": "분석 안 함",
    "address_book": "주소록에 팀이 있는 사람의 메일만",
    "decision_like": "결정 표현이 있는 메일만",
    "all": "모든 메일",
}
CONNECTOR_LABELS = {"gmail": "Gmail", "outlook_mail": "Outlook 메일", "imap_mail": "메일 (IMAP)"}
TEAM_PREFIX = "team:"


def source_key(source: MailSource) -> str:
    """External source name: documents live under @<connector>.<source id>/."""
    return f"{source.connector}.{source.id}"


def status_key(source: MailSource) -> str:
    return f"connector_status:{source.connector}:{source.id}"


def expand_senders(senders: list[str], maildb: MailDB | None) -> list[str]:
    """team:<name> becomes the addresses and domains of that team in the address book."""
    expanded: list[str] = []
    for entry in senders:
        if entry.casefold().startswith(TEAM_PREFIX):
            if maildb is not None:
                expanded.extend(maildb.team_patterns(entry[len(TEAM_PREFIX):]))
        else:
            expanded.append(entry)
    return list(dict.fromkeys(expanded))


def query_for(
    source: MailSource,
    settings: AppSettings,
    maildb: MailDB | None = None,
    since: str | None = None,
) -> MailQuery:
    all_mail = source.scope == "all_mail"
    labels = list(source.labels) or (["INBOX"] if all_mail else [])
    return MailQuery(
        source_id=source.id,
        name=source.name,
        purpose=source.purpose,
        team=source.team,
        labels=labels,
        senders=expand_senders(source.senders, maildb),
        subject_keywords=list(source.subject_keywords),
        exclude_senders=expand_senders(source.exclude_senders, maildb),
        exclude_subject_keywords=list(source.exclude_subject_keywords),
        gmail_query=source.gmail_query,
        days_back=source.days_back,
        max_messages=ALL_MAIL_LISTING if all_mail else source.max_messages,
        workers=max(1, min(4, settings.max_parallel_calls)),
        prefilter=settings.mail_prefilter,
        folder_word="라벨" if source.connector == "gmail" else "폴더",
        since=since,
        body_cap=settings.mail_cycle_cap,
        include_sent=source.include_sent and source.connector == "gmail",
    )


def fetch_fingerprint(source: MailSource) -> str:
    """The fields that decide which mails are listed. A change empties the index of the source."""
    data = source.model_dump(include={
        "connector", "labels", "senders", "subject_keywords", "exclude_senders",
        "exclude_subject_keywords", "gmail_query", "scope",
    })
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def filter_summary(source: MailSource) -> str:
    """One line describing the filter (counts for senders, so no address is repeated)."""
    word = "라벨" if source.connector == "gmail" else "폴더"
    parts = []
    if source.scope == "all_mail":
        parts.append(f"전체 메일 · {word} " + (", ".join(source.labels) or "INBOX"))
    elif source.labels:
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
    parts.append("AI 분석: " + ANALYSIS_LABELS[source.effective_analysis])
    return " · ".join(parts)


def known_ids(maildb: MailDB, source: MailSource) -> set[str]:
    """Message ids that need no body request: indexed mails plus earlier non-matching ones.

    When the listing rules of the source changed, its index is emptied first so every mail is
    read again under the new rules.
    """
    current = fetch_fingerprint(source)
    if maildb.meta_get(f"fetch_fp:{source.id}") != current:
        forget_source(maildb, source)
        maildb.meta_put(f"fetch_fp:{source.id}", current)
        return set()
    try:
        skipped = set(json.loads(maildb.meta_get(f"skipped:{source.id}") or "[]"))
    except ValueError:
        skipped = set()
    return maildb.ids(source.id) | skipped


def since_for(maildb: MailDB, source: MailSource) -> str | None:
    """All-mail sources read only new mail after the first fetch (one day of overlap)."""
    if source.scope != "all_mail":
        return None
    last = maildb.meta_get(f"last_fetch:{source.id}")
    if not last:
        return None
    return (datetime.date.fromisoformat(last) - datetime.timedelta(days=1)).isoformat()


def forget_source(maildb: MailDB, source: MailSource) -> None:
    """Delete the source's indexed mail and fetch memory ("메일 지우기")."""
    maildb.clear_source(source.id)
    for key in ("skipped", "last_fetch", "fetch_fp"):
        maildb.meta_delete(f"{key}:{source.id}")


def remove_documents(engine: ProjectEngine, db: StateDB, source: MailSource) -> bool:
    """Drop the source's documents from the analysis and its status. The index stays."""
    changed = engine.set_external(source_key(source), {})
    db.meta_delete(status_key(source))
    return changed


def build_documents(
    source: MailSource,
    rows: list,
    settings: AppSettings,
    maildb: MailDB,
    stats: dict[str, int],
) -> dict[str, ParsedDocument]:
    """Documents for the indexed mails the analysis policy selects (plus per-mail overrides)."""
    policy = source.effective_analysis
    query = query_for(source, settings, maildb)
    selected = []
    forced = []
    for row in rows:
        message = message_of(row)
        if row["include"]:
            forced.append(message)
            continue
        if policy == "off":
            stats["stored_only"] = stats.get("stored_only", 0) + 1
            continue
        if policy == "address_book":
            contact = maildb.contact_for(message.sender)
            if not (contact and contact["team"]):
                stats["stored_only"] = stats.get("stored_only", 0) + 1
                continue
        if policy == "decision_like":
            lines = clean_message_text(message.body) or [message.subject]
            if not looks_like_decision(lines):
                stats["skipped_prefilter"] = stats.get("skipped_prefilter", 0) + 1
                continue
        selected.append(message)
    docs = collect_mail(selected, query, stats)
    for message in forced:
        made = mail_document(message, stats, query.kind)
        if made is not None:
            name, doc = made
            doc.meta.update({"source_id": source.id, "source_name": source.name, "included": "1"})
            if source.team:
                doc.meta["team"] = source.team
            docs[name] = doc
    return docs


def rebuild(engine: ProjectEngine, maildb: MailDB, source: MailSource, settings: AppSettings) -> bool:
    """Set the source's documents from its index. Returns True when they changed."""
    if not source.enabled:
        return engine.set_external(source_key(source), {})
    docs = build_documents(source, maildb.rows(source.id), settings, maildb, {})
    return engine.set_external(source_key(source), docs)


def apply_fetch(
    engine: ProjectEngine,
    maildb: MailDB,
    source: MailSource,
    outcome: FetchOutcome,
    query: MailQuery,
    settings: AppSettings,
    today: datetime.date,
    accounts: list[str] | None = None,
    unread: bool = False,
) -> dict[str, int]:
    """Index one fetch, apply retention, rebuild the documents. Returns the counts for status.

    Mail read through its thread (my own replies) is kept whatever the source's filter says.
    accounts are the connected addresses, so my own mail is marked as sent; unread marks new
    arrivals (after the first fetch) as not read in the mail view.
    """
    rules = MailFilter.of(query)
    matching = [m for m in outcome.messages if m.via_thread or rules.matches(m)]
    unmatched = [m.msg_id for m in outcome.messages if not (m.via_thread or rules.matches(m))]
    new = maildb.upsert(source.id, matching, accounts or [], unread)
    try:
        skipped = json.loads(maildb.meta_get(f"skipped:{source.id}") or "[]")
    except ValueError:
        skipped = []
    seen = set(outcome.seen_ids or [])
    skipped = [i for i in skipped if i in seen] + unmatched
    maildb.meta_put(f"skipped:{source.id}", json.dumps(skipped[-MAX_SKIPPED:]))
    maildb.prune((today - datetime.timedelta(days=settings.mail_index_days)).isoformat(), source.id)
    deferred = outcome.stats.get("deferred", 0)
    capped = deferred or outcome.stats.get("matched", 0) >= query.max_messages
    if not capped:
        # Mail of the listed window that the server no longer lists was deleted or unlabeled.
        maildb.drop_unlisted(source.id, query.listing_start(today).isoformat(), seen)
    if not deferred:
        maildb.meta_put(f"last_fetch:{source.id}", today.isoformat())
    stats = {key: outcome.stats.get(key, 0) for key in ("matched", "known", "deferred")}
    stats["new"] = new
    stats["skipped_filtered"] = len(unmatched)
    stats["skipped_empty"] = outcome.stats.get("skipped_empty", 0)
    fresh = {m.msg_id for m in matching}
    build_documents(source, [r for r in maildb.rows(source.id) if r["msg_id"] in fresh], settings, maildb, stats)
    stats["docs_changed"] = int(rebuild(engine, maildb, source, settings))
    return stats


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
    "ANALYSIS_LABELS", "CONNECTOR_LABELS", "PURPOSE_LABELS", "apply_fetch", "build_documents",
    "expand_senders", "failure", "filter_summary", "forget_source", "known_ids", "query_for",
    "read_status", "rebuild", "remove_documents", "save_status", "since_for", "source_key",
    "status_key",
]
