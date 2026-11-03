"""Demo profile: a reversible preset of a few settings for a live demonstration.

The preset values live in demo_profile.json in the data folder (written with the defaults below
the first time, and editable). Turning the profile on saves the previous values of exactly those
settings to demo_profile_backup.json; turning it off writes them back and deletes the backup.
The backup file existing is what "on" means, so the state survives restarts.

Change settings.json only while the program is stopped: the running program keeps its own copy
and would write it back. tools/demo_profile.ps1 stops and restarts the program around this.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from decision_agent.desktop.paths import app_home, settings_path
from decision_agent.desktop.settings import AppSettings, save_settings

PRESET_FILE = "demo_profile.json"
BACKUP_FILE = "demo_profile_backup.json"

# Only settings that make a demo quick and predictable. Detection settings are not touched.
DEFAULT_VALUES: dict = {
    "debounce_seconds": 3.0,
    "debounce_max_seconds": 20.0,
    "mail_poll_enabled": True,
    "mail_poll_seconds": 15,
    "cost_profile": "balanced",
    "confirm_over_calls": 100,
}
MISSING = "__missing__"


def preset_path() -> Path:
    return app_home() / PRESET_FILE


def backup_path() -> Path:
    return app_home() / BACKUP_FILE


def preset_values() -> dict:
    """The preset from demo_profile.json, created with the defaults when it does not exist."""
    path = preset_path()
    if not path.exists():
        body = {
            "note": "시연 설정. 'decision-agent demo-profile on'이 이 값을 적용하고, 'off'가 이전 값으로 되돌립니다.",
            "values": DEFAULT_VALUES,
        }
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return dict(DEFAULT_VALUES)
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, dict) or not values:
        raise ValueError(f"{path}: 'values' 항목이 없습니다.")
    unknown = set(values) - set(AppSettings.model_fields)
    if unknown:
        raise ValueError(f"{path}: 알 수 없는 설정입니다: {', '.join(sorted(unknown))}")
    return values


def _read_raw(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def status() -> dict:
    """{"on": bool, "since": str, "values": {...}} for the 정보 card and the preflight."""
    backup = backup_path()
    if not backup.exists():
        return {"on": False, "since": "", "values": {}}
    try:
        saved = json.loads(backup.read_text(encoding="utf-8"))
        current = _read_raw(settings_path())
    except (OSError, ValueError):
        return {"on": True, "since": "", "values": {}}
    keys = list(saved.get("previous", {}))
    return {
        "on": True,
        "since": str(saved.get("since", "")),
        "values": {key: current.get(key) for key in keys},
    }


def turn_on(path: Path | None = None) -> dict:
    """Apply the preset. Running it again keeps the first backup, so 'off' restores the original."""
    target = path or settings_path()
    values = preset_values()
    raw = _read_raw(target)
    backup = backup_path()
    if not backup.exists():
        previous = {key: raw.get(key, MISSING) for key in values}
        body = {"since": datetime.now().isoformat(timespec="seconds"), "previous": previous}
        backup.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    merged = dict(raw)
    merged.update(values)
    save_settings(AppSettings.model_validate(merged), target)
    return status()


def turn_off(path: Path | None = None) -> dict:
    """Write back the values saved by turn_on and delete the backup. Does nothing when off."""
    target = path or settings_path()
    backup = backup_path()
    if not backup.exists():
        return status()
    saved = json.loads(backup.read_text(encoding="utf-8"))
    raw = _read_raw(target)
    for key, value in saved.get("previous", {}).items():
        if value == MISSING:
            raw.pop(key, None)
        else:
            raw[key] = value
    save_settings(AppSettings.model_validate(raw), target)
    backup.unlink()
    return status()
