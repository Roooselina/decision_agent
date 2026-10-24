from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from decision_agent.desktop.paths import app_home

MIN_WIDTH = 1100
MIN_HEIGHT = 720
DEFAULTS = {
    "width": 1280,
    "height": 820,
    "x": None,
    "y": None,
    "maximized": False,
    "close_hint_shown": False,
}


def ui_state_path() -> Path:
    return app_home() / "ui_state.json"


def load_ui_state(path: Path | None = None) -> dict:
    """Window geometry and one-time hints. Not user settings, so kept apart from settings.json."""
    target = path or ui_state_path()
    state = dict(DEFAULTS)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if not isinstance(raw, dict):
        return state
    for key in ("width", "height"):
        if isinstance(raw.get(key), int):
            state[key] = raw[key]
    for key in ("x", "y"):
        if isinstance(raw.get(key), int) and -10000 < raw[key] < 30000:
            state[key] = raw[key]
    for key in ("maximized", "close_hint_shown"):
        if isinstance(raw.get(key), bool):
            state[key] = raw[key]
    state["width"] = max(MIN_WIDTH, min(state["width"], 8000))
    state["height"] = max(MIN_HEIGHT, min(state["height"], 6000))
    return state


def save_ui_state(state: dict, path: Path | None = None) -> None:
    target = path or ui_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {k: state.get(k, v) for k, v in DEFAULTS.items()}
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix="ui_state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
