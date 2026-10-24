from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from decision_agent.models import ArtifactKind, DecisionScope, ProjectConfig, ScopeKind
from decision_agent.parsing.csv_parser import parse_schedule, parse_tasks
from decision_agent.parsing.markdown_parser import parse_markdown_artifacts, parse_meetings
from decision_agent.scope import normalize_scope


def _heuristic_raw_scope(text: str, current_release: str | None) -> dict[str, str | None]:
    lowered = text.lower()
    raw: dict[str, str | None] = {
        "release": None,
        "module": None,
        "user_group": None,
        "environment": None,
    }
    if "mvp" in lowered or "이번 버전" in text or "시연 버전" in text:
        raw["release"] = current_release or "mvp"
    elif "production" in lowered or "정식" in text:
        raw["release"] = "production"
    if "로그인" in text:
        raw["module"] = "login"
    if "관리자" in text:
        raw["user_group"] = "admin"
    return raw


def infer_scope_from_text(
    text: str, config: ProjectConfig, aliases: dict[str, Any]
) -> DecisionScope:
    """Infer a scope from keywords in free text; no keyword means UNKNOWN, not GLOBAL."""
    raw = _heuristic_raw_scope(text, config.current_release)
    if not any(raw.values()):
        return DecisionScope(scope_kind=ScopeKind.UNKNOWN)
    return normalize_scope(raw, config, aliases)


def load_project(directory: str | Path):
    """Load meetings and artifacts; returns (meeting items, artifacts, {"project", "aliases"})."""
    root = Path(directory)
    cfg = ProjectConfig.model_validate_json((root / "project.json").read_text(encoding="utf-8"))
    aliases = json.loads((root / cfg.scope_aliases_file).read_text(encoding="utf-8"))

    def text_scope(text: str) -> DecisionScope:
        return infer_scope_from_text(text, cfg, aliases)

    def row_scope(row: dict[str, str], text: str) -> DecisionScope:
        raw = {k: (row.get(k) or None) for k in ("release", "module", "user_group", "environment")}
        if not any(raw.values()):
            return text_scope(text)
        return normalize_scope(raw, cfg, aliases)

    meetings = []
    for path in sorted((root / "meetings").glob("*.md")):
        meetings.extend(parse_meetings(path))
    artifacts = []
    req = root / "requirements.md"
    if req.exists():
        artifacts.extend(parse_markdown_artifacts(req, ArtifactKind.REQUIREMENT, text_scope))
    task = root / "tasks.csv"
    if task.exists():
        artifacts.extend(parse_tasks(task, row_scope))
    test_plan = root / "test_plan.md"
    if test_plan.exists():
        artifacts.extend(parse_markdown_artifacts(test_plan, ArtifactKind.TEST, text_scope))
    schedule = root / "schedule.csv"
    if schedule.exists():
        artifacts.extend(parse_schedule(schedule, row_scope))
    return meetings, artifacts, {"project": cfg, "aliases": aliases}
