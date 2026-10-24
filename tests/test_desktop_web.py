from __future__ import annotations

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient

from decision_agent.desktop import secrets as secret_store
from decision_agent.desktop import webpage
from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.providers import build_provider
from decision_agent.desktop.state import StateDB
from decision_agent.desktop.webapp import create_web_app

TOKEN = "t" * 32
PORT = 8765
HEADERS = {"X-DA-Token": TOKEN}


@pytest.fixture
def fake_keys(monkeypatch):
    store = {}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(secret_store, "get_api_key", lambda: store.get("k"))
    monkeypatch.setattr(secret_store, "set_api_key", lambda v: store.__setitem__("k", v))
    monkeypatch.setattr(secret_store, "delete_api_key", lambda: store.pop("k", None))
    return store


@pytest.fixture
def web(tmp_path, fake_keys):
    root = dh.make_watch_dir(tmp_path)
    app = DecisionAgentApp(
        dh.make_settings(root),
        bundle_factory=lambda s: build_provider(s, key_getter=lambda: fake_keys.get("k")),
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
    )
    app.start()
    assert app.wait_idle(60)
    client = TestClient(create_web_app(app, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}")
    yield client, app, root
    app.stop()


def test_api_requires_the_token(web):
    client, _, _ = web
    assert client.get("/api/status").status_code == 403
    assert client.get("/api/status", headers={"X-DA-Token": "wrong"}).status_code == 403
    assert client.get("/api/status", headers=HEADERS).status_code == 200
    assert client.post("/api/rescan", json={}).status_code == 403
    assert client.get("/api/ping").json()["app"] == "decision-agent"


def test_unknown_host_is_rejected(web):
    client, _, _ = web
    response = client.get("/api/ping", headers={"Host": "evil.example:8765"})
    assert response.status_code == 400


def test_page_needs_cookie_and_sets_csp_nonce(web):
    client, _, _ = web
    assert client.get("/").status_code == 403
    assert client.get("/?t=wrong").status_code == 403
    entered = client.get(f"/?t={TOKEN}", follow_redirects=False)
    assert entered.status_code == 303 and "httponly" in entered.headers["set-cookie"].lower()
    page = client.get("/")
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in page.text
    assert "__NONCE__" not in page.text and "__TOKEN__" not in page.text
    assert "script-src 'nonce-" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
    assert page.headers["x-frame-options"] == "DENY"


def test_page_source_never_uses_html_injection():
    assert "innerHTML" not in webpage.PAGE
    assert "outerHTML" not in webpage.PAGE
    assert "document.write" not in webpage.PAGE
    assert "eval(" not in webpage.PAGE


def test_read_endpoints_return_results(web):
    client, _, _ = web
    status = client.get("/api/status", headers=HEADERS).json()
    assert status["inconsistent"] >= 3 and status["provider"]["local_only"] is True
    findings = client.get("/api/findings", headers=HEADERS).json()
    assert any(f["label"] == "INCONSISTENT" for f in findings)
    assert len(client.get("/api/questions", headers=HEADERS).json()) == 1
    files = client.get("/api/files", headers=HEADERS).json()
    assert {"tasks.csv", "schedule.csv"} <= {f["path"] for f in files}


def test_dismiss_and_unknown_status(web):
    client, _, _ = web
    target = next(
        f for f in client.get("/api/findings", headers=HEADERS).json() if f["label"] == "INCONSISTENT"
    )
    body = {"root": target["root"], "topic": target["topic"], "artifact_id": target["artifact_id"]}
    assert client.post("/api/finding/status", headers=HEADERS, json={**body, "status": "bogus"}).status_code == 400
    assert client.post("/api/finding/status", headers=HEADERS, json={**body, "status": "dismissed"}).status_code == 200
    remaining = client.get("/api/findings", headers=HEADERS).json()
    assert all(f["artifact_id"] != target["artifact_id"] for f in remaining)
    missing = {**body, "artifact_id": "nope", "status": "dismissed"}
    assert client.post("/api/finding/status", headers=HEADERS, json=missing).status_code == 404


def test_answer_endpoint_validates_and_resolves(web):
    client, app, _ = web
    question = client.get("/api/questions", headers=HEADERS).json()[0]
    bad = {"root": "/not/watched", "topic": question["topic"], "text": "x"}
    assert client.post("/api/answer", headers=HEADERS, json=bad).status_code == 404
    empty = {"root": question["root"], "topic": question["topic"], "text": ""}
    assert client.post("/api/answer", headers=HEADERS, json=empty).status_code == 422
    good = {"root": question["root"], "topic": question["topic"], "text": "발표 일정만 변경됩니다."}
    assert client.post("/api/answer", headers=HEADERS, json=good).status_code == 200
    assert app.wait_idle(60)
    assert client.get("/api/questions", headers=HEADERS).json() == []


def test_pause_endpoint(web):
    client, app, _ = web
    assert client.post("/api/pause", headers=HEADERS, json={"paused": True}).json() == {"paused": True}
    assert app.paused
    assert client.post("/api/pause", headers=HEADERS, json={"paused": False}).json() == {"paused": False}


def test_settings_whitelist(web):
    client, app, _ = web
    current = client.get("/api/settings", headers=HEADERS).json()
    assert current["has_api_key"] is False and "api_key" not in current
    forbidden = client.post("/api/settings", headers=HEADERS, json={"allowed_folders": ["/"]})
    assert forbidden.status_code == 400 and app.settings.allowed_folders != ["/"]
    assert client.post("/api/settings", headers=HEADERS, json={"ui_port": 1}).status_code == 400
    invalid = client.post("/api/settings", headers=HEADERS, json={"analysis_mode": "weird"})
    assert invalid.status_code == 400
    ok = client.post("/api/settings", headers=HEADERS, json={"notify": False, "debounce_seconds": 12})
    assert ok.status_code == 200
    assert app.settings.notify is False and app.settings.debounce_seconds == 12
    assert app.wait_idle(60)


def test_folder_endpoints_reject_dangerous_paths(web, tmp_path):
    client, app, root = web
    for path in ("/", str(tmp_path / "does-not-exist"), str(root / "meetings")):
        response = client.post("/api/folders/add", headers=HEADERS, json={"path": path})
        assert response.status_code == 400, path
    other = dh.make_watch_dir(tmp_path, "other")
    added = client.post("/api/folders/add", headers=HEADERS, json={"path": str(other)})
    assert added.status_code == 200 and app.wait_idle(60)
    removed = client.post("/api/folders/remove", headers=HEADERS, json={"path": str(other)})
    assert removed.json() == {"ok": True}
    assert app.wait_idle(60)


def test_api_key_is_write_only_and_gates_provider(web, fake_keys):
    client, app, _ = web
    short = client.post("/api/secret/api-key", headers=HEADERS, json={"value": "abc"})
    assert short.status_code == 422
    assert client.post("/api/secret/api-key", headers=HEADERS, json={"value": "sk-test-1234567890"}).status_code == 200
    assert fake_keys["k"] == "sk-test-1234567890"
    assert app.wait_idle(60)
    shown = client.get("/api/settings", headers=HEADERS)
    assert shown.json()["has_api_key"] is True and "sk-test" not in shown.text
    status = client.get("/api/status", headers=HEADERS)
    assert "sk-test" not in status.text and status.json()["provider"]["local_only"] is True
    assert client.delete("/api/secret/api-key", headers=HEADERS).status_code == 200
    assert "k" not in fake_keys
    assert app.wait_idle(60)
