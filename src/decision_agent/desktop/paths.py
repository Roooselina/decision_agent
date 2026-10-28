from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


def app_home() -> Path:
    """Directory that holds settings and state. It is never inside a watched folder."""
    override = os.getenv("DECISION_AGENT_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming") / "DecisionAgent"
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "decision-agent"
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return app_home() / "settings.json"


def log_path() -> Path:
    return app_home() / "decision-agent.log"


def activity_path() -> Path:
    """Readable one-line history shown as "최근 점검 기록" (app data, never a watched folder)."""
    return app_home() / "activity.jsonl"


def state_db_path(root: Path) -> Path:
    """One state database per watched folder, named by a hash of the resolved folder path."""
    digest = hashlib.sha1(str(root.resolve()).lower().encode("utf-8")).hexdigest()[:12]
    folder = app_home() / "state"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}.sqlite"


def mail_db_path() -> Path:
    """The local mail index and address book (one for all folders, in the app's own folder)."""
    folder = app_home() / "state"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "mail.sqlite"
