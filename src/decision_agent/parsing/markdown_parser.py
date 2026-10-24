from __future__ import annotations

import re
from pathlib import Path

from decision_agent.models import Artifact, ArtifactKind, DecisionScope, MeetingItem, ScopeKind

HEADER_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s+회의\s*$")
ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.+?)\s*$")


def parse_meetings(path: str | Path) -> list[MeetingItem]:
    source = Path(path)
    items: list[MeetingItem] = []
    current_date: str | None = None
    for idx, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        header = HEADER_RE.match(line)
        if header:
            current_date = header.group(1)
            continue
        match = ITEM_RE.match(line)
        if match and current_date:
            items.append(
                MeetingItem(
                    date=current_date,
                    text=match.group(1),
                    source_doc=str(source.name),
                    location=str(idx),
                )
            )
    return items


def parse_markdown_artifacts(
    path: str | Path, kind: ArtifactKind, scope_parser=None
) -> list[Artifact]:
    source = Path(path)
    result: list[Artifact] = []
    for idx, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        match = ITEM_RE.match(line)
        if not match:
            continue
        text = match.group(1)
        scope = scope_parser(text) if scope_parser else DecisionScope(scope_kind=ScopeKind.UNKNOWN)
        result.append(
            Artifact(
                id=f"{kind.value}_{idx}",
                kind=kind,
                text=text,
                source_doc=source.name,
                location=str(idx),
                scope=scope,
            )
        )
    return result
