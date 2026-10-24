from __future__ import annotations

import argparse
import getpass
import json
import logging
import logging.handlers
import os
import secrets as pysecrets
import shutil
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from decision_agent import __version__
from decision_agent.desktop import secrets as secret_store
from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.autostart import Autostart
from decision_agent.desktop.engine import ProjectEngine
from decision_agent.desktop.hotkey import HotkeyListener
from decision_agent.desktop.notify import CallbackNotifier, ChainNotifier, make_notifier
from decision_agent.desktop.paths import activity_path, app_home, log_path, state_db_path
from decision_agent.desktop.providers import build_provider
from decision_agent.desktop.settings import (
    AppSettings,
    FolderRejected,
    add_folder,
    load_settings,
    remove_folder,
    save_settings,
)
from decision_agent.desktop.shell import DesktopShell, SingleInstance, choose_ui_mode
from decision_agent.desktop.state import StateDB

TOKEN_FILE = "ui_token"


def setup_logging(background: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    if not background and sys.stderr is not None:
        root.addHandler(logging.StreamHandler(sys.stderr))
    for noisy in ("uvicorn", "httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def port_in_use(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        probe.close()


def ui_url(port: int, token: str, page: str = "/") -> str:
    suffix = "" if page == "/" else f"&next={page}"
    return f"http://127.0.0.1:{port}/?t={token}{suffix}"


def write_token(token: str) -> None:
    path = app_home() / TOKEN_FILE
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_token() -> str | None:
    path = app_home() / TOKEN_FILE
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def is_running_instance(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as response:
            return json.loads(response.read().decode("utf-8")).get("app") == "decision-agent"
    except Exception:  # noqa: BLE001 - anything else on that port is not our program
        return False


def activate_running(port: int, token: str, page: str = "") -> bool:
    """Ask the instance that is already running to show its window."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/app/activate",
        data=json.dumps({"page": page}).encode("utf-8"),
        headers={"X-DA-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return bool(json.loads(response.read().decode("utf-8")).get("ok"))
    except Exception:  # noqa: BLE001 - an old or broken instance; fall back to the browser
        return False


def run_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    setup_logging(args.background)
    port = getattr(args, "port", None) or settings.ui_port
    instance = SingleInstance() if not args.no_tray else None
    first_instance = instance.acquire() if instance is not None else True
    if port_in_use(port) or not first_instance:
        token = read_token()
        if is_running_instance(port) and token:
            wants_window = not args.background and not args.no_browser
            if wants_window and not activate_running(port, token):
                webbrowser.open(ui_url(port, token))
            print("이미 실행 중입니다. 열려 있는 창을 앞으로 가져옵니다.")
            return 0
        if not first_instance:
            print("이미 실행 중인 Decision Agent가 응답하지 않습니다. 잠시 후 다시 시도하세요.", file=sys.stderr)
            return 1
        print(f"포트 {port}를 사용할 수 없습니다. 다른 프로그램이 사용 중일 수 있습니다.", file=sys.stderr)
        return 1
    if args.no_tray:
        return _serve(args, settings, port)
    try:
        return _run_desktop(args, settings, port)
    finally:
        instance.release()


def _serve(args: argparse.Namespace, settings: AppSettings, port: int) -> int:
    """Watchers and the web UI only (no tray, no window), for servers and remote desktops."""
    from decision_agent.desktop.webapp import WebServer, create_web_app

    token = pysecrets.token_urlsafe(32)
    write_token(token)
    controller = DecisionAgentApp(
        settings, settings_file=None, notifier=make_notifier(None), activity_file=activity_path()
    )
    controller.settings_file = app_home() / "settings.json"
    controller.start()
    server = WebServer(create_web_app(controller, token, port), port)
    server.start()
    controller.activate_hook = lambda page: webbrowser.open(ui_url(port, token))
    if not args.no_browser:
        webbrowser.open(ui_url(port, token))
    print(f"대시보드: http://127.0.0.1:{port}/")
    try:
        _wait(threading.Event())
    finally:
        server.stop()
        controller.stop()
    return 0


def _run_desktop(args: argparse.Namespace, settings: AppSettings, port: int) -> int:
    """Tray icon, folder watching, and the dashboard in its own window (browser as fallback)."""

    from decision_agent.desktop.tray import TrayApp, pick_folder_dialog
    from decision_agent.desktop.webapp import WebServer, create_web_app

    token = pysecrets.token_urlsafe(32)
    write_token(token)
    holder: dict[str, object] = {"tray": None}

    def tray_notify(title: str, message: str) -> None:
        tray = holder["tray"]
        if tray is not None:
            tray.notify(title, message)

    notifier = make_notifier(tray_notify)
    if not isinstance(notifier, ChainNotifier):
        notifier = ChainNotifier(CallbackNotifier(tray_notify))
    controller = DecisionAgentApp(
        settings, settings_file=None, notifier=notifier, activity_file=activity_path()
    )
    controller.settings_file = app_home() / "settings.json"
    controller.autostart = Autostart()
    controller.start()
    server = WebServer(create_web_app(controller, token, port), port)
    server.start()
    url = ui_url(port, token)
    quick_url = ui_url(port, token, "/quick")
    hotkey_holder: dict[str, object] = {"listener": None}

    mode, reason = ("browser", "") if args.no_window else choose_ui_mode()
    shell: DesktopShell | None = None
    if mode == "window":
        import webview

        def first_hide() -> None:
            tray_notify(
                "계속 백그라운드에서 감시합니다",
                "창을 닫아도 폴더 감시는 계속됩니다. 트레이 아이콘에서 다시 열거나 종료할 수 있습니다.",
            )

        shell = DesktopShell(
            webview,
            url,
            quick_url,
            storage_path=app_home() / "webview",
            on_first_hide=first_hide,
            on_drop=controller.drops.put,
            background=args.background,
        )
        controller.folder_picker = shell.pick_folder
        open_main = shell.show_main
        open_quick = shell.show_quick
    else:
        if reason:
            logging.getLogger(__name__).warning("native window unavailable: %s", reason)
        controller.folder_picker = pick_folder_dialog
        open_main = lambda page="": webbrowser.open(url)
        open_quick = lambda: webbrowser.open(quick_url)
    controller.ui_mode, controller.ui_reason = mode, reason
    controller.activate_hook = lambda page: open_quick() if page == "/quick" else open_main(page)

    def apply_hotkey(current: AppSettings) -> None:
        old = hotkey_holder["listener"]
        if old is not None:
            old.stop()
            hotkey_holder["listener"] = None
        controller.hotkey_state = {
            "enabled": current.hotkey_enabled, "active": False, "spec": current.hotkey, "reason": ""
        }
        if not current.hotkey_enabled:
            return
        listener = HotkeyListener(current.hotkey, open_quick)
        ok = listener.start()
        controller.hotkey_state["active"] = ok
        controller.hotkey_state["reason"] = listener.reason
        if ok:
            hotkey_holder["listener"] = listener
        else:
            logging.getLogger(__name__).warning("hotkey not registered: %s", listener.reason)

    apply_hotkey(settings)
    controller.settings_hooks.append(apply_hotkey)
    first_run = not settings.allowed_folders
    if shell is None and not args.no_browser and (not args.background or first_run):
        webbrowser.open(url)
    print(f"대시보드: http://127.0.0.1:{port}/")

    stop_event = threading.Event()

    def shutdown() -> None:
        stop_event.set()
        tray = holder["tray"]
        if tray is not None:
            tray.stop()
        if shell is not None:
            shell.quit()

    tray = TrayApp(
        controller,
        open_ui=open_main,
        open_quick=open_quick,
        open_digest=lambda: open_main("#/digest"),
        quit_app=shutdown,
        autostart=controller.autostart,
        pick_folder=controller.folder_picker,
        open_log_folder=lambda: _open_folder(log_path().parent),
    )
    holder["tray"] = tray
    if shell is not None:
        # The GUI loop owns the main thread; the tray icon runs on its own thread.
        threading.Thread(target=_run_tray, args=(tray,), name="tray", daemon=True).start()
        if first_run:
            shell.background = False
        try:
            shell.run()
        except Exception:
            logging.getLogger(__name__).exception("native window failed, using the browser")
            controller.ui_mode, controller.ui_reason = "browser", "창을 열지 못해 기본 브라우저를 사용합니다."
            controller.folder_picker = pick_folder_dialog
            controller.activate_hook = lambda page: webbrowser.open(url)
            tray.open_ui = lambda: webbrowser.open(url)
            tray.open_quick = lambda: webbrowser.open(quick_url)
            webbrowser.open(url)
            _wait(stop_event)
    else:
        if not _run_tray(tray):
            print("트레이 아이콘을 사용할 수 없어 대시보드만 실행합니다.", file=sys.stderr)
        _wait(stop_event)
    listener = hotkey_holder["listener"]
    if listener is not None:
        listener.stop()
    server.stop()
    controller.stop()
    return 0


def _run_tray(tray) -> bool:
    try:
        tray.run()
        return True
    except Exception as exc:  # noqa: BLE001 - no display or no tray backend
        logging.getLogger(__name__).warning("tray unavailable: %s", exc)
        tray.icon = None
        return False


def _wait(stop_event: threading.Event) -> None:
    try:
        while not stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass


def _open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        webbrowser.open(path.as_uri())


def scan_command(args: argparse.Namespace) -> int:
    """One-shot analysis without watchers, for scripts and for checking a setup."""
    settings = load_settings()
    setup_logging(True)
    roots = [Path(args.folder).resolve()] if args.folder else settings.roots()
    if not roots:
        print("감시 폴더가 없습니다. add-folder로 추가하거나 --folder를 지정하세요.", file=sys.stderr)
        return 1
    bundle = build_provider(settings)
    print(f"판정기: {bundle.provider_id} ({bundle.reason})")
    found_inconsistent = 0
    report_rows: list[dict] = []
    for root in roots:
        if args.folder:
            try:
                add_folder(settings.model_copy(deep=True), root)
            except FolderRejected as exc:
                print(str(exc), file=sys.stderr)
                return 1
        db = StateDB(state_db_path(root))
        engine = ProjectEngine(root, settings, db, bundle)
        report = engine.process()
        print(f"[{root}] 읽은 파일 변경 {len(report.sync.changed)}건, 점검한 topic {len(report.topics_checked)}개, LLM 호출 {report.llm_calls}회")
        if report.error:
            print(f"  문제: {report.error}")
        for row in db.list_findings():
            report_rows.append(
                {"root": str(root), "topic": row["topic"], "label": row["label"],
                 "text": row["text"], "source_doc": row["source_doc"]}
            )
            if row["label"] == "CONSISTENT":
                continue
            print(f"  {row['label']:<13} {row['topic']}: {row['text']} ({row['source_doc']})")
            if row["label"] == "INCONSISTENT":
                found_inconsistent += 1
        for question in db.list_questions():
            print(f"  확인 질문 [{question['topic']}]: {question['text']}")
        db.close()
    if args.report:
        _write_report(Path(args.report), roots, bundle.provider_id, report_rows)
    return 2 if (found_inconsistent and args.fail_on_inconsistent) else 0


def _write_report(target: Path, roots: list[Path], provider_id: str, rows: list[dict]) -> None:
    """JSON summary of a scan. Refuses to write inside a scanned folder (they stay read-only)."""
    resolved = target.resolve()
    for root in roots:
        if resolved.is_relative_to(root.resolve()):
            raise SystemExit("보고서는 점검하는 폴더 밖에 저장해야 합니다.")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": __version__,
        "provider": provider_id,
        "findings": rows,
        "inconsistent": sum(1 for r in rows if r["label"] == "INCONSISTENT"),
    }
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def folders_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.action == "list":
        for folder in settings.allowed_folders:
            print(folder)
        return 0
    try:
        if args.action == "add":
            print(f"추가했습니다: {add_folder(settings, args.path)}")
        else:
            if not remove_folder(settings, args.path):
                print("등록되지 않은 폴더입니다.", file=sys.stderr)
                return 1
            print("제거했습니다.")
    except FolderRejected as exc:
        print(str(exc), file=sys.stderr)
        return 1
    save_settings(settings)
    return 0


def key_command(args: argparse.Namespace) -> int:
    try:
        if args.action == "set":
            value = getpass.getpass("Anthropic API 키: ").strip()
            if len(value) < 8:
                print("키가 너무 짧습니다.", file=sys.stderr)
                return 1
            secret_store.set_api_key(value)
            print("자격 증명 저장소에 저장했습니다. 외부 전송은 설정에서 따로 켜야 합니다.")
        else:
            secret_store.delete_api_key()
            print("삭제했습니다.")
    except secret_store.SecretStoreUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def reset_data_command(args: argparse.Namespace) -> int:
    """Delete the API key, connector secrets, and all app data. Used by the uninstaller.

    Files in the watched folders are never touched.
    """
    if not args.yes:
        print("모든 앱 데이터와 저장된 키를 지웁니다. 계속하려면 --yes를 붙이세요.", file=sys.stderr)
        return 1
    from decision_agent.connectors.registry import CONNECTORS
    from decision_agent.desktop.connector_manager import ConnectorManager

    settings = load_settings()
    manager = ConnectorManager(lambda: settings)
    for connector_id in CONNECTORS:
        try:
            manager.forget(connector_id)
        except Exception:  # noqa: BLE001 - keep deleting the rest
            logging.getLogger(__name__).warning("could not forget %s", connector_id)
    secret_store.delete_api_key()
    Autostart().disable()
    home = app_home()
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        # The log file lives in the data folder; release it so the folder can be deleted.
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).is_relative_to(home):
            root_logger.removeHandler(handler)
            handler.close()
    shutil.rmtree(home, ignore_errors=True)
    print("앱 데이터를 삭제했습니다. 감시 폴더의 파일은 그대로입니다.")
    return 0


def autostart_command(args: argparse.Namespace) -> int:
    autostart = Autostart()
    if not autostart.supported:
        print("자동 실행은 Windows에서만 지원합니다.", file=sys.stderr)
        return 1
    if args.action == "on":
        autostart.enable()
    elif args.action == "off":
        autostart.disable()
    print("켜짐" if autostart.is_enabled() else "꺼짐")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decision-agent", description="결정 변경 전파 점검 상주 프로그램"
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="트레이 아이콘과 폴더 감시를 시작합니다(기본).")
    run.add_argument("--background", action="store_true", help="브라우저를 열지 않고 조용히 시작")
    run.add_argument("--no-browser", action="store_true")
    run.add_argument("--no-tray", action="store_true", help="트레이 없이 실행(서버 모드)")
    run.add_argument("--no-window", action="store_true", help="독립 창 대신 기본 브라우저 사용")
    run.add_argument("--port", type=int, help="로컬 화면 포트 (기본은 설정의 ui_port)")
    run.set_defaults(func=run_command)

    serve = sub.add_parser("serve", help="트레이 없이 감시와 웹 UI만 실행합니다.")
    serve.add_argument("--background", action="store_true")
    serve.add_argument("--no-browser", action="store_true", default=True)
    serve.add_argument("--port", type=int, help="로컬 화면 포트 (기본은 설정의 ui_port)")
    serve.set_defaults(func=run_command, no_tray=True, no_window=True)

    scan = sub.add_parser("scan", help="한 번만 점검하고 결과를 출력합니다.")
    scan.add_argument("--folder", help="등록하지 않은 폴더를 한 번만 점검")
    scan.add_argument("--fail-on-inconsistent", action="store_true")
    scan.add_argument("--report", help="결과를 JSON으로 저장할 경로 (점검 폴더 밖)")
    scan.set_defaults(func=scan_command)

    reset = sub.add_parser("reset-data", help="저장된 키와 모든 앱 데이터를 삭제합니다.")
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=reset_data_command)

    folders = sub.add_parser("folders", help="감시 폴더 관리")
    folders.add_argument("action", choices=["list", "add", "remove"])
    folders.add_argument("path", nargs="?")
    folders.set_defaults(func=folders_command)

    key = sub.add_parser("key", help="API 키를 OS 자격 증명 저장소에 저장하거나 삭제")
    key.add_argument("action", choices=["set", "clear"])
    key.set_defaults(func=key_command)

    auto = sub.add_parser("autostart", help="Windows 로그인 시 자동 실행")
    auto.add_argument("action", choices=["on", "off", "status"])
    auto.set_defaults(func=autostart_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["run"] + (argv or []))
    if args.command == "folders" and args.action != "list" and not args.path:
        parser.error("path가 필요합니다.")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
