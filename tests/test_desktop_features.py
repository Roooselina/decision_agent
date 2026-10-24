from __future__ import annotations

import base64
import datetime
import json
import time

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient

from decision_agent.connectors.http import HttpResponse
from decision_agent.desktop import secrets as secret_store
from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.providers import build_provider
from decision_agent.desktop.state import StateDB
from decision_agent.desktop.webapp import create_web_app

TOKEN = "t" * 32
PORT = 8765
HEADERS = {"X-DA-Token": TOKEN}


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


class FakeHttp:
    def __init__(self):
        self.routes = []
        self.calls = []

    def add(self, method, prefix, handler):
        self.routes.append((method, prefix, handler))

    def request(self, method, url, *, headers=None, params=None, form=None, json_body=None, timeout=30.0):
        self.calls.append((method, url))
        for route_method, prefix, handler in reversed(self.routes):
            if route_method == method and url.startswith(prefix):
                return HttpResponse(200, json.dumps(handler(params or {}, json_body, form)).encode("utf-8"))
        return HttpResponse(404, b"{}")


def make_app(root, http=None, secrets=None, **overrides):
    settings = dh.make_settings(root, **overrides)
    return DecisionAgentApp(
        settings,
        bundle_factory=lambda s: build_provider(s, key_getter=lambda: None),
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
        connector_http=http,
        secret_store=secrets or MemorySecrets(),
    )


@pytest.fixture
def app(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    controller = make_app(root)
    controller.start()
    assert controller.wait_idle(60)
    yield controller, root
    controller.stop()


def slack_http(text):
    http = FakeHttp()
    stamp = time.time() - 3600
    http.add(
        "GET",
        "https://slack.com/api/conversations.history",
        lambda p, b, f: {"ok": True, "messages": [{"ts": str(stamp), "text": text}]},
    )
    return http


def test_note_is_analysed_and_can_be_deleted(app):
    controller, root = app
    before = len(controller.files())
    note = controller.add_note("발표는 다음 주 월요일로 연기한다", "2026-10-22")
    assert controller.wait_idle(60)
    paths = [row["path"] for row in controller.files()]
    assert any(path.startswith("@notes/") for path in paths) and len(paths) == before + 1
    assert controller.list_notes()[0]["id"] == note["id"]
    assert controller.delete_note(note["id"])
    assert controller.wait_idle(60)
    assert not any(row["path"].startswith("@notes/") for row in controller.files())
    assert not (root / "@notes").exists()


def test_notes_need_text_and_a_folder(tmp_path):
    controller = make_app(dh.make_watch_dir(tmp_path))
    controller.start()
    try:
        assert controller.wait_idle(60)
        with pytest.raises(ValueError):
            controller.add_note("   ")
    finally:
        controller.stop()
    empty = DecisionAgentApp(
        dh.make_settings(tmp_path, allowed_folders=[]),
        bundle_factory=lambda s: build_provider(s, key_getter=lambda: None),
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
    )
    with pytest.raises(Exception, match="폴더"):
        empty.add_note("x는 y로 한다")


def test_imported_markdown_is_read_but_never_written_to_the_folder(app):
    controller, root = app
    listing = sorted(p.name for p in root.rglob("*"))
    content = "# 회의록\n\n- 2026-10-20 발표 일정을 다음 주 월요일로 연기한다.\n".encode()
    name = controller.import_file("extra.md", content)
    assert controller.wait_idle(60)
    assert any(row["path"].endswith("extra.md") and row["path"].startswith("@") for row in controller.files())
    assert name
    assert sorted(p.name for p in root.rglob("*")) == listing


def test_import_rejects_unsupported_and_unknown_formats(app):
    controller, _ = app
    with pytest.raises(Exception, match="hwp|변환|지원"):
        controller.import_file("minutes.hwp", b"x")
    with pytest.raises(Exception, match="지원하지 않는"):
        controller.import_file("minutes.exe", b"x")


def test_import_path_traversal_in_name_is_flattened(app, tmp_path):
    controller, root = app
    controller.import_file("../../evil.md", "# 제목\n\n- 일정을 변경한다.\n".encode())
    assert controller.wait_idle(60)
    assert not (tmp_path / "evil.md").exists()
    assert not (root.parent.parent / "evil.md").exists()


def test_connector_cycle_stores_documents_and_status(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    secrets = MemorySecrets()
    secrets.set("connector.slack.bot_token", "xoxb-1")
    controller = make_app(root, http=slack_http("발표 일정을 다음 주 월요일로 연기하기로 결정했습니다."), secrets=secrets)
    controller.start()
    try:
        assert controller.wait_idle(60)
        controller.save_connector_config("slack", True, {"channels": ["C1"]})
        assert controller.run_connectors("slack")
        assert controller.wait_idle(60)
        assert any(row["path"].startswith("@slack/") for row in controller.files())
        slack = next(row for row in controller.connector_overview() if row["id"] == "slack")
        assert slack["enabled"] and slack["status"]["ok"] and slack["has_secrets"]["bot_token"]
        assert slack["values"]["channels"] == ["C1"]
    finally:
        controller.stop()


def test_connector_without_required_options_reports_the_problem(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    controller = make_app(root, http=slack_http("x"))
    controller.start()
    try:
        assert controller.wait_idle(60)
        controller.save_connector_config("slack", True, {})
        controller.run_connectors("slack")
        assert controller.wait_idle(60)
        slack = next(row for row in controller.connector_overview() if row["id"] == "slack")
        assert slack["status"]["ok"] is False and slack["status"]["message"]
        assert any(row["level"] == "error" for row in controller.activity.recent(50))
    finally:
        controller.stop()


def test_disconnect_removes_secrets_documents_and_status(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    secrets = MemorySecrets()
    secrets.set("connector.slack.bot_token", "xoxb-1")
    controller = make_app(root, http=slack_http("발표 일정을 다음 주 월요일로 연기하기로 결정했습니다."), secrets=secrets)
    controller.start()
    try:
        assert controller.wait_idle(60)
        controller.save_connector_config("slack", True, {"channels": ["C1"]})
        controller.run_connectors("slack")
        assert controller.wait_idle(60)
        controller.disconnect_connector("slack")
        assert controller.wait_idle(60)
        assert not any(row["path"].startswith("@slack/") for row in controller.files())
        assert secrets.get("connector.slack.bot_token") is None
        slack = next(row for row in controller.connector_overview() if row["id"] == "slack")
        assert slack["enabled"] is False and slack["status"] is None
    finally:
        controller.stop()


def test_connector_secrets_never_reach_the_settings_file(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    settings_file = tmp_path / "settings.json"
    secrets = MemorySecrets()
    controller = make_app(root, secrets=secrets)
    controller.settings_file = settings_file
    controller.start()
    try:
        assert controller.wait_idle(60)
        controller.connectors.save_secret("slack", "bot_token", "xoxb-super-secret")
        controller.save_connector_config("slack", True, {"channels": ["C1"]})
        text = settings_file.read_text(encoding="utf-8")
        assert "xoxb-super-secret" not in text and "C1" in text
    finally:
        controller.stop()


def test_quick_preview_finds_related_items_without_saving(app):
    controller, _ = app
    notes_before = len(controller.list_notes())
    result = controller.quick_preview("MVP 이메일 인증을 이번 개발 범위에서 뺀다")
    assert any("이메일" in a["text"] for a in result["artifacts"])
    assert result["checked_kinds"]
    assert len(controller.list_notes()) == notes_before
    with pytest.raises(Exception, match="입력"):
        controller.quick_preview(" ")


def test_search_covers_findings_files_and_notes(app):
    controller, _ = app
    controller.add_note("로그인 화면 디자인을 확정한다", "2026-10-22")
    assert controller.wait_idle(60)
    result = controller.search("이메일")
    assert result["findings"] and result["decisions"]
    assert controller.search("tasks")["files"]
    assert controller.search("로그인 화면")["notes"]
    assert controller.search("") == {"decisions": [], "findings": [], "files": [], "notes": []}


def test_draft_for_demo_judge_has_message_but_no_model_text(app):
    controller, _ = app
    row = next(f for f in controller.findings() if f["label"] == "INCONSISTENT")
    draft = controller.draft_fix(row["root"], row["topic"], row["artifact_id"])
    assert draft["model_used"] is False and draft["revised_text"] == ""
    assert row["source_doc"] in draft["message"]
    with pytest.raises(Exception, match="찾을 수 없"):
        controller.draft_fix(row["root"], row["topic"], "nope")


def test_digest_is_sent_once_a_day_after_the_set_time(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    now = {"value": datetime.datetime(2026, 10, 22, 8, 0)}
    toasts = []
    from decision_agent.desktop.notify import CallbackNotifier

    controller = DecisionAgentApp(
        dh.make_settings(root, digest_enabled=True, digest_time="09:00"),
        notifier=CallbackNotifier(lambda t, m: toasts.append(t)),
        bundle_factory=lambda s: build_provider(s, key_getter=lambda: None),
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
        clock=lambda: now["value"],
    )
    controller.start()
    try:
        assert controller.wait_idle(60)
        toasts.clear()
        controller.digest_tick()
        assert toasts == []
        now["value"] = datetime.datetime(2026, 10, 22, 9, 5)
        controller.digest_tick()
        controller.digest_tick()
        assert toasts.count("오늘의 결정 점검 요약") == 1
        latest = controller.latest_digest()
        assert latest["name"] == "2026-10-22.md" and "반영 안 됨" in latest["markdown"]
        now["value"] = datetime.datetime(2026, 10, 23, 9, 1)
        controller.digest_tick()
        assert toasts.count("오늘의 결정 점검 요약") == 2
    finally:
        controller.stop()


def test_access_report_lists_folders_connectors_and_limits(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    controller = make_app(root)
    controller.start()
    try:
        assert controller.wait_idle(60)
        controller.save_connector_config("slack", True, {"channels": ["C1"]})
        report = controller.access_report()
        assert report["folders"][0]["root"] == str(root.resolve())
        assert [c["id"] for c in report["connectors"]] == ["slack"]
        assert report["provider"]["sends_externally"] is False
        assert any("수정" in line for line in report["never"])
    finally:
        controller.stop()


# web layer


@pytest.fixture
def web(tmp_path, monkeypatch):
    store = {}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(secret_store, "get_api_key", lambda: store.get("k"))
    monkeypatch.setattr(secret_store, "set_api_key", lambda v: store.__setitem__("k", v))
    monkeypatch.setattr(secret_store, "delete_api_key", lambda: store.pop("k", None))
    root = dh.make_watch_dir(tmp_path)
    controller = make_app(root)
    controller.start()
    assert controller.wait_idle(60)
    client = TestClient(create_web_app(controller, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}")
    yield client, controller, root
    controller.stop()


NEW_GET = [
    "/api/activity", "/api/log", "/api/access", "/api/connectors", "/api/notes",
    "/api/search?q=a", "/api/digest",
]
NEW_POST = [
    "/api/provider/test", "/api/connectors/config", "/api/connectors/run", "/api/connectors/disconnect",
    "/api/connectors/oauth/start", "/api/notes", "/api/import/file", "/api/quick/preview",
    "/api/finding/draft", "/api/digest/now",
]


def test_new_endpoints_require_the_token(web):
    client, _, _ = web
    for path in NEW_GET:
        assert client.get(path).status_code == 403, path
    for path in NEW_POST:
        assert client.post(path, json={}).status_code == 403, path
    assert client.delete("/api/notes/abc").status_code == 403


def test_status_exposes_progress_and_overall(web):
    client, _, _ = web
    status = client.get("/api/status", headers=HEADERS).json()
    assert status["overall"]["state"] == "demo"
    progress = status["folders"][0]["progress"]
    assert progress["files_done"] == progress["files_total"] > 0
    assert status["folders"][0]["completion"]["verified"] is False


def test_validation_errors_have_a_plain_message(web):
    client, _, _ = web
    response = client.post("/api/answer", headers=HEADERS, json={"root": "x", "topic": "t", "text": ""})
    assert response.status_code == 422 and isinstance(response.json()["detail"], str)


def test_notes_api_roundtrip_and_validation(web):
    client, controller, _ = web
    ok = client.post("/api/notes", headers=HEADERS, json={"text": "발표는 월요일로 한다", "date": "2026-10-22"})
    assert ok.status_code == 200
    assert controller.wait_idle(60)
    listed = client.get("/api/notes", headers=HEADERS).json()
    assert [n["text"] for n in listed] == ["발표는 월요일로 한다"]
    assert client.post("/api/notes", headers=HEADERS, json={"text": " ", "date": None}).status_code in {400, 422}
    assert client.post("/api/notes", headers=HEADERS, json={"text": "x는 y", "date": "not-a-date"}).status_code == 400
    assert client.delete(f"/api/notes/{listed[0]['id']}", headers=HEADERS).json() == {"ok": True}


def test_import_api_validates_base64_and_format(web):
    client, controller, _ = web
    body = base64.b64encode("# 제목\n\n- 일정을 연기한다.\n".encode()).decode()
    assert client.post("/api/import/file", headers=HEADERS, json={"name": "a.md", "content_base64": body}).status_code == 200
    assert controller.wait_idle(60)
    assert client.post("/api/import/file", headers=HEADERS, json={"name": "a.md", "content_base64": "@@@"}).status_code == 400
    assert client.post("/api/import/file", headers=HEADERS, json={"name": "a.exe", "content_base64": body}).status_code == 400


def test_quick_search_digest_access_and_log_endpoints(web):
    client, _, _ = web
    preview = client.post("/api/quick/preview", headers=HEADERS, json={"text": "이메일 인증 제외"}).json()
    assert preview["artifacts"] and "decisions" in preview
    assert client.post("/api/quick/preview", headers=HEADERS, json={"text": ""}).status_code in {400, 422}
    assert client.get("/api/search?q=이메일", headers=HEADERS).json()["findings"]
    assert client.get("/api/digest", headers=HEADERS).json() == {"name": "", "markdown": ""}
    made = client.post("/api/digest/now", headers=HEADERS).json()
    assert made["markdown"].startswith("# ")
    assert client.get("/api/digest", headers=HEADERS).json()["name"].endswith(".md")
    assert client.get("/api/access", headers=HEADERS).json()["folders"]
    assert "lines" in client.get("/api/log", headers=HEADERS).json()
    assert client.get("/api/activity", headers=HEADERS).json()


def test_provider_test_reports_demo_judge_honestly(web):
    client, _, _ = web
    result = client.post("/api/provider/test", headers=HEADERS).json()
    assert result["verified"] is False and "실제 모델이 아니라" in result["message"]


def test_finding_draft_endpoint(web):
    client, controller, _ = web
    row = next(f for f in controller.findings() if f["label"] == "INCONSISTENT")
    payload = {"root": row["root"], "topic": row["topic"], "artifact_id": row["artifact_id"]}
    assert client.post("/api/finding/draft", headers=HEADERS, json=payload).json()["message"]
    payload["artifact_id"] = "missing"
    assert client.post("/api/finding/draft", headers=HEADERS, json=payload).status_code == 400


def test_connector_endpoints_validate_and_never_echo_secrets(web):
    client, controller, _ = web
    rows = client.get("/api/connectors", headers=HEADERS).json()
    assert {r["id"] for r in rows} >= {"gmail", "google_calendar", "outlook_mail", "outlook_calendar", "imap_mail", "notion", "jira", "slack"}
    assert client.post("/api/connectors/secret", headers=HEADERS, json={"id": "nope", "name": "x", "value": "v"}).status_code == 400
    assert client.post("/api/connectors/secret", headers=HEADERS, json={"id": "slack", "name": "bot_token", "value": "xoxb-secret"}).status_code == 200
    text = client.get("/api/connectors", headers=HEADERS).text
    assert "xoxb-secret" not in text
    slack = next(r for r in json.loads(text) if r["id"] == "slack")
    assert slack["has_secrets"]["bot_token"] is True
    assert client.post("/api/connectors/config", headers=HEADERS, json={"id": "slack", "enabled": True, "options": {"channels": ["C1"]}}).status_code == 200
    assert client.post("/api/connectors/config", headers=HEADERS, json={"id": "bogus", "enabled": True, "options": {}}).status_code == 400
    assert client.request("DELETE", "/api/connectors/secret", headers=HEADERS, json={"id": "slack", "name": "bot_token", "value": ""}).status_code == 200
    assert controller.connectors.secrets.get("connector.slack.bot_token") is None


def test_oauth_start_and_callback_reject_bad_input(web):
    client, _, _ = web
    assert client.post("/api/connectors/oauth/start", headers=HEADERS, json={"id": "slack"}).status_code == 400
    assert client.post("/api/connectors/oauth/start", headers=HEADERS, json={"id": "gmail"}).status_code == 400
    bad = client.get("/oauth/callback?code=abc&state=forged")
    assert bad.status_code == 400 and "연결 실패" in bad.text
    assert client.get("/oauth/callback?error=access_denied").status_code == 400


def test_oauth_start_returns_a_google_login_url_once_configured(web):
    client, _, _ = web
    client.post("/api/connectors/config", headers=HEADERS, json={"id": "gmail", "enabled": True, "options": {"client_id": "cid", "label": "회의록"}})
    url = client.post("/api/connectors/oauth/start", headers=HEADERS, json={"id": "gmail"}).json()["url"]
    assert url.startswith("https://accounts.google.com/") and "code_challenge=" in url
    assert "gmail.readonly" in url and "127.0.0.1" in url


def test_settings_accepts_new_fields_and_rejects_bad_ones(web):
    client, controller, root = web
    ok = client.post("/api/settings", headers=HEADERS, json={"hotkey": "ctrl+shift+q", "digest_enabled": True, "digest_time": "18:30", "embedding_model": "nomic-embed-text"})
    assert ok.status_code == 200 and controller.settings.hotkey == "ctrl+shift+q"
    assert client.post("/api/settings", headers=HEADERS, json={"digest_time": "25:99"}).status_code == 400
    assert client.post("/api/settings", headers=HEADERS, json={"hotkey": "d"}).status_code == 400
    assert client.post("/api/settings", headers=HEADERS, json={"ollama_url": "file:///etc/passwd"}).status_code == 400
    assert client.post("/api/settings", headers=HEADERS, json={"connector_target": "/tmp"}).status_code == 400
    assert client.post("/api/settings", headers=HEADERS, json={"connector_target": str(root)}).status_code == 200
    assert client.post("/api/settings", headers=HEADERS, json={"connectors": {}}).status_code == 400


def test_settings_hooks_run_when_settings_change(web):
    client, controller, _ = web
    seen = []
    controller.settings_hooks.append(lambda s: seen.append(s.hotkey))
    client.post("/api/settings", headers=HEADERS, json={"hotkey": "ctrl+alt+k"})
    assert seen == ["ctrl+alt+k"]


def test_quick_page_and_dashboard_page_are_served_with_nonce(web):
    client, _, _ = web
    first = client.get(f"/?t={TOKEN}&next=/quick", follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == "/quick"
    client.cookies.update(first.cookies)
    quick = client.get("/quick")
    assert quick.status_code == 200 and "빠른 점검" in quick.text
    nonce = quick.headers["content-security-policy"].split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in quick.text
    off_list = client.get(f"/?t={TOKEN}&next=//evil.example", follow_redirects=False)
    assert off_list.headers["location"] == "/"


def test_pages_use_text_nodes_only():
    from decision_agent.desktop.webpage import PAGE, QUICK_PAGE

    for page in (PAGE, QUICK_PAGE):
        for banned in ("innerHTML", "outerHTML", "document.write", "eval(", "insertAdjacentHTML"):
            assert banned not in page
