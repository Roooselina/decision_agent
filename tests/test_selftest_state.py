"""selftest, the read-only state dump, and the 정보 card data."""

from __future__ import annotations

import hashlib
import json
import socket
import sqlite3
from pathlib import Path

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient
from test_desktop_features import MemorySecrets

from decision_agent.connectors.base import MailMessage
from decision_agent.desktop import about as about_module
from decision_agent.desktop import cli
from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.maildb import MailDB
from decision_agent.desktop.paths import mail_db_path, settings_path, state_db_path
from decision_agent.desktop.providers import ProviderBundle, build_provider
from decision_agent.desktop.selftest import run_selftest
from decision_agent.desktop.settings import ConnectorSettings, MailSource, save_settings
from decision_agent.desktop.state import StateDB
from decision_agent.desktop.statedump import REDACTED, dump_state, open_readonly, redact
from decision_agent.desktop.webapp import create_web_app

TOKEN = "s" * 32
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_cli(argv, capsys) -> tuple[int, dict]:
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


class Answers:
    def __init__(self, fail=False):
        self.fail = fail

    def complete(self, system, user, temperature, json_schema):
        if self.fail:
            raise RuntimeError("down")
        return "OK"


def real_bundle(fail=False):
    return lambda settings: ProviderBundle(Answers(fail), "anthropic:test", False, "", True)


# selftest


def test_selftest_on_a_fresh_home_passes_and_prints_json(capsys):
    code, report = run_cli(["selftest", "--no-model", "--port", str(free_port())], capsys)
    assert code == 0 and report["ok"] is True
    names = {c["name"]: c["status"] for c in report["checks"]}
    assert names["imports"] == "ok" and names["data_folder"] == "ok" and names["settings"] == "ok"
    assert names["databases"] == "ok" and names["port"] == "ok"
    assert names["folders"] == "skipped" and names["model"] == "skipped"
    assert report["version"] and report["data_dir"]


def test_selftest_reports_a_missing_folder_and_a_busy_port(tmp_path, capsys):
    save_settings(dh.make_settings(tmp_path / "gone"))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        code, report = run_cli(["selftest", "--no-model", "--port", str(port)], capsys)
    assert code == 1 and report["ok"] is False
    names = {c["name"]: c for c in report["checks"]}
    assert names["folders"]["status"] == "failed" and names["port"]["status"] == "failed"


def test_selftest_model_check_only_with_a_real_model(tmp_path):
    save_settings(dh.make_settings(dh.make_watch_dir(tmp_path)))
    port = free_port()
    skipped = run_selftest(port=port, bundle_factory=lambda s: build_provider(s, key_getter=lambda: None))
    assert next(c for c in skipped["checks"] if c["name"] == "model")["status"] == "skipped"
    good = run_selftest(port=port, bundle_factory=real_bundle())
    assert good["ok"] and next(c for c in good["checks"] if c["name"] == "model")["status"] == "ok"
    bad = run_selftest(port=port, bundle_factory=real_bundle(fail=True))
    assert not bad["ok"] and next(c for c in bad["checks"] if c["name"] == "model")["status"] == "failed"


def test_selftest_reports_a_broken_database(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    save_settings(dh.make_settings(root))
    state_db_path(root).write_bytes(b"not a database" * 100)
    report = run_selftest(port=free_port(), model=False)
    assert next(c for c in report["checks"] if c["name"] == "databases")["status"] == "failed"


# state dump


def files_digest(paths: list[Path]) -> dict:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}


def test_state_dump_reads_a_running_app_without_writing_or_leaking(tmp_path, capsys):
    root = dh.make_watch_dir(tmp_path)
    settings = dh.make_settings(
        root,
        connectors={"gmail": ConnectorSettings(enabled=True), "imap_mail": ConnectorSettings(enabled=True)},
        mail_sources=[MailSource(id="team", name="팀", connector="gmail", senders=["lead@team.kr"])],
    )
    save_settings(settings)
    secrets = MemorySecrets()
    secrets.set("connector.gmail.oauth", json.dumps({
        "refresh_token": "refresh-SECRET-1", "access_token": "access-SECRET-2", "expires_at": 0,
        "scopes": [READ_SCOPE, SEND_SCOPE],
    }))
    app = DecisionAgentApp(
        settings,
        bundle_factory=lambda s: build_provider(s, key_getter=lambda: None),
        db_factory=lambda r: StateDB(state_db_path(r)),
        start_watchers=False,
        secret_store=secrets,
    )
    app.start()
    try:
        assert app.wait_idle(60)
        app.maildb.upsert("team", [MailMessage("m1", "2026-10-20", "회의", "lead <lead@team.kr>", "본문")])
        files = [state_db_path(root), mail_db_path()]
        files += [Path(str(p) + "-wal") for p in files]
        before = files_digest(files)

        data = dump_state(secrets=secrets)
        assert files_digest(files) == before

        folder = data["folders"][0]
        assert folder["root"] == str(root) and folder["state"] == "demo"
        assert folder["files"].get("ok", 0) > 0
        assert folder["last_cycle"]["requests"] >= 0 and "phase_seconds" in folder["last_cycle"]
        assert data["mail"]["by_connection"] == {"gmail": 1} and data["mail"]["by_source"] == {"team": 1}
        rows = {r["id"]: r for r in data["connections"]}
        assert rows["gmail"]["connected"] and rows["gmail"]["send_granted"] is True
        assert rows["gmail"]["scopes"] == sorted([READ_SCOPE, SEND_SCOPE])
        assert rows["imap_mail"]["read_only"] is True and rows["imap_mail"]["connected"] is False
        text = json.dumps(data, ensure_ascii=False)
        assert "SECRET" not in text and "refresh_token" not in text
        assert data["settings"]["allowed_folders"] == [str(root)]

        # The CLI form works too, while the app keeps running.
        assert cli.main(["state", "--json"]) == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["folders"][0]["root"] == str(root)
        assert files_digest(files) == before
    finally:
        app.stop()


def test_state_dump_with_nothing_stored():
    data = dump_state()
    assert data["folders"] == [] and data["mail"]["total"] == 0 and data["connections"] == []


def test_readonly_connection_refuses_writes(tmp_path):
    db = StateDB(tmp_path / "x.sqlite")
    db.close()
    conn = open_readonly(tmp_path / "x.sqlite")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO meta(key, value) VALUES('a', 'b')")
    conn.close()
    assert open_readonly(tmp_path / "missing.sqlite") is None
    MailDB(tmp_path / "m.sqlite").close()
    assert open_readonly(tmp_path / "m.sqlite") is not None


def test_redact_hides_secret_looking_values():
    out = redact({"a": 1, "client_secret": "x", "nested": [{"api_key": "y", "token_url": ""}], "password": None})
    assert out == {"a": 1, "client_secret": REDACTED, "nested": [{"api_key": REDACTED, "token_url": ""}], "password": None}


# 정보


def test_about_prefers_the_install_file(tmp_path, monkeypatch):
    info_file = tmp_path / "install_info.json"
    info_file.write_text(json.dumps({"commit": "abc1234", "installed_at": "2026-10-29T10:00:00",
                                     "install_dir": "C:/x"}), encoding="utf-8")
    monkeypatch.setattr(about_module, "install_info_path", lambda: info_file)
    info = about_module.about()
    assert info["commit"] == "abc1234" and info["commit_source"] == "install"
    assert info["installed_at"] == "2026-10-29T10:00:00"
    assert Path(info["data_dir"]) == settings_path().parent
    assert info["log_path"].endswith("decision-agent.log")


def test_about_endpoint_and_diagnostics(tmp_path):
    from test_desktop_features import make_app

    app = make_app(dh.make_watch_dir(tmp_path))
    http = TestClient(create_web_app(app, TOKEN, 8765), base_url="http://127.0.0.1:8765")
    assert http.get("/api/about").status_code == 403
    data = http.get("/api/about", headers={"X-DA-Token": TOKEN}).json()
    assert {"version", "commit", "data_dir", "log_path"} <= set(data)
    assert data["version"] in app.diagnostics().splitlines()[0]
