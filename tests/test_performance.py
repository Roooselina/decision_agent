"""Phase times of a check, the 성능 page numbers, and its two maintenance actions."""

from __future__ import annotations

import desktop_helpers as dh
from fastapi.testclient import TestClient
from test_desktop_features import make_app

from decision_agent.desktop.engine import PHASE_NAMES, slowest_phase
from decision_agent.desktop.webapp import create_web_app

TOKEN = "p" * 32
HEADERS = {"X-DA-Token": TOKEN}


def test_a_check_records_its_phases_and_the_page_shows_them(tmp_path):
    app = make_app(dh.make_watch_dir(tmp_path))
    app.start()
    try:
        assert app.wait_idle(60)
        record = next(iter(app.runtimes.values())).engine.completion()
        assert {"parse", "ingest", "extract", "retrieval", "judge", "store"} <= set(record["phase_seconds"])
        assert "cache_hits" in record and "cache_misses" in record
        http = TestClient(create_web_app(app, TOKEN, 8765), base_url="http://127.0.0.1:8765")
        assert http.get("/api/performance").status_code == 403
        data = http.get("/api/performance", headers=HEADERS).json()
        assert data["mode"] == "normal" and set(data["modes"]) == {"save", "normal", "max"}
        folder = data["folders"][0]
        assert folder["slowest"]["name"] in PHASE_NAMES.values() and folder["db_bytes"] > 0
        assert {p["name"] for p in folder["phases"]} <= set(PHASE_NAMES.values())
        assert data["limits"]["max_parallel"] == 16
    finally:
        app.stop()


def test_clean_and_reindex_keep_documents_and_stored_verdicts(tmp_path):
    root = dh.make_watch_dir(tmp_path)
    before = sorted(p.read_bytes() for p in root.rglob("*") if p.is_file())
    app = make_app(root)
    app.start()
    try:
        assert app.wait_idle(60)
        runtime = next(iter(app.runtimes.values()))
        verdicts = runtime.db.conn.execute("SELECT COUNT(*) FROM line_verdicts").fetchone()[0]
        http = TestClient(create_web_app(app, TOKEN, 8765), base_url="http://127.0.0.1:8765")
        assert http.post("/api/performance/clean", headers=HEADERS).json()["removed"] == 0
        assert runtime.db.conn.execute("SELECT COUNT(*) FROM line_verdicts").fetchone()[0] == verdicts
        files = http.post("/api/performance/reindex", headers=HEADERS).json()["files"]
        assert files > 0 and app.wait_idle(60)
        assert runtime.db.conn.execute("SELECT COUNT(*) FROM files WHERE status='ok'").fetchone()[0] == files
        assert runtime.db.conn.execute("SELECT COUNT(*) FROM line_verdicts").fetchone()[0] == verdicts
    finally:
        app.stop()
    assert sorted(p.read_bytes() for p in root.rglob("*") if p.is_file()) == before


def test_slowest_phase_names_the_largest():
    assert slowest_phase({"parse": 0.2, "judge": 3.1, "store": 0.1}) == ("judge", 3.1)
    assert slowest_phase({}) is None
