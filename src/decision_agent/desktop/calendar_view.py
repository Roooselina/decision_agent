"""Week view for the home calendar, built from what the analysis already read.

Events come from dated schedule items: connected calendars (Google, Outlook), imported .ics
files, and schedule documents in the watched folders (schedule.csv and the like). Other items
with a date column (tasks, tests) are not calendar events. Each event carries its linked
decision when a finding exists for it. Nothing here reads new files or calls a service, and no
message or event body is returned beyond a short title. Items whose date cannot be read are
left out and logged once (source and raw date only, never the text).
"""

from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Iterable
from pathlib import PurePosixPath

from decision_agent.models import Artifact, ArtifactKind
from decision_agent.sources.dates import parse_date
from decision_agent.sources.model import ParsedDocument

log = logging.getLogger(__name__)

CALENDAR_SOURCES = {"google_calendar": "Google 캘린더", "outlook_calendar": "Outlook 캘린더"}
TITLE_LIMIT = 120
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_LOGGED: set[str] = set()
_LOGGED_LIMIT = 5000


def week_start(day: datetime.date) -> datetime.date:
    """Monday of the week that contains day."""
    return day - datetime.timedelta(days=day.weekday())


def parse_day(value: str | None, today: datetime.date) -> datetime.date:
    if value:
        try:
            return datetime.date.fromisoformat(value[:10])
        except ValueError:
            pass
    return today


def source_of(path: str) -> tuple[str, str]:
    """(kind, display name) for a document path. Connector documents start with "@id/"."""
    if path.startswith("@"):
        connector = path[1:].split("/", 1)[0]
        if connector in CALENDAR_SOURCES:
            return "calendar", CALENDAR_SOURCES[connector]
        if connector == "import":
            name = path.split("/", 1)[1] if "/" in path else path
            return ("ics" if name.lower().endswith(".ics") else "import"), name
        return "connector", connector
    name = PurePosixPath(path).name or path
    return ("ics" if name.lower().endswith(".ics") else "file"), name


def event_times(docs: Iterable[ParsedDocument]) -> dict[tuple[str, str, str], str]:
    """Start times from schedule tables that have a "time" column, keyed by (doc, title, date)."""
    found: dict[tuple[str, str, str], str] = {}
    for doc in docs:
        for section in doc.sections:
            for table in section.tables:
                headers = [h.strip().lower() for h in table.headers]
                if "time" not in headers or "date" not in headers:
                    continue
                t, d = headers.index("time"), headers.index("date")
                title = headers.index("summary") if "summary" in headers else 0
                for row in table.rows:
                    if max(t, d, title) < len(row) and _TIME.match(row[t].strip()):
                        found[(doc.path, row[title].strip(), row[d].strip()[:10])] = row[t].strip()
    return found


def is_calendar_item(artifact: Artifact, source_kind: str) -> bool:
    """Schedule items and anything that came from a calendar (connected or imported .ics)."""
    return artifact.kind == ArtifactKind.SCHEDULE or source_kind in {"calendar", "ics"}


def event_day(raw: str) -> datetime.date | None:
    """The date of a schedule item, or None when it cannot be read."""
    text = raw.strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        pass
    parsed = parse_date(text)
    return datetime.date.fromisoformat(parsed) if parsed else None


def _log_unreadable(artifact: Artifact, raw: str) -> None:
    key = f"{artifact.source_doc}|{artifact.id}|{raw}"
    if key in _LOGGED:
        return
    if len(_LOGGED) >= _LOGGED_LIMIT:
        _LOGGED.clear()
    _LOGGED.add(key)
    log.info(
        "calendar: left out a schedule item with no readable date (source %s, location %s, date %r)",
        source_of(artifact.source_doc)[1], artifact.location, raw[:40],
    )


def _finding_index(findings: Iterable[dict]) -> dict[str, dict]:
    """The most serious finding per artifact id (반영 안 됨 before 확인 필요 before 문제없음)."""
    order = {"INCONSISTENT": 0, "UNCERTAIN": 1, "CONSISTENT": 2}
    best: dict[str, dict] = {}
    for row in findings:
        current = best.get(row["artifact_id"])
        if current is None or order.get(row["label"], 3) < order.get(current["label"], 3):
            best[row["artifact_id"]] = row
    return best


def build_week(
    start: datetime.date,
    artifacts: Iterable[tuple[str, Artifact]],
    findings: Iterable[dict],
    times: dict[tuple[str, str, str], str],
    calendar_connected: bool,
) -> dict:
    """Events of the 7 days from start (a Monday), plus where else events exist.

    artifacts are (root, artifact) pairs from the last build of each watched folder.
    """
    end = start + datetime.timedelta(days=6)
    linked = _finding_index(findings)
    events: list[dict] = []
    all_dates: list[datetime.date] = []
    sources: set[str] = set()
    for root, artifact in artifacts:
        kind, name = source_of(artifact.source_doc)
        if not is_calendar_item(artifact, kind):
            continue
        raw = str(artifact.metadata.get("date") or "")
        day = event_day(raw)
        if day is None:
            _log_unreadable(artifact, raw)
            continue
        sources.add(kind)
        all_dates.append(day)
        if not start <= day <= end:
            continue
        title = artifact.text.strip()
        if len(title) > TITLE_LIMIT:
            title = title[: TITLE_LIMIT - 3] + "..."
        finding = linked.get(artifact.id)
        decision = None
        if finding is not None:
            decision = {
                "root": finding.get("root", root),
                "topic": finding["topic"],
                "topic_label": finding.get("topic_label", finding["topic"]),
                "artifact_id": artifact.id,
                "label": finding["label"],
                "evidence": (finding.get("evidence") or "")[:300],
            }
        events.append(
            {
                "id": f"{root}|{artifact.id}",
                "title": title,
                "date": day.isoformat(),
                "time": times.get((artifact.source_doc, artifact.text.strip(), day.isoformat()), ""),
                "kind": artifact.kind.value,
                "source": kind,
                "source_name": name,
                "decision": decision,
            }
        )
    events.sort(key=lambda e: (e["date"], e["time"] or "99:99", e["title"]))
    nearest = None
    outside = [d for d in all_dates if not start <= d <= end]
    if not events and outside:
        middle = start + datetime.timedelta(days=3)
        nearest = week_start(min(outside, key=lambda d: abs((d - middle).days))).isoformat()
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_connected": calendar_connected,
        "has_events": bool(all_dates),
        "sources": sorted(sources),
        "nearest_week": nearest,
        "events": events,
    }


__all__ = ["build_week", "event_day", "event_times", "is_calendar_item", "parse_day", "source_of", "week_start"]
