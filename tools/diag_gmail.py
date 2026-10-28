"""Read-only Gmail diagnostic for Decision Agent.

Run from the repo folder:  python tools/diag_gmail.py [keyword]
                     or:    python tools/diag_gmail.py --source SOURCE_ID

It uses the login the program already saved. It prints which Google account is connected, which
labels exist, what the configured label returns, and whether a mail with the keyword exists in
this account at all. Only counts, label names, dates and subjects are printed to this console.
Nothing is stored, sent, labeled, or marked as read. Tokens and secrets are never printed.

With --source it runs one mail source as the program would and prints the same numbers as the
source panel: the exact Gmail query, how many mails matched, how many would be read, and why
the others are skipped. The local mail index is only read (to know which mails are stored).
"""

from __future__ import annotations

import datetime
import sys

from decision_agent.connectors.base import ConnectorError
from decision_agent.desktop.connector_manager import ConnectorManager
from decision_agent.desktop.settings import load_settings

API = "https://gmail.googleapis.com/gmail/v1/users/me"


def subjects(conn, params: dict, limit: int = 5) -> tuple[int, list[str]]:
    """Return (matched count up to 100, up to `limit` 'date | subject' lines) using headers only."""
    listing = conn._get(f"{API}/messages", {**params, "maxResults": 100})
    refs = listing.get("messages", []) or []
    lines = []
    for ref in refs[:limit]:
        message = conn._get(f"{API}/messages/{ref['id']}", {"format": "metadata"})
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        stamp = int(message.get("internalDate", "0") or 0) / 1000.0
        day = datetime.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M") if stamp else "?"
        lines.append(f"{day} | {headers.get('subject', '(no subject)')}")
    return len(refs), lines


def show(title: str, count: int, lines: list[str]) -> None:
    print(f"{title}: {count}")
    for line in lines:
        print(f"    {line}")


def run(manager: ConnectorManager, keyword: str) -> int:
    conn = manager.instance("gmail")
    label = conn.text_option("label")
    days = conn.int_option("days_back", 14, 1, 365)
    if not label:
        # Newer versions keep the labels in mail sources; check the first Gmail source.
        sources = [s for s in manager.get_settings().mail_sources if s.connector == "gmail" and s.labels]
        if sources:
            label, days = sources[0].labels[0], sources[0].days_back
    print(f"configured label : {label!r}")
    print(f"days_back        : {days}")
    problems = conn.problems()
    if problems:
        print("settings problems:", "; ".join(problems))
        return 1
    try:
        profile = conn._get(f"{API}/profile")
        print(f"connected account: {profile.get('emailAddress')} (total messages {profile.get('messagesTotal')})")
        labels = conn._get(f"{API}/labels").get("labels", [])
        user_labels = sorted(item["name"] for item in labels if item.get("type") == "user")
        print(f"user labels ({len(user_labels)}): {', '.join(user_labels) if user_labels else '(none)'}")
        exact = [item for item in labels if item["name"] == label]
        loose = [item for item in labels if item["name"].strip().lower() == label.strip().lower()]
        similar = [item["name"] for item in labels if label and label in item["name"] and item["name"] != label]
        print(f"label exact match: {'yes' if exact else 'NO'}"
              f"{'' if exact or not loose else ' (case or space difference only)'}"
              f"{'' if not similar else ' | similar names: ' + ', '.join(similar)}")
        count, lines = subjects(conn, {"q": f'label:"{label}" newer_than:{days}d'})
        show(f"query label:\"{label}\" newer_than:{days}d", count, lines)
        if exact:
            count, lines = subjects(conn, {"labelIds": exact[0]["id"]})
            show("by label id (any date)", count, lines)
        count, lines = subjects(conn, {"q": f'"{keyword}" newer_than:7d', "includeSpamTrash": "true"})
        show(f"any mail containing '{keyword}' in the last 7 days (all labels)", count, lines)
    except ConnectorError as exc:
        print("ERROR:", exc)
        return 2
    return 0


def run_source(manager: ConnectorManager, source_id: str, maildb=None) -> int:
    """The source panel's numbers for one source, computed now. Nothing is stored."""
    from decision_agent.desktop import mailsources

    settings = manager.get_settings()
    source = next((s for s in settings.mail_sources if s.id == source_id), None)
    if source is None:
        known = ", ".join(s.id for s in settings.mail_sources) or "(none)"
        print(f"no source {source_id!r}; sources: {known}")
        return 1
    if maildb is None:
        from decision_agent.desktop.maildb import MailDB
        from decision_agent.desktop.paths import mail_db_path

        maildb = MailDB(mail_db_path())
    conn = manager.instance(source.connector)
    problems = conn.problems()
    if problems:
        print("settings problems:", "; ".join(problems))
        return 1
    query = mailsources.query_for(source, settings, maildb)
    print(f"source           : {source.name} ({source.id}), enabled={source.enabled}")
    print(f"query            : {mailsources.query_text(source, query)}")
    try:
        outcome = conn.fetch_source(query, manager.today(), maildb.ids(source.id))
    except ConnectorError as exc:
        print("ERROR:", exc)
        return 2
    counts = mailsources.fetch_counts(outcome, query)
    print(f"matched          : {counts['matched']}")
    print(f"would read       : {counts['loaded']} (of which from the same threads: {counts['thread']})")
    print(f"skipped, stored  : {counts['known']}")
    print(f"skipped, rule    : {counts['excluded']}")
    print(f"skipped, empty   : {counts['empty']}")
    print(f"not a decision   : {counts['not_decision']} (stored, not analysed)")
    print(f"left for later   : {counts['deferred']}")
    print(f"stored now       : {maildb.count(source.id)}")
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--source":
        return run_source(ConnectorManager(load_settings), sys.argv[2])
    keyword = sys.argv[1] if len(sys.argv) > 1 else "포럼"
    return run(ConnectorManager(load_settings), keyword)


if __name__ == "__main__":
    raise SystemExit(main())
