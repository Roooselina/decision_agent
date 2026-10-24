from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

from decision_agent.models import Artifact, ArtifactKind, DecisionScope, ScopeKind


def _load_rows(path: str | Path, required: set[str]) -> list[dict[str, str]]:
    source = Path(path)
    with source.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        missing = required - fields
        if missing:
            raise ValueError(f"{source.name}: 필수 열 누락: {', '.join(sorted(missing))}")
        return [dict(row) for row in reader]


def parse_tasks(
    path: str | Path, scope_parser: Callable[[dict[str, str], str], DecisionScope] | None = None
) -> list[Artifact]:
    rows = _load_rows(path, {"id", "title", "status", "assignee"})
    result = []
    for idx, row in enumerate(rows, start=2):
        scope = (
            scope_parser(row, row["title"])
            if scope_parser
            else DecisionScope(scope_kind=ScopeKind.UNKNOWN)
        )
        result.append(
            Artifact(
                id=row["id"],
                kind=ArtifactKind.TASK,
                text=row["title"],
                source_doc=Path(path).name,
                location=str(idx),
                scope=scope,
                metadata={"status": row["status"], "assignee": row["assignee"]},
            )
        )
    return result


def parse_schedule(
    path: str | Path, scope_parser: Callable[[dict[str, str], str], DecisionScope] | None = None
) -> list[Artifact]:
    rows = _load_rows(path, {"id", "title", "date"})
    result = []
    for idx, row in enumerate(rows, start=2):
        date = row["date"]
        if date:
            parts = date.split("-")
            if len(parts) != 3 or not all(p.isdigit() for p in parts):
                raise ValueError(f"{Path(path).name}: 날짜 형식 오류: {date}")
        scope = (
            scope_parser(row, row["title"])
            if scope_parser
            else DecisionScope(scope_kind=ScopeKind.UNKNOWN)
        )
        result.append(
            Artifact(
                id=row["id"],
                kind=ArtifactKind.SCHEDULE,
                text=row["title"],
                source_doc=Path(path).name,
                location=str(idx),
                scope=scope,
                metadata={"date": date},
            )
        )
    return result
