"""Open every dashboard screen in headless Chromium and save screenshots.

Checks for console errors and uncaught page errors in light and dark themes at 1100 and 1600
pixels wide. Uses a temporary app home and a copy of the demo folder, so nothing real is touched.

    pip install playwright && python -m playwright install chromium
    python tools/ui_check.py --out out/ui_check
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ROUTES = ["home", "results", "questions", "folders", "accounts", "notes", "digest", "activity",
          "settings", "profile"]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start(home: Path, folders: list[Path], picker: Path | None):
    os.environ["DECISION_AGENT_HOME"] = str(home)
    from decision_agent.desktop.app import DecisionAgentApp
    from decision_agent.desktop.settings import AppSettings
    from decision_agent.desktop.webapp import WebServer, create_web_app

    settings = AppSettings(allowed_folders=[str(f) for f in folders], provider="mock", rescan_minutes=0,
                           debounce_seconds=0.5, display_name="김하늘")
    controller = DecisionAgentApp(settings, settings_file=home / "settings.json", start_watchers=False)
    controller.folder_picker = (lambda: str(picker)) if picker else None
    controller.path_opener = lambda path: None
    controller.start()
    controller.wait_idle(120)
    port = free_port()
    token = "ui-check-token-0123456789abcdef"
    server = WebServer(create_web_app(controller, token, port, url_opener=lambda url: True), port)
    server.start()
    return controller, server, port, token


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "out" / "ui_check"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    work = Path(tempfile.mkdtemp(prefix="da-ui-"))
    watch = work / "project"
    shutil.copytree(ROOT / "data" / "demo", watch)
    (watch / "archive" / "old").mkdir(parents=True)
    (watch / "archive" / "old" / "notes.md").write_text("# 옛 회의\n- 결정: 없음\n", encoding="utf-8")
    other = work / "other-project"
    shutil.copytree(ROOT / "data" / "demo", other)
    problems: list[str] = []

    controller, server, port, token = start(work / "home", [watch], other)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for scheme in ("light", "dark"):
                for width, height in ((1100, 720), (1600, 900)):
                    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme,
                                                  locale="ko-KR")
                    page = context.new_page()
                    tag = f"{scheme}-{width}"
                    page.on("console", lambda m, t=tag: problems.append(f"[{t}] console {m.type}: {m.text}")
                            if m.type in ("error", "warning") else None)
                    page.on("pageerror", lambda e, t=tag: problems.append(f"[{t}] pageerror: {e}"))
                    page.goto(f"http://127.0.0.1:{port}/?t={token}")
                    page.wait_for_selector(".nav-item")
                    for route in ROUTES:
                        page.goto(f"http://127.0.0.1:{port}/#/{route}")
                        page.wait_for_timeout(700)
                        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
                        if overflow:
                            problems.append(f"[{tag}] horizontal overflow on {route}")
                        page.screenshot(path=str(out / f"{tag}-{route}.png"), full_page=False)
                    if width == 1600:
                        page.goto(f"http://127.0.0.1:{port}/#/results")
                        page.wait_for_timeout(600)
                        page.click(".finding-row")
                        page.wait_for_timeout(400)
                        page.screenshot(path=str(out / f"{tag}-results-drawer.png"))
                        page.keyboard.press("Escape")
                        page.goto(f"http://127.0.0.1:{port}/#/folders")
                        page.wait_for_timeout(600)
                        page.click("text=하위 폴더 선택")
                        page.wait_for_timeout(700)
                        page.screenshot(path=str(out / f"{tag}-folders-tree.png"), full_page=True)
                        page.click("text=폴더 추가")
                        page.wait_for_timeout(900)
                        page.screenshot(path=str(out / f"{tag}-folders-preview.png"))
                        page.keyboard.press("Escape")
                        page.goto(f"http://127.0.0.1:{port}/#/accounts")
                        page.wait_for_timeout(600)
                        page.click(".svc-card button:has-text(\"연결\")")
                        page.wait_for_timeout(300)
                        page.screenshot(path=str(out / f"{tag}-accounts-wizard.png"))
                        page.keyboard.press("Escape")
                        page.goto(f"http://127.0.0.1:{port}/#/profile")
                        page.wait_for_timeout(600)
                        page.screenshot(path=str(out / f"{tag}-profile-full.png"), full_page=True)
                    context.close()
            quick = browser.new_context(viewport={"width": 480, "height": 640})
            qpage = quick.new_page()
            qpage.on("pageerror", lambda e: problems.append(f"[quick] pageerror: {e}"))
            qpage.on("console", lambda m: problems.append(f"[quick] console {m.type}: {m.text}")
                     if m.type in ("error", "warning") else None)
            qpage.goto(f"http://127.0.0.1:{port}/?t={token}&next=/quick")
            qpage.wait_for_timeout(800)
            qpage.fill("textarea", "이메일 인증은 MVP에서 제외한다")
            qpage.click("text=관련 항목 찾기")
            qpage.wait_for_timeout(800)
            qpage.screenshot(path=str(out / "quick-480x640.png"))
            browser.close()
    finally:
        server.stop()
        controller.stop()

    # first run: no folders, so the onboarding wizard appears
    controller, server, port, token = start(work / "home2", [], other)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1100, "height": 720})
            page.on("pageerror", lambda e: problems.append(f"[onboarding] pageerror: {e}"))
            page.on("console", lambda m: problems.append(f"[onboarding] console {m.type}: {m.text}")
                    if m.type in ("error", "warning") else None)
            page.goto(f"http://127.0.0.1:{port}/?t={token}")
            page.wait_for_selector(".onboarding")
            page.screenshot(path=str(out / "onboarding-1-welcome.png"))
            page.click("text=시작하기")
            page.click(".onb-card button:has-text(\"폴더 선택\")")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out / "onboarding-2-folder.png"), full_page=True)
            page.click("text=이 폴더로 시작")
            page.wait_for_selector("text=판정 방식을 고르세요")
            page.screenshot(path=str(out / "onboarding-3-judge.png"))
            page.click(".onb-card >> text=다음")
            page.wait_for_selector("text=준비가 끝났습니다")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(out / "onboarding-4-done.png"))
            page.click("text=홈으로 이동")
            page.wait_for_timeout(2500)
            page.screenshot(path=str(out / "onboarding-5-home.png"))
            browser.close()
    finally:
        server.stop()
        controller.stop()
    shutil.rmtree(work, ignore_errors=True)

    for line in problems:
        print(line)
    print(f"screenshots: {out}")
    print("problems:", len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
