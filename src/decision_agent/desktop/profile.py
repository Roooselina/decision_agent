from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from decision_agent.desktop.settings import AppSettings
from decision_agent.models import ProjectConfig

DEFAULT_ALIASES: dict[str, dict[str, list[str]]] = {
    "release": {
        "mvp": ["MVP", "이번 버전", "1차", "시연 버전", "데모 버전"],
        "production": ["Production", "정식 출시", "정식 버전", "본 출시", "프로덕션"],
    },
    "module": {"login": ["로그인"]},
    "user_group": {"admin": ["관리자"], "beta": ["베타", "베타 테스터"]},
    "environment": {},
}


@dataclass
class Profile:
    config: ProjectConfig
    aliases: dict[str, Any]
    overrides: dict[str, str]
    fingerprint: str
    notes: list[str] = field(default_factory=list)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_profile(root: Path, settings: AppSettings) -> Profile:
    """Resolve the scope vocabulary for one watched folder.

    Order of precedence: the folder's own .decision_agent.json, then the older project.json
    plus scope_aliases.json layout, then the application settings, then built-in defaults.
    """
    notes: list[str] = []
    current_release = settings.current_release
    aliases: dict[str, Any] = dict(settings.scope_aliases or DEFAULT_ALIASES)
    overrides = dict(settings.kind_overrides)

    legacy = root / "project.json"
    if legacy.is_file():
        try:
            data = _read_json(legacy)
            current_release = data.get("current_release", current_release)
            alias_file = root / data.get("scope_aliases_file", "scope_aliases.json")
            if alias_file.is_file():
                aliases = _read_json(alias_file)
                notes.append("project.json과 scope_aliases.json을 사용합니다.")
        except (ValueError, OSError):
            notes.append("project.json을 읽지 못해 기본 설정을 사용합니다.")

    own = root / ".decision_agent.json"
    if own.is_file():
        try:
            data = _read_json(own)
            current_release = data.get("current_release", current_release)
            if isinstance(data.get("scope_aliases"), dict):
                aliases = data["scope_aliases"]
            if isinstance(data.get("kind_overrides"), dict):
                overrides.update({str(k): str(v) for k, v in data["kind_overrides"].items()})
            notes.append(".decision_agent.json을 사용합니다.")
        except (ValueError, OSError):
            notes.append(".decision_agent.json을 읽지 못해 무시했습니다.")

    for dim in ("release", "module", "user_group", "environment"):
        aliases.setdefault(dim, {})
    payload = json.dumps(
        {"r": current_release, "a": aliases, "o": overrides}, sort_keys=True, ensure_ascii=False
    )
    return Profile(
        config=ProjectConfig(current_release=current_release),
        aliases=aliases,
        overrides=overrides,
        fingerprint=hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
        notes=notes,
    )
