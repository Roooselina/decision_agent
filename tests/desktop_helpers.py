from __future__ import annotations

import shutil
from pathlib import Path

from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.providers import build_provider
from decision_agent.desktop.settings import AppSettings
from decision_agent.desktop.state import StateDB

DEMO_DIR = Path(__file__).resolve().parents[1] / "data" / "demo"


def make_watch_dir(base: Path, name: str = "watch") -> Path:
    target = base / name
    shutil.copytree(DEMO_DIR, target)
    return target


def make_settings(root: Path, **overrides) -> AppSettings:
    values = {
        "allowed_folders": [str(root)],
        "provider": "mock",
        "debounce_seconds": 0.3,
        "rescan_minutes": 0,
    }
    values.update(overrides)
    return AppSettings(**values)


def make_engine(root: Path, **overrides) -> tuple[ProjectEngine, StateDB, AppSettings]:
    settings = make_settings(root, **overrides)
    db = StateDB(":memory:")
    return ProjectEngine(root, settings, db, build_provider(settings)), db, settings


def findings_by_text(db: StateDB) -> dict[str, str]:
    return {row["text"]: row["label"] for row in db.list_findings(include_hidden=True)}
