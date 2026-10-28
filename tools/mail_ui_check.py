"""Headless check of the mail screens: the home mail card and the mail page.

Runs the app with a temporary home, a generated copy of demo_capstone and a fake Gmail account
holding the five demo threads (tools/fake_gmail.py), lets the real mail poller read them, then
opens the screens in Chromium at 900, 1200 and 1600 pixels in light and dark themes.

Checks, failing with a list of problems:
- no element of the home mail card extends outside the card, and no two home cards overlap
- no horizontal page overflow
- no console errors or uncaught page errors
- the conversation of thread T2 shows four messages, two of them mine

    python tools/mail_ui_check.py --out out/mail_ui_check
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

WIDTHS = (900, 1200, 1600)
# Every descendant of the card, clipped by its nearest scrolling ancestor, must stay in the card.
CARD_BOUNDS_JS = """
(card) => {
  const box = card.getBoundingClientRect();
  const bad = [];
  for (const el of card.querySelectorAll('*')) {
    let r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    for (let p = el.parentElement; p && p !== card; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (/(auto|scroll|hidden)/.test(s.overflowY + s.overflowX)) {
        const c = p.getBoundingClientRect();
        r = {left: Math.max(r.left, c.left), right: Math.min(r.right, c.right), top: Math.max(r.top, c.top), bottom: Math.min(r.bottom, c.bottom)};
      }
    }
    if (r.right <= r.left || r.bottom <= r.top) continue;
    if (r.left < box.left - 1 || r.right > box.right + 1 || r.top < box.top - 1 || r.bottom > box.bottom + 1) {
      bad.push(el.className || el.tagName);
    }
  }
  return bad.slice(0, 5);
}
"""
OVERLAP_JS = """
() => {
  const cards = Array.from(document.querySelectorAll('.home-card, .home-grid ~ * .card, .page > .card, .page > section'));
  const rects = cards.map(c => c.getBoundingClientRect()).filter(r => r.width && r.height);
  let hits = 0;
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const a = rects[i], b = rects[j];
    if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) hits++;
  }
  return hits;
}
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start(work: Path):
    os.environ["DECISION_AGENT_HOME"] = str(work / "home")
    import make_demo_capstone
    from fake_gmail import FakeGmailHttp, logged_in

    from decision_agent.desktop.app import DecisionAgentApp
    from decision_agent.desktop.settings import AppSettings, ConnectorSettings, MailSource
    from decision_agent.desktop.webapp import WebServer, create_web_app

    watch = work / "demo_capstone"
    make_demo_capstone.write_folder("capstone", watch, pdf=False)
    settings = AppSettings(allowed_folders=[str(watch)], provider="mock", rescan_minutes=0, debounce_seconds=0.5,
                           display_name="이은정", onboarding_done=True)
    settings.connectors["gmail"] = ConnectorSettings(enabled=True, options={"client_id": "cid"})
    settings.mail_sources = [MailSource(id="bookswap", name="BookSwap 팀", connector="gmail", labels=["BookSwap"],
                                        team="BookSwap", purpose="decision")]
    controller = DecisionAgentApp(settings, settings_file=work / "home" / "settings.json", start_watchers=False,
                                  connector_http=FakeGmailHttp(), secret_store=logged_in())
    controller.path_opener = lambda path: None
    controller.mail_analysis_delay = 1.0
    controller.start()
    controller.wait_idle(180)
    controller.check_mail_now()
    deadline = time.time() + 60
    while controller.maildb.count() < 17 and time.time() < deadline:
        time.sleep(0.2)
    label = controller.label_create("발표", "red")
    for item in controller.thread_list({})["items"]:
        if item["subject"].startswith("최종 발표"):
            controller.thread_labels_set(item["id"], [label["id"]])
    time.sleep(1.5)
    controller.wait_idle(180)
    port = free_port()
    token = "mail-ui-check-token-0123456789ab"
    server = WebServer(create_web_app(controller, token, port, url_opener=lambda url: True), port)
    server.start()
    return controller, server, port, token


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "out" / "mail_ui_check"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    controller, server, port, token = start(Path(tempfile.mkdtemp(prefix="da-mail-ui-")))
    t2 = next(t["id"] for t in controller.thread_list({})["items"] if t["subject"] == "배포 서버 비용 문의")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for scheme in ("light", "dark"):
                for width in WIDTHS:
                    tag = f"{scheme}-{width}"
                    context = browser.new_context(viewport={"width": width, "height": 860}, color_scheme=scheme, locale="ko-KR")
                    context.add_init_script("try { localStorage.setItem('da.home.panel', 'mail'); } catch (e) {}")
                    page = context.new_page()
                    page.on("console", lambda m, t=tag: problems.append(f"[{t}] console {m.type}: {m.text}")
                            if m.type == "error" else None)
                    page.on("pageerror", lambda e, t=tag: problems.append(f"[{t}] pageerror: {e}"))
                    page.goto(f"http://127.0.0.1:{port}/?t={token}")
                    page.wait_for_selector(".home-mail-row", timeout=20000)
                    page.wait_for_timeout(500)
                    card = page.query_selector(".mail-card")
                    outside = page.evaluate(CARD_BOUNDS_JS, card)
                    if outside:
                        problems.append(f"[{tag}] home mail card: elements outside the card: {outside}")
                    if page.evaluate(OVERLAP_JS):
                        problems.append(f"[{tag}] home cards overlap")
                    rows = page.locator(".home-mail-row").count()
                    if rows != 5:
                        problems.append(f"[{tag}] home card shows {rows} threads, expected 5")
                    if page.locator(".mail-card select, .mail-card input, .mail-card details").count():
                        problems.append(f"[{tag}] home mail card still has filter controls")
                    page.screenshot(path=str(out / f"{tag}-home.png"))
                    page.goto(f"http://127.0.0.1:{port}/#/mail")
                    page.wait_for_selector(".trow", timeout=20000)
                    page.wait_for_timeout(400)
                    page.screenshot(path=str(out / f"{tag}-mail-list.png"))
                    page.goto(f"http://127.0.0.1:{port}/#/mail?t={t2}")
                    page.wait_for_selector(".msg", timeout=20000)
                    page.wait_for_timeout(500)
                    if page.locator(".msg").count() != 4 or page.locator(".msg.mine").count() != 2:
                        problems.append(f"[{tag}] T2 conversation does not show 4 messages with 2 of mine")
                    if page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
                        problems.append(f"[{tag}] horizontal overflow on the mail page")
                    page.screenshot(path=str(out / f"{tag}-mail-thread.png"))
                    context.close()
            browser.close()
    finally:
        server.stop()
        controller.stop()
    for problem in problems:
        print(problem)
    print(f"screenshots in {out}; problems: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
