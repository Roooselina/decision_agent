"""Mail diagnostics: explicit counts, label lookup, preview, account line, permission checks."""

from __future__ import annotations

import base64
import datetime
import importlib.util
import json
import logging
import urllib.parse
from pathlib import Path

import pytest
from test_connectors import FakeHttp, FakeImap, MemorySecrets, context, gmail_labels

from decision_agent.connectors import imap as imap_module
from decision_agent.connectors.base import ConnectorError, check_response
from decision_agent.connectors.google import Gmail
from decision_agent.connectors.http import HttpResponse
from decision_agent.connectors.imap import ImapMail
from decision_agent.connectors.microsoft import OutlookMail
from decision_agent.connectors.oauth import MISSING_SCOPE, OAuthApp, OAuthManager
from decision_agent.desktop.connector_manager import ConnectorManager
from decision_agent.desktop.settings import AppSettings, ConnectorSettings

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TODAY = datetime.date(2026, 10, 22)
STAMP = str(int(datetime.datetime(2026, 10, 20, 12).timestamp() * 1000))


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def message(subject: str, payload: dict, msg_id: str = "m1") -> dict:
    payload = {"headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": "lead@team.kr"}], **payload}
    return {"id": msg_id, "internalDate": STAMP, "payload": payload}


def gmail_with(http: FakeHttp, messages: dict[str, dict], label: str = "회의록") -> Gmail:
    gmail_labels(http, label, "팀/공지")
    http.add("GET", f"{API}/messages", lambda p, b, f: {"messages": [{"id": i} for i in messages]})
    for msg_id, body in messages.items():
        http.add("GET", f"{API}/messages/{msg_id}", lambda p, b, f, body=body: body)
    return Gmail({"client_id": "cid", "label": label}, context(http))


def test_unknown_label_lists_the_existing_labels():
    http = FakeHttp()
    gmail = gmail_with(http, {})
    gmail.opts["label"] = "회의 록"
    with pytest.raises(ConnectorError) as raised:
        gmail.fetch(TODAY)
    assert str(raised.value) == "라벨 '회의 록'을(를) 찾지 못했습니다. 있는 라벨: 팀/공지, 회의록"
    assert not any(c["url"].endswith("/messages") for c in http.calls)


def test_label_lookup_is_case_insensitive_trimmed_nested_and_accepts_system_ids():
    http = FakeHttp()
    gmail = gmail_with(http, {})
    labels = gmail.user_labels()
    assert gmail.resolve_label("  팀/공지 ", labels) == "L2"
    assert gmail.resolve_label("inbox", labels) == "INBOX"
    gmail_labels(http, "Meeting Notes")
    assert gmail.resolve_label("meeting notes", gmail.user_labels()) == "L1"


def test_nothing_matched_says_so_with_the_label_and_days():
    http = FakeHttp()
    outcome = gmail_with(http, {}).fetch(TODAY)
    assert outcome.summary == "조건에 맞는 메일이 없습니다 (라벨: 회의록, 최근 14일)"
    assert outcome.stats["matched"] == 0 and outcome.docs == {}


def test_refused_label_listing_falls_back_to_the_query():
    http = FakeHttp()
    http.add("GET", f"{API}/labels", lambda p, b, f: {"error": {"code": 403}}, status=403)
    http.add("GET", f"{API}/messages", lambda p, b, f: {})
    outcome = Gmail({"client_id": "cid", "label": "회의록"}, context(http)).fetch(TODAY)
    listing = next(c for c in http.calls if c["url"].endswith("/messages"))
    assert listing["params"]["q"] == 'label:"회의록" newer_than:14d' and "labelIds" not in listing["params"]
    assert outcome.summary.startswith("조건에 맞는 메일이 없습니다")


def test_unreadable_bodies_are_counted_and_keep_the_subject():
    http = FakeHttp()
    image_only = message("제3차 미래포럼 행사 안내(26.11.4. 서울 글래드 여의도)", {
        "mimeType": "text/html", "body": {"data": b64('<p><img src="cid:poster"></p>')}}, "img")
    pdf_only = message("회의 자료 송부", {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "application/pdf", "filename": "자료.pdf", "body": {"attachmentId": "PDF1"}}]}, "pdf")
    large = message("결정 사항", {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/html", "body": {"attachmentId": "BIG1", "size": 900000}}]}, "big")
    quoted = message("", {"mimeType": "text/plain", "body": {"data": b64("> 인용만 있음")}}, "empty")
    gmail = gmail_with(http, {"img": image_only, "pdf": pdf_only, "big": large, "empty": quoted})
    http.add("GET", f"{API}/messages/big/attachments/BIG1", lambda p, b, f: {"data": b64("<p>- 발표는 취소한다</p>")})
    outcome = gmail.fetch(TODAY)
    expected = {
        "matched": 4, "read": 1, "subject_only": 2, "skipped_empty": 1, "skipped_filtered": 0,
        "skipped_prefilter": 0, "known": 0,
    }
    assert {key: outcome.stats[key] for key in expected} == expected
    assert outcome.summary == (
        "4건 찾음, 1건 읽음, 제목만 사용 2건, 1건은 본문이 비어 있거나 인용만 있어 건너뜀"
    )
    texts = {
        doc.meta["msg_id"]: [b.text for b in doc.sections[0].blocks][1:] for doc in outcome.docs.values()
    }
    assert texts["big"] == ["발표는 취소한다"]
    assert texts["img"] == ["제3차 미래포럼 행사 안내(26.11.4. 서울 글래드 여의도)"]
    assert texts["pdf"] == ["회의 자료 송부"]
    # The PDF itself is never downloaded.
    assert not any("PDF1" in c["url"] for c in http.calls)


def test_google_error_reasons_get_short_hints_without_tokens():
    def failing(reason: str, status: int = 403) -> HttpResponse:
        body = {"error": {"code": status, "message": "Gmail API has not been used", "errors": [{"reason": reason}]}}
        return HttpResponse(status, json.dumps(body).encode())

    with pytest.raises(ConnectorError, match="API를 사용 설정했는지") as raised:
        check_response(failing("accessNotConfigured"), "Gmail")
    assert "Gmail API has not been used" in str(raised.value) and "Bearer" not in str(raised.value)
    with pytest.raises(ConnectorError, match="권한이 부족합니다"):
        check_response(failing("insufficientPermissions"), "Gmail")
    with pytest.raises(ConnectorError, match="인증에 실패했거나"):
        check_response(HttpResponse(401, b"not json"), "Gmail")


def oauth_app(scopes=("https://www.googleapis.com/auth/gmail.readonly",)) -> OAuthApp:
    return OAuthApp("https://auth.example/auth", "https://auth.example/token", scopes, "cid")


def login(manager: OAuthManager, connector_id: str = "gmail") -> str:
    url = manager.begin(connector_id, oauth_app())
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]


def test_login_without_the_read_scope_is_refused_and_not_stored():
    http = FakeHttp()
    http.add("POST", "https://auth.example/token", lambda p, b, f: {
        "access_token": "A", "refresh_token": "R", "expires_in": 3600, "scope": "openid email"})
    secrets = MemorySecrets()
    manager = OAuthManager(http, secrets, 8765)
    with pytest.raises(ConnectorError) as raised:
        manager.complete(login(manager), "CODE")
    assert str(raised.value) == MISSING_SCOPE
    assert secrets.get("connector.gmail.oauth") is None
    assert manager.connected("gmail", oauth_app()) is False


def test_connected_means_stored_login_with_the_required_scopes():
    http = FakeHttp()
    http.add("POST", "https://auth.example/token", lambda p, b, f: {
        "access_token": "A", "refresh_token": "R", "expires_in": 3600,
        "scope": "https://www.googleapis.com/auth/gmail.readonly openid"})
    secrets = MemorySecrets()
    manager = OAuthManager(http, secrets, 8765)
    manager.complete(login(manager), "CODE")
    assert manager.connected("gmail", oauth_app()) is True
    assert json.loads(secrets.get("connector.gmail.oauth"))["scopes"][0].endswith("gmail.readonly")
    # A login stored before scopes were recorded keeps working.
    secrets.set("connector.old.oauth", json.dumps({"refresh_token": "R"}))
    assert manager.connected("old", oauth_app()) is True
    secrets.set("connector.cut.oauth", json.dumps({"refresh_token": "R", "scopes": ["openid"]}))
    assert manager.connected("cut", oauth_app()) is False
    # Microsoft answers scopes with the resource prefix.
    ms = oauth_app(("Mail.Read", "offline_access"))
    secrets.set("connector.ms.oauth", json.dumps({"refresh_token": "R", "scopes": ["https://graph.microsoft.com/Mail.Read"]}))
    assert manager.connected("ms", ms) is True


def test_expired_refresh_token_explains_the_seven_day_limit():
    http = FakeHttp()
    http.add("POST", "https://auth.example/token", lambda p, b, f: {"error": "invalid_grant"}, status=400)
    secrets = MemorySecrets(**{"connector.gmail.oauth": json.dumps({"refresh_token": "R", "expires_at": 0})})
    manager = OAuthManager(http, secrets, 8765)
    with pytest.raises(ConnectorError, match="7일 뒤 만료"):
        manager.access_token("gmail", oauth_app())


def logged_in_secrets() -> MemorySecrets:
    token = {"refresh_token": "R", "access_token": "TOKEN-SECRET", "expires_at": 4e9,
             "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}
    return MemorySecrets(**{"connector.gmail.oauth": json.dumps(token)})


def manager_with(http: FakeHttp, settings: AppSettings) -> ConnectorManager:
    return ConnectorManager(lambda: settings, http, logged_in_secrets(), today=lambda: TODAY)


def test_preview_reads_headers_only_and_shows_the_account(tmp_path, caplog):
    http = FakeHttp()
    gmail_labels(http, "회의록")
    http.add("GET", f"{API}/profile", lambda p, b, f: {"emailAddress": "me@example.com"})
    http.add("GET", f"{API}/messages", lambda p, b, f: {"messages": [{"id": "a"}, {"id": "b"}]})
    http.add("GET", f"{API}/messages/", lambda p, b, f: {"internalDate": STAMP, "payload": {"headers": [
        {"name": "Subject", "value": "주간 회의"}, {"name": "From", "value": "lead@team.kr"}]}})
    settings = AppSettings(connectors={"gmail": ConnectorSettings(enabled=True, options={"client_id": "cid", "label": "회의록"})})
    manager = manager_with(http, settings)
    with caplog.at_level(logging.DEBUG):
        result = manager.preview("gmail")
    assert [r["subject"] for r in result["rows"]] == ["주간 회의", "주간 회의"]
    assert result["summary"] == "조건에 맞는 메일 2건 (라벨: 회의록, 최근 14일)"
    assert result["account"] == "me@example.com" and result["label_names"] == ["회의록"]
    reads = [c["params"].get("format") for c in http.calls if "/messages/" in c["url"]]
    assert reads == ["metadata", "metadata"]
    row = next(r for r in manager.overview({}) if r["id"] == "gmail")
    assert row["account"] == "me@example.com" and row["connected"] is True
    # The address, subjects and the token stay out of logs and the settings file.
    text = caplog.text + settings.model_dump_json()
    for secret in ("me@example.com", "주간 회의", "lead@team.kr", "TOKEN-SECRET"):
        assert secret not in text


def test_imap_and_outlook_preview_without_bodies(monkeypatch):
    FakeImap.log = []
    monkeypatch.setattr(imap_module, "IMAP_FACTORY", FakeImap)
    imap = ImapMail({"host": "h", "username": "me", "folder": "회의록"}, context(FakeHttp(), **{"connector.imap_mail.password": "pw"}))
    result = imap.preview(TODAY)
    assert len(result["rows"]) == 2 and result["stats"]["matched"] == 2
    assert all(entry[2] == "(BODY.PEEK[HEADER])" for entry in FakeImap.log if entry[0] == "fetch")
    fetched = imap.fetch(TODAY)
    assert fetched.stats["read"] == 2 and fetched.summary == "2건 찾음, 2건 읽음"

    http = FakeHttp()
    http.add("GET", "https://graph.microsoft.com/v1.0/me/mailFolders", lambda p, b, f: {"value": [{"id": "F1", "displayName": "회의록"}]})
    http.add("GET", "https://graph.microsoft.com/v1.0/me/mailFolders/F1/messages", lambda p, b, f: {"value": [
        {"id": "x", "subject": "공지", "receivedDateTime": "2026-10-21T01:00:00Z", "from": {"emailAddress": {"address": "a@b.c"}}}]})
    preview = OutlookMail({"client_id": "cid", "folder": "회의록"}, context(http)).preview(TODAY)
    select = next(c for c in http.calls if c["url"].endswith("/messages"))["params"]["$select"]
    assert "body" not in select and preview["rows"][0]["subject"] == "공지"


def test_diag_gmail_runs_read_only_against_a_fake_server(capsys):
    path = Path(__file__).resolve().parents[1] / "tools" / "diag_gmail.py"
    spec = importlib.util.spec_from_file_location("diag_gmail", path)
    diag = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diag)
    http = FakeHttp()
    gmail_labels(http, "회의록")
    http.add("GET", f"{API}/profile", lambda p, b, f: {"emailAddress": "me@example.com", "messagesTotal": 12})
    http.add("GET", f"{API}/messages", lambda p, b, f: {"messages": [{"id": "a"}]})
    http.add("GET", f"{API}/messages/", lambda p, b, f: {"internalDate": STAMP, "payload": {"headers": [{"name": "Subject", "value": "포럼 안내"}]}})
    settings = AppSettings(connectors={"gmail": ConnectorSettings(enabled=True, options={"client_id": "cid", "label": "회의록"})})
    assert diag.run(manager_with(http, settings), "포럼") == 0
    out = capsys.readouterr().out
    assert "connected account: me@example.com" in out and "label exact match: yes" in out
    assert "TOKEN-SECRET" not in out
    assert all(c["method"] == "GET" for c in http.calls)


def test_preview_endpoint_needs_the_token_stores_nothing_and_calls_no_model(tmp_path):
    import desktop_helpers as dh
    from fastapi.testclient import TestClient
    from test_desktop_features import make_app

    from decision_agent.desktop.webapp import create_web_app

    http = FakeHttp()
    gmail_labels(http, "회의록")
    http.add("GET", f"{API}/profile", lambda p, b, f: {"emailAddress": "me@example.com"})
    http.add("GET", f"{API}/messages", lambda p, b, f: {"messages": [{"id": "a"}]})
    http.add("GET", f"{API}/messages/", lambda p, b, f: {"internalDate": STAMP, "payload": {"headers": [{"name": "Subject", "value": "주간 회의"}]}})
    root = dh.make_watch_dir(tmp_path)
    controller = make_app(root, http=http, secrets=logged_in_secrets())
    controller.start()
    try:
        assert controller.wait_idle(60)
        controller.save_connector_config("gmail", False, {"client_id": "cid", "label": "회의록"})
        runtime = next(iter(controller.runtimes.values()))
        files_before = [r["path"] for r in runtime.db.list_files()]
        calls_before = runtime.engine.counter.calls
        client = TestClient(create_web_app(controller, "t" * 32, 8765), base_url="http://127.0.0.1:8765")
        assert client.post("/api/connectors/gmail/preview").status_code == 403
        assert client.post("/api/connectors/nope/preview", headers={"X-DA-Token": "t" * 32}).status_code == 400
        body = client.post("/api/connectors/gmail/preview", headers={"X-DA-Token": "t" * 32}).json()
        assert body["rows"][0]["subject"] == "주간 회의" and body["account"] == "me@example.com"
        assert [r["path"] for r in runtime.db.list_files()] == files_before
        assert runtime.engine.counter.calls == calls_before
        assert not any(c["params"].get("format") == "full" for c in http.calls)
    finally:
        controller.stop()


def test_settings_page_shows_preview_rows_as_text_only():
    from decision_agent.desktop.webpage import all_sources

    app_js = all_sources()["app.js"]
    for text in ("AI로 보내지 않는 미리보기입니다.", "/preview", "연결된 계정: ", "있는 라벨: "):
        assert text in app_js, text
