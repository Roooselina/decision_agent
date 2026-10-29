"""Version, commit and where the app keeps its data: the 정보 card, selftest and state dump.

The commit comes from install_info.json, which tools/update_windows.ps1 writes next to the
installed environment, or from git when the program runs from a repository checkout.
"""

from __future__ import annotations

import functools
import json
import platform
import subprocess
import sys
from pathlib import Path

from decision_agent import __version__
from decision_agent.desktop.paths import app_home, log_path, settings_path

INSTALL_INFO = "install_info.json"


def install_info_path() -> Path:
    """install_info.json sits beside the environment the program runs from (<install>/venv)."""
    return Path(sys.prefix).parent / INSTALL_INFO


def _read_install_file() -> dict:
    try:
        data = json.loads(install_info_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@functools.lru_cache(maxsize=1)
def _git_commit() -> str:
    repo = Path(__file__).resolve().parents[3]
    if not (repo / ".git").exists():
        return ""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            check=False, capture_output=True, text=True, timeout=3, creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def about() -> dict:
    """Plain facts for the 정보 card. No secret values, no document text."""
    installed = _read_install_file()
    commit = str(installed.get("commit") or "") or _git_commit()
    return {
        "version": __version__,
        "commit": commit,
        "commit_source": "install" if installed.get("commit") else "git" if commit else "",
        "installed_at": str(installed.get("installed_at") or ""),
        "install_dir": str(installed.get("install_dir") or ""),
        "data_dir": str(app_home()),
        "settings_path": str(settings_path()),
        "log_path": str(log_path()),
        "python": sys.version.split()[0],
        "os": platform.platform(),
    }
