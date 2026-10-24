from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from decision_agent.desktop.paths import app_home, settings_path

DEFAULT_EXCLUDES = [
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv", "$RECYCLE.BIN",
    "System Volume Information", ".DS_Store", "Thumbs.db", "desktop.ini", "~$*", ".~lock.*",
    "*.tmp", "*.temp", "*.crdownload", "*.part", "*.swp", "*.bak", ".idea", ".vscode",
    ".obsidian", "dist", "build",
]

# Locations that must never be watched, even when the user asks for them.
_DENIED_EXACT = {"/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/var", "/proc", "/sys", "/dev", "/boot"}


class FolderRejected(ValueError):
    """Raised when a folder cannot be used as a watch root."""


class ConnectorSettings(BaseModel):
    """Non-secret settings of one connector. Passwords and tokens live in the credential store."""

    enabled: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class AppSettings(BaseModel):
    allowed_folders: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    max_file_mb: int = Field(default=25, ge=1, le=500)
    debounce_seconds: float = Field(default=30.0, ge=0.0, le=3600.0)
    rescan_minutes: int = Field(default=60, ge=0, le=1440)
    analysis_mode: Literal["agent", "pipeline"] = "agent"
    allow_external_send: bool = False
    provider: Literal["auto", "mock", "anthropic", "ollama"] = "auto"
    model_name: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    embedding_model: str = ""
    notify: bool = True
    ui_port: int = Field(default=8765, ge=1024, le=65535)
    current_release: str | None = None
    scope_aliases: dict[str, dict[str, list[str]]] | None = None
    kind_overrides: dict[str, str] = Field(default_factory=dict)
    max_llm_calls_per_cycle: int = Field(default=400, ge=1, le=100000)
    connectors: dict[str, ConnectorSettings] = Field(default_factory=dict)
    connector_target: str | None = None
    connector_interval_minutes: int = Field(default=15, ge=1, le=1440)
    hotkey_enabled: bool = True
    hotkey: str = "ctrl+alt+d"
    digest_enabled: bool = False
    digest_time: str = "09:00"
    # Subfolders the user unticked in the folder tree, per watch root, as relative POSIX paths.
    excluded_subfolders: dict[str, list[str]] = Field(default_factory=dict)
    display_name: str = Field(default="", max_length=40)
    profile_color: str = "#4f46e5"
    theme: Literal["system", "light", "dark"] = "system"
    language: Literal["ko"] = "ko"
    onboarding_done: bool = False

    @field_validator("profile_color")
    @classmethod
    def _check_color(cls, value: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("#RRGGBB 형식이어야 합니다.")
        return value.lower()

    @field_validator("display_name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("excluded_subfolders")
    @classmethod
    def _check_excluded(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        cleaned: dict[str, list[str]] = {}
        for root, items in value.items():
            kept = []
            for item in items:
                relative = normalize_relative(item)
                if relative is None:
                    raise ValueError("제외할 하위 폴더는 감시 폴더 안의 상대 경로여야 합니다.")
                if relative not in kept:
                    kept.append(relative)
            if kept:
                cleaned[root] = kept
        return cleaned

    @field_validator("digest_time")
    @classmethod
    def _check_time(cls, value: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("HH:MM 형식이어야 합니다.")
        return value

    @field_validator("hotkey")
    @classmethod
    def _check_hotkey(cls, value: str) -> str:
        if not re.fullmatch(r"(?:(?:ctrl|alt|shift|win)\+){1,3}[a-z0-9]|(?:(?:ctrl|alt|shift|win)\+){1,3}f(?:[1-9]|1[0-2])", value.lower()):
            raise ValueError("예: ctrl+alt+d")
        return value.lower()

    @field_validator("ollama_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not re.match(r"^https?://[^\s/]+(?::\d+)?/?$", value):
            raise ValueError("주소는 http://호스트:포트 형식이어야 합니다.")
        return value.rstrip("/")

    def roots(self) -> list[Path]:
        return [Path(p) for p in self.allowed_folders]

    def excluded_for(self, root: Path | str) -> list[str]:
        """Excluded subfolders of one watch root (matched on the resolved, case-folded path)."""
        wanted = root_key(root)
        for key, items in self.excluded_subfolders.items():
            if root_key(key) == wanted:
                return list(items)
        return []


def root_key(path: Path | str) -> str:
    return str(Path(path).resolve()).casefold()


def normalize_relative(value: str) -> str | None:
    """A clean relative POSIX path inside a root, or None if it is absolute or escapes the root."""
    text = str(value).replace("\\", "/").strip().strip("/")
    if not text or ":" in text:
        return None
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _denied_paths() -> set[Path]:
    denied = {Path(p) for p in _DENIED_EXACT}
    denied.add(Path.home())
    denied.add(app_home())
    if sys.platform == "win32":
        for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "windir"):
            value = os.getenv(name)
            if value:
                denied.add(Path(value))
    return denied


def validate_folder(path: str | Path) -> Path:
    """Resolve and sanity-check a folder before it becomes a watch root."""
    candidate = Path(path).expanduser()
    if not candidate.exists() or not candidate.is_dir():
        raise FolderRejected(f"폴더를 찾을 수 없습니다: {path}")
    resolved = candidate.resolve()
    if resolved == resolved.parent:
        raise FolderRejected("드라이브 루트는 감시할 수 없습니다. 프로젝트 폴더를 지정해 주세요.")
    for denied in _denied_paths():
        try:
            if resolved == denied.resolve():
                raise FolderRejected(f"이 폴더는 감시할 수 없습니다: {resolved}")
        except OSError:
            continue
    if sys.platform == "win32":
        for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            value = os.getenv(name)
            if value and resolved.is_relative_to(Path(value).resolve()):
                raise FolderRejected(f"시스템 폴더 아래는 감시할 수 없습니다: {resolved}")
    elif resolved.parts[:2] in {("/", "etc"), ("/", "proc"), ("/", "sys"), ("/", "dev")}:
        raise FolderRejected(f"시스템 폴더 아래는 감시할 수 없습니다: {resolved}")
    return resolved


def load_settings(path: Path | None = None) -> AppSettings:
    target = path or settings_path()
    if not target.exists():
        return AppSettings()
    try:
        return AppSettings.model_validate_json(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        backup = target.with_suffix(".broken.json")
        try:
            target.replace(backup)
        except OSError:
            pass
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    """Write atomically so a crash never leaves a half-written settings file."""
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix="settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class FolderContained(FolderRejected):
    """The folder already lies inside a registered folder, so it is read already."""

    def __init__(self, parent: Path) -> None:
        super().__init__(f"이미 포함되어 있습니다. 등록된 폴더({parent})가 이 폴더를 포함합니다.")
        self.parent = parent


class FolderNeedsMerge(FolderRejected):
    """The folder contains registered folders. Adding it with merge=True replaces them."""

    def __init__(self, children: list[Path]) -> None:
        names = ", ".join(str(c) for c in children)
        super().__init__(f"이미 등록된 폴더({names})를 포함하는 상위 폴더입니다. 하나로 합칠 수 있습니다.")
        self.children = children


def is_inside(child: Path, parent: Path) -> bool:
    """True when child is strictly below parent (case-insensitive, as on Windows)."""
    a = [p.casefold() for p in child.parts]
    b = [p.casefold() for p in parent.parts]
    return len(a) > len(b) and a[: len(b)] == b


def add_folder(settings: AppSettings, path: str | Path, merge: bool = False) -> Path:
    """Register a watch root. Every subfolder below it is read too.

    A folder inside a registered one is refused (it is already read). A parent of registered
    folders is refused unless merge is True; then it replaces them and their excluded subfolders
    are kept, re-expressed relative to the new parent.
    """
    resolved = validate_folder(path)
    existing = [Path(p).resolve() for p in settings.allowed_folders]
    children = []
    for other in existing:
        if str(resolved).casefold() == str(other).casefold():
            return other
        if is_inside(resolved, other):
            raise FolderContained(other)
        if is_inside(other, resolved):
            children.append(other)
    if children and not merge:
        raise FolderNeedsMerge(children)
    excluded = settings.excluded_for(resolved)
    for child in children:
        prefix = "/".join(child.parts[len(resolved.parts):])
        for item in settings.excluded_for(child):
            relative = f"{prefix}/{item}" if prefix else item
            if relative not in excluded:
                excluded.append(relative)
        remove_folder(settings, child)
    settings.allowed_folders.append(str(resolved))
    if excluded:
        settings.excluded_subfolders[str(resolved)] = excluded
    return resolved


def remove_folder(settings: AppSettings, path: str | Path) -> bool:
    target = root_key(Path(path).expanduser())
    kept = [p for p in settings.allowed_folders if root_key(p) != target]
    removed = len(kept) != len(settings.allowed_folders)
    settings.allowed_folders = kept
    settings.excluded_subfolders = {
        k: v for k, v in settings.excluded_subfolders.items() if root_key(k) != target
    }
    return removed


def set_excluded_subfolders(settings: AppSettings, root: str | Path, relative: list[str]) -> list[str]:
    """Replace the excluded subfolders of one registered root. Returns the cleaned list."""
    target = root_key(root)
    match = next((p for p in settings.allowed_folders if root_key(p) == target), None)
    if match is None:
        raise FolderRejected("등록된 감시 폴더가 아닙니다.")
    cleaned: list[str] = []
    for item in relative:
        value = normalize_relative(item)
        if value is None:
            raise FolderRejected("제외할 하위 폴더는 감시 폴더 안의 상대 경로여야 합니다.")
        if value not in cleaned:
            cleaned.append(value)
    # A subfolder below another excluded one adds nothing.
    cleaned = [
        c for c in cleaned if not any(c != o and c.startswith(o + "/") for o in cleaned)
    ]
    others = {k: v for k, v in settings.excluded_subfolders.items() if root_key(k) != target}
    if cleaned:
        others[match] = sorted(cleaned)
    settings.excluded_subfolders = others
    return sorted(cleaned)
