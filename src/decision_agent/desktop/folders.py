from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path

from decision_agent.desktop.policy import FolderPolicy, is_link_dir
from decision_agent.desktop.settings import (
    AppSettings,
    FolderRejected,
    is_inside,
    validate_folder,
)
from decision_agent.sources.readers import extension_of

MANY_FILES = 5000
SCAN_FILE_LIMIT = 50000
SCAN_SECONDS = 8.0
TREE_NODE_LIMIT = 2000
TREE_DEPTH_LIMIT = 12


def relation_to_registered(path: Path, settings: AppSettings) -> dict:
    """How a candidate folder relates to the folders that are already registered."""
    for folder in settings.allowed_folders:
        other = Path(folder).resolve()
        if str(other).casefold() == str(path).casefold():
            return {"kind": "duplicate", "folders": [str(other)]}
        if is_inside(path, other):
            return {"kind": "contained", "folders": [str(other)]}
    children = [
        str(Path(f).resolve()) for f in settings.allowed_folders if is_inside(Path(f).resolve(), path)
    ]
    if children:
        return {"kind": "parent", "folders": children}
    return {"kind": "new", "folders": []}


def preview_folder(path: str, settings: AppSettings, clock=time.monotonic) -> dict:
    """What adding this folder would read, without registering it and without writing anything."""
    try:
        root = validate_folder(path)
    except FolderRejected as exc:
        return {"ok": False, "path": path, "reason": str(exc)}
    policy = FolderPolicy(root, settings.exclude_patterns, settings.max_file_mb)
    formats: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    excluded_dirs: list[str] = []
    readable = folders = seen = 0
    truncated = False
    deadline = clock() + SCAN_SECONDS
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        relative = base.relative_to(root).parts
        kept = []
        for name in dirs:
            if is_link_dir(base / name):
                skipped["폴더 밖을 가리킬 수 있는 링크 폴더"] += 1
            elif policy.is_excluded_relative(relative + (name,)):
                if len(excluded_dirs) < 20:
                    excluded_dirs.append("/".join(relative + (name,)))
            else:
                kept.append(name)
        dirs[:] = kept
        folders += len(kept)
        for name in files:
            seen += 1
            verdict = policy.check_file(base / name)
            if verdict.status == "ok":
                readable += 1
                formats[extension_of(base / name)] += 1
            elif verdict.status == "unsupported":
                unsupported[extension_of(base / name)] += 1
            else:
                skipped[verdict.reason] += 1
            if seen >= SCAN_FILE_LIMIT or clock() > deadline:
                truncated = True
                break
        if truncated:
            break
    warnings = []
    if readable > MANY_FILES:
        warnings.append(
            f"읽을 수 있는 파일이 {readable}개로 많습니다({MANY_FILES}개 초과). "
            "첫 점검이 오래 걸릴 수 있으니 필요한 하위 폴더만 남기는 것을 권합니다."
        )
    if truncated:
        warnings.append("파일이 너무 많아 일부만 세었습니다. 실제 수는 더 많습니다.")
    if readable == 0:
        warnings.append("이 폴더에는 읽을 수 있는 문서가 없습니다.")
    return {
        "ok": True,
        "path": str(root),
        "readable": readable,
        "formats": dict(formats.most_common()),
        "unsupported": dict(unsupported.most_common()),
        "skipped": dict(skipped.most_common()),
        "excluded_dirs": excluded_dirs,
        "subfolders": folders,
        "files_seen": seen,
        "truncated": truncated,
        "many_files": readable > MANY_FILES,
        "warnings": warnings,
        "relation": relation_to_registered(root, settings),
    }


def folder_tree(root: Path, settings: AppSettings) -> dict:
    """Subfolders of a registered root with file counts and whether the user excluded them.

    The walk does not prune user-excluded folders, so their counts stay visible and they can be
    ticked again. Pattern-excluded folders and links are left out entirely.
    """
    root = root.resolve()
    policy = FolderPolicy(root, settings.exclude_patterns, settings.max_file_mb)
    excluded = set(settings.excluded_for(root))
    nodes: dict[tuple[str, ...], dict] = {}

    def node(parts: tuple[str, ...]) -> dict:
        return {
            "name": parts[-1] if parts else root.name or str(root),
            "rel": "/".join(parts),
            "readable": 0,
            "unsupported": 0,
            "total_readable": 0,
            "total_unsupported": 0,
            "excluded": "/".join(parts) in excluded,
            "children": [],
        }

    nodes[()] = node(())
    truncated = False
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        relative = base.relative_to(root).parts
        if len(relative) >= TREE_DEPTH_LIMIT:
            dirs[:] = []
        kept = []
        for name in sorted(dirs, key=str.casefold):
            parts = relative + (name,)
            if is_link_dir(base / name) or policy.is_excluded_relative(parts):
                continue
            if len(nodes) >= TREE_NODE_LIMIT:
                truncated = True
                continue
            child = nodes[parts] = node(parts)
            nodes[relative]["children"].append(child)
            kept.append(name)
        dirs[:] = kept
        here = nodes[relative]
        for name in files:
            verdict = policy.check_file(base / name)
            if verdict.status == "ok":
                here["readable"] += 1
            elif verdict.status == "unsupported":
                here["unsupported"] += 1
    for parts in sorted(nodes, key=len, reverse=True):
        item = nodes[parts]
        item["total_readable"] = item["readable"] + sum(c["total_readable"] for c in item["children"])
        item["total_unsupported"] = item["unsupported"] + sum(
            c["total_unsupported"] for c in item["children"]
        )
    tree = nodes[()]
    tree["name"] = root.name or str(root)
    return {"root": str(root), "tree": tree, "excluded": sorted(excluded), "truncated": truncated}
