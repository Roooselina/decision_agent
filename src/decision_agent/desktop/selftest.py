"""`selftest`: quick checks that the installed program can run, printed as JSON.

Each check is ok, failed or skipped. Only failed checks make the exit code non-zero. Nothing is
written except one temporary file in the app's own data folder, and the databases are opened
read-only. The model check makes one tiny request only when a key or local model is set up.
"""

from __future__ import annotations

import importlib
import json
import os
import socket
import sqlite3
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from decision_agent.desktop.about import about
from decision_agent.desktop.paths import app_home, mail_db_path, settings_path, state_db_path
from decision_agent.desktop.settings import AppSettings
from decision_agent.desktop.statedump import open_readonly

REQUIRED_MODULES = (
    "decision_agent.desktop.app", "decision_agent.desktop.webapp", "fastapi", "uvicorn",
    "sklearn", "keyring", "docx", "openpyxl", "pypdf", "pptx", "bs4", "watchdog",
)
# Missing ones only change how the program looks (browser instead of window, no tray icon).
OPTIONAL_MODULES = ("webview", "pystray")


def _check(name: str, status: str, detail: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail}


def check_imports() -> list[dict]:
    missing, optional = [], []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - any import failure is a finding
            missing.append(f"{name} ({type(exc).__name__})")
    for name in OPTIONAL_MODULES:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001
            optional.append(name)
    rows = [_check("imports", "failed" if missing else "ok", ", ".join(missing))]
    if optional:
        rows.append(_check("optional_imports", "skipped", "없음: " + ", ".join(optional)))
    return rows


def check_data_folder() -> dict:
    try:
        home = app_home()
        fd, name = tempfile.mkstemp(dir=str(home), prefix="selftest-")
        os.close(fd)
        os.remove(name)
    except OSError as exc:
        return _check("data_folder", "failed", f"쓸 수 없습니다: {exc.strerror or exc}")
    return _check("data_folder", "ok", str(home))


def check_settings() -> tuple[dict, AppSettings]:
    path = settings_path()
    if not path.exists():
        return _check("settings", "ok", "설정 파일이 없어 기본값을 씁니다."), AppSettings()
    try:
        return _check("settings", "ok", str(path)), AppSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _check("settings", "failed", f"설정 파일을 읽지 못했습니다: {type(exc).__name__}"), AppSettings()


def check_databases(settings: AppSettings) -> dict:
    paths = [state_db_path(Path(root)) for root in settings.allowed_folders] + [mail_db_path()]
    opened, problems = 0, []
    for path in paths:
        try:
            conn = open_readonly(path)
        except sqlite3.Error as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        if conn is None:
            continue
        try:
            conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            opened += 1
        except sqlite3.Error as exc:
            problems.append(f"{path.name}: {exc}")
        finally:
            conn.close()
    if problems:
        return _check("databases", "failed", "; ".join(problems))
    return _check("databases", "ok", f"열어 본 데이터베이스 {opened}개")


def check_folders(settings: AppSettings) -> dict:
    if not settings.allowed_folders:
        return _check("folders", "skipped", "등록한 폴더가 없습니다.")
    problems = []
    for root in settings.allowed_folders:
        path = Path(root)
        if not path.is_dir():
            problems.append(f"{root}: 폴더가 없습니다.")
            continue
        try:
            with os.scandir(path) as entries:
                next(entries, None)
        except OSError:
            problems.append(f"{root}: 읽을 수 없습니다.")
    if problems:
        return _check("folders", "failed", "; ".join(problems))
    return _check("folders", "ok", f"읽을 수 있는 폴더 {len(settings.allowed_folders)}개")


def check_port(port: int) -> dict:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            pass
        else:
            return _check("port", "ok", f"{port}번 포트를 쓸 수 있습니다.")
    if _is_this_app(port):
        return _check("port", "ok", f"{port}번 포트는 실행 중인 이 프로그램이 쓰고 있습니다.")
    return _check("port", "failed", f"{port}번 포트를 다른 프로그램이 쓰고 있습니다. 설정에서 포트를 바꾸세요.")


def _is_this_app(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as response:
            return json.loads(response.read().decode("utf-8")).get("app") == "decision-agent"
    except Exception:  # noqa: BLE001 - anything else on the port is not this app
        return False


def check_model(settings: AppSettings, bundle_factory: Callable | None = None) -> dict:
    from decision_agent.desktop.providers import build_provider, check_provider

    bundle = (bundle_factory or build_provider)(settings)
    if bundle.local_only:
        return _check("model", "skipped", bundle.reason)
    result = check_provider(bundle)
    return _check("model", "ok" if result["ok"] else "failed", result["message"])


def run_selftest(port: int | None = None, model: bool = True, bundle_factory: Callable | None = None) -> dict:
    checks = check_imports()
    checks.append(check_data_folder())
    settings_row, settings = check_settings()
    checks.append(settings_row)
    checks.append(check_databases(settings))
    checks.append(check_folders(settings))
    checks.append(check_port(port or settings.ui_port))
    if model:
        try:
            checks.append(check_model(settings, bundle_factory))
        except Exception as exc:  # noqa: BLE001 - the report must still come out
            checks.append(_check("model", "failed", type(exc).__name__))
    else:
        checks.append(_check("model", "skipped", "--no-model"))
    info = about()
    return {
        "ok": all(c["status"] != "failed" for c in checks),
        "version": info["version"],
        "commit": info["commit"],
        "data_dir": info["data_dir"],
        "checks": checks,
    }
