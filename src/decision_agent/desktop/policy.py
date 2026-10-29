from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from decision_agent.sources.readers import READERS, UNSUPPORTED_HINTS, extension_of

CONFIG_NAMES = {"project.json", "scope_aliases.json", ".decision_agent.json"}


def is_link_dir(path: Path) -> bool:
    """True for a directory symlink or a Windows junction (both may point outside the root)."""
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        if isjunction is not None and isjunction(path):
            return True
        if os.name == "nt":
            attrs = getattr(os.lstat(path), "st_file_attributes", 0)
            return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return True
    return False


@dataclass(frozen=True)
class Verdict:
    """Whether a file may be read, and why not. status is ok, skip, or unsupported."""

    status: str
    reason: str = ""


class FolderPolicy:
    """Read-only access rules for one watched folder.

    A path is readable only if it resolves, after following symbolic links, to a location
    inside the folder, matches no exclude pattern, is a regular file under the size limit,
    and has a supported extension. Nothing here writes to the watched folder.
    """

    def __init__(
        self,
        root: Path,
        exclude_patterns: list[str],
        max_file_mb: int,
        excluded_subfolders: list[str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.patterns = [p.lower() for p in exclude_patterns]
        self.max_bytes = max_file_mb * 1024 * 1024
        # Relative subfolders the user unticked; matched case-insensitively, like Windows paths.
        self.excluded_dirs = tuple(
            tuple(part.casefold() for part in item.split("/")) for item in excluded_subfolders or []
        )

    @classmethod
    def for_root(cls, root: Path, settings) -> FolderPolicy:
        """The policy for one watch root under the given AppSettings."""
        return cls(root, settings.exclude_patterns, settings.max_file_mb, settings.excluded_for(root))

    def _matches(self, name: str) -> bool:
        lowered = name.lower()
        return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in self.patterns)

    def in_excluded_subfolder(self, parts: tuple[str, ...]) -> bool:
        folded = tuple(part.casefold() for part in parts)
        return any(folded[: len(d)] == d for d in self.excluded_dirs if len(folded) >= len(d))

    def is_excluded_relative(self, parts: tuple[str, ...]) -> bool:
        if self.excluded_dirs and self.in_excluded_subfolder(parts):
            return True
        for part in parts:
            if part.startswith(".") and part.lower() not in CONFIG_NAMES:
                return True
            if self._matches(part):
                return True
        relative = "/".join(parts).lower()
        return any(fnmatch.fnmatchcase(relative, pattern) for pattern in self.patterns)

    def relative_parts(self, path: Path) -> tuple[str, ...] | None:
        """Parts of path relative to the root, or None when it lies outside (symlinks resolved)."""
        try:
            resolved = path.resolve()
            return resolved.relative_to(self.root).parts
        except (OSError, ValueError):
            return None

    def check_file(self, path: Path) -> Verdict:
        parts = self.relative_parts(path)
        if parts is None:
            return Verdict("skip", "폴더 밖을 가리키는 경로입니다.")
        if not parts or self.is_excluded_relative(parts):
            return Verdict("skip", "제외 패턴에 해당합니다.")
        if len(parts) == 1 and parts[0].lower() in CONFIG_NAMES:
            return Verdict("skip", "설정 파일입니다.")
        ext = extension_of(path)
        if ext not in READERS:
            if ext in UNSUPPORTED_HINTS:
                return Verdict("unsupported", UNSUPPORTED_HINTS[ext])
            return Verdict("skip", "읽을 수 없는 형식입니다.")
        try:
            resolved = path.resolve()
            if not resolved.is_file():
                return Verdict("skip", "일반 파일이 아닙니다.")
            if resolved.stat().st_size > self.max_bytes:
                return Verdict("skip", "파일이 최대 크기를 넘습니다.")
        except OSError:
            return Verdict("skip", "파일 정보를 읽을 수 없습니다.")
        return Verdict("ok")

    def iter_files(self) -> Iterator[Path]:
        """Walk the folder without following directory links and without entering excluded dirs.

        Every subfolder is included unless it matches an exclude pattern or the user excluded it.
        Junctions and directory symlinks are not entered, so the walk never leaves the folder.
        """
        for current, dirs, files in os.walk(self.root, followlinks=False):
            base = Path(current)
            relative = base.relative_to(self.root).parts
            dirs[:] = [
                d for d in dirs
                if not self.is_excluded_relative(relative + (d,)) and not is_link_dir(base / d)
            ]
            for name in files:
                yield base / name
