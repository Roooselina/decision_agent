"""Demo preflight: one command that says whether the live demo can start.

    powershell -ExecutionPolicy Bypass -File tools\\demo_preflight.ps1
    python tools/demo_preflight.py [--folder PATH] [--no-model]

Each line is OK, WARN or FAIL with a one-line fix. The last line is "시연 가능" or the list of
blockers. Reads the running program through its local API (127.0.0.1, the token in the data
folder) and never prints keys or tokens. The model check makes exactly one tiny request
(the same one as "연결 테스트" in the settings); --no-model skips it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from decision_agent.desktop.paths import app_home
from decision_agent.desktop.secrets import get_api_key
from decision_agent.desktop.statedump import read_settings

DEFAULT_FOLDER = r"C:\Users\kuroc\Downloads\demo_bookswap"
REPO = Path(__file__).resolve().parent.parent
WEBVIEW2_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


class Report:
    def __init__(self) -> None:
        self.blockers: list[str] = []

    def line(self, status: str, name: str, detail: str, fix: str = "") -> None:
        text = f"[{status:4}] {name}: {detail}"
        if fix and status != "OK":
            text += f"\n         -> {fix}"
        print(text, flush=True)
        if status == "FAIL":
            self.blockers.append(name)


def api(port: int, path: str, method: str = "GET", timeout: float = 10.0):
    token = (app_home() / "ui_token").read_text(encoding="utf-8").strip()
    data = b"{}" if method == "POST" else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"x-da-token": token, "content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def program_changed_since(commit: str) -> bool | None:
    """True when the program code (src, pyproject.toml) in the repository differs from the
    installed commit; tools and documents do not need a new install."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "diff", "--quiet", commit, "HEAD", "--", "src", "pyproject.toml"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: False, 1: True}.get(out.returncode)


def webview2_version() -> str:
    try:
        import winreg
    except ImportError:
        return ""
    places = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"),
    ]
    for hive, key in places:
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _ = winreg.QueryValueEx(handle, "pv")
                if value and value != "0.0.0.0":
                    return str(value)
        except OSError:
            continue
    return ""


def reachable(host: str, port: int = 443) -> bool:
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


def minutes_since(hhmm_or_iso: str) -> float | None:
    now = datetime.now()
    try:
        if "T" in hhmm_or_iso:
            then = datetime.fromisoformat(hhmm_or_iso)
        else:
            hour, minute = (int(x) for x in hhmm_or_iso.split(":"))
            then = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return (now - then).total_seconds() / 60
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="시연 전 점검")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="시연 폴더")
    parser.add_argument("--no-model", action="store_true", help="모델 요청 1회를 건너뜀")
    args = parser.parse_args()
    folder = str(Path(args.folder))
    r = Report()
    settings = read_settings()
    port = settings.ui_port
    print("시연 전 점검", datetime.now().strftime("%Y-%m-%d %H:%M"), flush=True)

    # 1. The program is running and is the latest build.
    try:
        about = api(port, "/api/about")
    except Exception:  # noqa: BLE001 - anything means the program is not answering
        r.line("FAIL", "프로그램 실행", "실행 중인 프로그램이 응답하지 않습니다.",
               "바탕화면의 프로그램 아이콘으로 실행하고 1~2분 뒤 다시 점검하세요.")
        print("\n시연 불가: 프로그램이 꺼져 있습니다.")
        return 1
    commit = about.get("commit", "")
    changed = program_changed_since(commit) if commit else None
    if changed:
        r.line("WARN", "버전", f"{about.get('version')} 설치본 {commit}, 저장소에 더 새 프로그램 코드가 있음",
               "powershell -ExecutionPolicy Bypass -File tools\\update_windows.ps1 로 최신본을 설치하세요.")
    else:
        r.line("OK", "버전", f"{about.get('version')} (커밋 {commit}, 설치 {about.get('installed_at', '')[:16]})")

    # 2. WebView2 for the program window.
    version = webview2_version()
    if version:
        r.line("OK", "WebView2", version)
    else:
        r.line("WARN", "WebView2", "설치 정보를 찾지 못했습니다(창 대신 브라우저로 열릴 수 있음).",
               "https://go.microsoft.com/fwlink/p/?LinkId=2124703 에서 Evergreen 런타임을 설치하세요.")

    # 3. Network and model key, then one tiny live request.
    if reachable("api.anthropic.com"):
        r.line("OK", "네트워크", "api.anthropic.com 연결됨")
    else:
        r.line("FAIL", "네트워크", "모델 서버에 연결할 수 없습니다.", "와이파이를 확인하거나 휴대폰 테더링으로 바꾸세요.")
    if get_api_key():
        r.line("OK", "모델 키", "저장되어 있음(값은 표시하지 않음)")
    else:
        r.line("FAIL", "모델 키", "저장된 키가 없습니다.", "설정 > 연결 및 AI에서 키를 저장하세요.")
    if args.no_model:
        r.line("WARN", "모델 요청", "건너뜀(--no-model)", "시연 직전에는 --no-model 없이 한 번 돌리세요.")
    else:
        try:
            result = api(port, "/api/provider/test", "POST", timeout=60)
            if result.get("ok") and result.get("verified"):
                r.line("OK", "모델 요청", "작은 요청 1회 성공 (요청 1회 사용)")
            elif result.get("ok"):
                r.line("FAIL", "모델 요청", "실제 모델이 아니라 로컬 판정기입니다.",
                       "설정에서 제공자, 모델, 외부 전송 허용을 확인하세요.")
            else:
                r.line("FAIL", "모델 요청", result.get("message", "실패")[:120],
                       "키와 네트워크를 확인하고, 안 되면 대체 계획(녹화본)으로 가세요.")
        except Exception as exc:  # noqa: BLE001
            r.line("FAIL", "모델 요청", type(exc).__name__, "프로그램을 다시 시작한 뒤 다시 점검하세요.")

    # 4. Gmail, mail refresh, send consent, calendar.
    connectors = {c["id"]: c for c in api(port, "/api/connectors")}
    gmail = connectors.get("gmail", {})
    if gmail.get("connected") and (gmail.get("status") or {}).get("ok", True):
        r.line("OK", "Gmail 연결", "연결됨")
    else:
        r.line("FAIL", "Gmail 연결", (gmail.get("status") or {}).get("message", "연결 안 됨")[:120],
               "설정 > 연결 및 AI > Gmail에서 '계정 연결'을 다시 하세요.")
    mail = api(port, "/api/mail/state").get("status", {})
    sources = mail.get("sources", [])
    fresh = [s for s in sources if s.get("ok") and (minutes_since(s.get("checked_at", "")) or 99) < 5]
    interval = mail.get("interval")
    if mail.get("running") and fresh:
        r.line("OK", "메일 새로고침", f"{mail.get('line', '')}, {int(interval or 0)}초마다")
    else:
        bad = "; ".join(s.get("message", "") for s in sources if not s.get("ok"))[:120]
        r.line("FAIL", "메일 새로고침", bad or "최근 5분 안에 확인한 기록이 없습니다.",
               "메일 화면에서 새로고침을 눌러 보고, 안 되면 Gmail을 다시 연결하세요.")
    send = api(port, "/api/mail/send/status")
    if send.get("granted") and send.get("enabled"):
        r.line("OK", "메일 보내기", "보내기 권한과 설정 켜짐")
    else:
        what = "권한(gmail.send) 없음" if not send.get("granted") else "보내기 설정 꺼짐"
        r.line("WARN", "메일 보내기", what + " (스토리 6단계만 해당, 선택)",
               "설정 > 메일 > 보내기에서 켜고 Google 동의 화면에서 보내기 권한을 허용하세요.")
    calendar = connectors.get("google_calendar", {})
    if calendar.get("connected") and (calendar.get("status") or {}).get("ok", True):
        r.line("OK", "캘린더 연결", (calendar.get("status") or {}).get("summary", "연결됨"))
    else:
        r.line("WARN", "캘린더 연결", "연결 안 됨", "설정 > 캘린더 연결 관리에서 연결하세요(시연에 필수는 아님).")

    # 5. The demo folder is registered and its first check is done (warm cache).
    status = api(port, "/api/status")
    by_root = {str(Path(f["root"])).lower(): f for f in status.get("folders", [])}
    entry = by_root.get(folder.lower())
    if entry is None:
        r.line("FAIL", "시연 폴더", f"등록 안 됨: {folder}", "tools\\demo_reset.ps1 을 실행하세요.")
    else:
        state = entry.get("state", {})
        completion = entry.get("completion") or {}
        counts = entry.get("open_findings") or {}
        if state.get("state") == "verified" and completion.get("verified"):
            r.line("OK", "시연 폴더", f"{state.get('label')} (첫 점검 끝, 캐시 있음)")
        elif entry.get("pending_confirm"):
            r.line("FAIL", "시연 폴더", "첫 점검이 비용 확인을 기다립니다.", "tools\\demo_reset.ps1 로 웜업을 끝내세요.")
        else:
            r.line("FAIL", "시연 폴더", f"{state.get('label', '?')} {state.get('detail', '')}"[:120],
                   "tools\\demo_reset.ps1 로 웜업을 끝내세요.")
        if entry.get("failure"):
            r.line("FAIL", "최근 점검", str(entry["failure"].get("message", ""))[:120], "tools\\demo_reset.ps1 을 다시 실행하세요.")
        if counts:
            r.line("OK", "결과 수", ", ".join(f"{k} {v}" for k, v in counts.items()))
        others = [f for f in status.get("folders", []) if str(Path(f["root"])).lower() != folder.lower()]
        busy = [Path(f["root"]).name for f in others if (f.get("state") or {}).get("state") in {"checking", "awaiting_confirm"}]
        if busy:
            r.line("WARN", "다른 폴더", "확인 대기 또는 점검 중: " + ", ".join(busy),
                   "시연 중 확인 창이 뜨지 않게, 해당 폴더를 일시 중지하거나 '나중에'를 누르세요.")

    # 6. Settings for the demo.
    s = read_settings()
    from decision_agent.desktop.demo_profile import status as demo_status
    demo = demo_status()
    if demo.get("on") and s.debounce_seconds <= 5:
        r.line("OK", "시연 설정", f"켜짐: 변경 대기 {s.debounce_seconds:g}초, 메일 {s.mail_poll_seconds}초, "
               f"점검 수준 {s.cost_profile}, 확인 기준 {s.confirm_over_calls}회")
    else:
        r.line("FAIL", "시연 설정", f"꺼짐(변경 대기 {s.debounce_seconds:g}초)",
               "powershell -ExecutionPolicy Bypass -File tools\\demo_profile.ps1 on")

    # 7. Disk.
    free_gb = shutil.disk_usage(str(app_home())).free / 1e9
    if free_gb >= 2:
        r.line("OK", "디스크", f"남은 공간 {free_gb:.0f}GB")
    else:
        r.line("FAIL", "디스크", f"남은 공간 {free_gb:.1f}GB", "필요 없는 파일을 지워 2GB 이상 비우세요.")
    r.line("OK", "글꼴", "추가 글꼴 필요 없음(시스템 글꼴 사용)")

    print()
    if r.blockers:
        print("시연 불가: " + ", ".join(r.blockers))
        return 1
    print("시연 가능")
    return 0


if __name__ == "__main__":
    sys.exit(main())
