from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import desktop_helpers as dh
import pytest
from fastapi.testclient import TestClient

from decision_agent.desktop import cli, folders, uistate, webpage
from decision_agent.desktop import secrets as secret_store
from decision_agent.desktop.app import DecisionAgentApp
from decision_agent.desktop.autostart import Autostart
from decision_agent.desktop.paths import app_home, state_db_path
from decision_agent.desktop.policy import FolderPolicy
from decision_agent.desktop.providers import build_provider
from decision_agent.desktop.settings import (
    AppSettings,
    FolderContained,
    FolderNeedsMerge,
    FolderRejected,
    add_folder,
    remove_folder,
    set_excluded_subfolders,
)
from decision_agent.desktop.shell import (
    MUTEX_NAME,
    QUICK_SIZE,
    DesktopShell,
    DropBox,
    SingleInstance,
    choose_ui_mode,
    fit_to_screen,
    webview2_version,
)
from decision_agent.desktop.state import StateDB
from decision_agent.desktop.webapp import create_web_app

TOKEN = "t" * 32
PORT = 8765
HEADERS = {"X-DA-Token": TOKEN}
STATIC = Path(webpage.STATIC_DIR)


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def snapshot(folder: Path) -> dict[str, str]:
    """Hash of every file and the list of directories, to prove a folder was not changed."""
    state = {}
    for path in sorted(folder.rglob("*")):
        key = path.relative_to(folder).as_posix()
        state[key] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
    return state


def make_project(tmp_path: Path, name: str = "watch") -> Path:
    root = dh.make_watch_dir(tmp_path, name)
    (root / "archive" / "old").mkdir(parents=True)
    (root / "archive" / "old" / "minutes.md").write_text("# 옛 회의\n- 결정: 보관\n", encoding="utf-8")
    (root / "archive" / "legacy.hwp").write_bytes(b"\x00binary")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "readme.md").write_text("x", encoding="utf-8")
    return root


@pytest.fixture
def keys(monkeypatch):
    store = {}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(secret_store, "get_api_key", lambda: store.get("k"))
    monkeypatch.setattr(secret_store, "set_api_key", lambda v: store.__setitem__("k", v))
    monkeypatch.setattr(secret_store, "delete_api_key", lambda: store.pop("k", None))
    return store


@pytest.fixture
def web(tmp_path, keys):
    root = make_project(tmp_path)
    secrets = MemorySecrets()
    controller = DecisionAgentApp(
        dh.make_settings(root),
        bundle_factory=lambda s: build_provider(s, key_getter=lambda: keys.get("k")),
        db_factory=lambda r: StateDB(":memory:"),
        start_watchers=False,
        secret_store=secrets,
    )
    opened = []
    urls = []
    controller.path_opener = opened.append
    controller.start()
    assert controller.wait_idle(60)
    client = TestClient(
        create_web_app(controller, TOKEN, PORT, url_opener=lambda u: urls.append(u) or True),
        base_url=f"http://127.0.0.1:{PORT}",
    )
    yield client, controller, root, {"opened": opened, "urls": urls, "secrets": secrets}
    controller.stop()


# settings and policy


def test_excluded_subfolders_must_stay_inside_the_root():
    for bad in ("../outside", "a/../../b", "C:/Windows", "/", ""):
        with pytest.raises(ValueError):
            AppSettings(excluded_subfolders={"/x": [bad]})
    ok = AppSettings(excluded_subfolders={"/x": ["archive\\old", "archive/old", "./docs/"]})
    assert ok.excluded_subfolders == {"/x": ["archive/old", "docs"]}


def test_profile_fields_are_validated():
    assert AppSettings(profile_color="#ABCDEF").profile_color == "#abcdef"
    assert AppSettings(display_name="  김   하늘 ").display_name == "김 하늘"
    for bad in ({"profile_color": "red"}, {"theme": "neon"}, {"language": "en"}, {"display_name": "x" * 41}):
        with pytest.raises(ValueError):
            AppSettings(**bad)


def test_policy_skips_excluded_subfolders_case_insensitively(tmp_path):
    root = make_project(tmp_path)
    policy = FolderPolicy(root, ["node_modules"], 25, ["Archive/OLD"])
    seen = {p.relative_to(root).as_posix() for p in policy.iter_files()}
    assert "archive/old/minutes.md" not in seen
    assert "archive/legacy.hwp" in seen and "requirements.md" in seen
    assert not any(s.startswith("node_modules") for s in seen)
    assert policy.check_file(root / "archive" / "old" / "minutes.md").status == "skip"
    assert policy.check_file(root / "requirements.md").status == "ok"


def test_policy_for_root_reads_the_settings(tmp_path):
    root = make_project(tmp_path)
    settings = AppSettings(allowed_folders=[str(root)], excluded_subfolders={str(root): ["archive"]})
    policy = FolderPolicy.for_root(root, settings)
    assert policy.check_file(root / "archive" / "old" / "minutes.md").status == "skip"


def test_engine_does_not_read_excluded_subfolders(tmp_path):
    root = make_project(tmp_path)
    engine, db, _ = dh.make_engine(root, excluded_subfolders={str(root): ["archive"]})
    engine.process()
    paths = {r["path"] for r in db.list_files()}
    assert "requirements.md" in paths
    assert not any(p.startswith("archive/") for p in paths)


def test_add_folder_refuses_contained_and_offers_merge(tmp_path):
    root = make_project(tmp_path, "parent/child")
    parent = root.parent
    settings = AppSettings(excluded_subfolders={})
    add_folder(settings, root)
    set_excluded_subfolders(settings, root, ["archive"])
    with pytest.raises(FolderContained) as contained:
        add_folder(settings, root / "meetings")
    assert "이미 포함되어 있습니다" in str(contained.value)
    with pytest.raises(FolderNeedsMerge) as merge:
        add_folder(settings, parent)
    assert merge.value.children == [root.resolve()]
    assert isinstance(merge.value, FolderRejected)
    merged = add_folder(settings, parent, merge=True)
    assert settings.allowed_folders == [str(merged)]
    assert settings.excluded_for(merged) == ["child/archive"]
    assert add_folder(settings, parent) == merged  # adding again is a no-op


def test_set_excluded_subfolders_cleans_and_requires_a_registered_root(tmp_path):
    root = make_project(tmp_path)
    settings = AppSettings(allowed_folders=[str(root)])
    assert set_excluded_subfolders(settings, root, ["archive/old", "archive", "archive"]) == ["archive"]
    with pytest.raises(FolderRejected):
        set_excluded_subfolders(settings, tmp_path, ["x"])
    with pytest.raises(FolderRejected):
        set_excluded_subfolders(settings, root, ["../escape"])
    assert remove_folder(settings, root) and settings.excluded_subfolders == {}


# folder preview and tree


def test_preview_counts_files_without_touching_the_folder(tmp_path):
    root = make_project(tmp_path)
    before = snapshot(root)
    info = folders.preview_folder(str(root), AppSettings())
    assert snapshot(root) == before
    assert info["ok"] and info["readable"] >= 6
    assert info["formats"]["md"] >= 3 and info["formats"]["csv"] == 2
    assert info["unsupported"] == {"hwp": 1}
    assert "node_modules" in info["excluded_dirs"]
    assert info["relation"]["kind"] == "new" and not info["many_files"]


def test_preview_warns_about_many_files_and_reports_relations(tmp_path, monkeypatch):
    root = make_project(tmp_path)
    monkeypatch.setattr(folders, "MANY_FILES", 3)
    settings = AppSettings(allowed_folders=[str(root)])
    info = folders.preview_folder(str(root / "archive"), settings)
    assert info["relation"]["kind"] == "contained"
    info = folders.preview_folder(str(root), AppSettings(allowed_folders=[str(root / "archive")]))
    assert info["relation"]["kind"] == "parent" and info["many_files"]
    assert any("많습니다" in w for w in info["warnings"])


def test_preview_explains_rejected_locations():
    info = folders.preview_folder(str(Path.home()), AppSettings())
    assert info["ok"] is False and "감시할 수 없습니다" in info["reason"]
    root = Path(Path.home().anchor)
    assert folders.preview_folder(str(root), AppSettings())["ok"] is False


def test_folder_tree_shows_counts_and_exclusions(tmp_path):
    root = make_project(tmp_path)
    settings = AppSettings(allowed_folders=[str(root)], excluded_subfolders={str(root): ["archive/old"]})
    data = folders.folder_tree(root, settings)
    tree = data["tree"]
    names = [c["name"] for c in tree["children"]]
    assert "archive" in names and "meetings" in names and "node_modules" not in names
    archive = next(c for c in tree["children"] if c["name"] == "archive")
    old = archive["children"][0]
    assert old["excluded"] and old["readable"] == 1
    assert archive["unsupported"] == 1 and archive["total_readable"] == 1
    assert data["excluded"] == ["archive/old"]


# web API


def test_folder_cards_and_open_in_explorer(web):
    client, _, root, extra = web
    cards = client.get("/api/folders", headers=HEADERS).json()
    assert cards[0]["root"] == str(root.resolve()) and cards[0]["watch"] == "watching"
    assert cards[0]["files_ok"] >= 6 and cards[0]["last_checked"]
    assert client.post("/api/folders/open", headers=HEADERS, json={"target": str(root)}).status_code == 200
    assert client.post("/api/folders/open", headers=HEADERS, json={"target": "data"}).status_code == 200
    outside = client.post("/api/folders/open", headers=HEADERS, json={"target": str(root.parent)})
    assert outside.status_code == 400
    assert extra["opened"] == [Path(cards[0]["root"]), app_home()]


def test_folder_pick_preview_and_merge_flow(web, tmp_path):
    client, controller, root, _ = web
    assert client.post("/api/folders/pick", headers=HEADERS).status_code == 400
    controller.folder_picker = lambda: str(tmp_path)
    assert client.post("/api/folders/pick", headers=HEADERS).json() == {"path": str(tmp_path)}
    preview = client.post("/api/folders/preview", headers=HEADERS, json={"path": str(tmp_path)}).json()
    assert preview["relation"]["kind"] == "parent"
    refused = client.post("/api/folders/add", headers=HEADERS, json={"path": str(tmp_path)})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "merge"
    nested = client.post("/api/folders/add", headers=HEADERS, json={"path": str(root / "archive")})
    assert nested.status_code == 400 and "이미 포함되어 있습니다" in nested.json()["detail"]
    merged = client.post("/api/folders/add", headers=HEADERS, json={"path": str(tmp_path), "merge": True})
    assert merged.status_code == 200 and controller.wait_idle(60)
    assert controller.settings.allowed_folders == [str(tmp_path.resolve())]


def test_excluding_a_subfolder_removes_its_files_from_the_analysis(web):
    client, controller, root, _ = web
    tree = client.get("/api/folders/tree", headers=HEADERS, params={"root": str(root)}).json()
    assert any(c["name"] == "archive" for c in tree["tree"]["children"])
    files = {f["path"] for f in client.get("/api/files", headers=HEADERS).json()}
    assert "archive/old/minutes.md" in files
    saved = client.post("/api/folders/excluded", headers=HEADERS, json={"root": str(root), "excluded": ["archive"]})
    assert saved.json() == {"ok": True, "excluded": ["archive"]} and controller.wait_idle(60)
    files = {f["path"] for f in client.get("/api/files", headers=HEADERS).json()}
    assert not any(p.startswith("archive/") for p in files)
    bad = client.post("/api/folders/excluded", headers=HEADERS, json={"root": str(root), "excluded": ["../x"]})
    assert bad.status_code == 400
    assert client.post("/api/settings", headers=HEADERS, json={"excluded_subfolders": {}}).status_code == 400
    assert client.get("/api/folders/tree", headers=HEADERS, params={"root": str(root.parent)}).status_code == 400


def test_removing_a_folder_deletes_only_app_data(tmp_path, keys):
    root = make_project(tmp_path)
    before = snapshot(root)
    controller = DecisionAgentApp(dh.make_settings(root), start_watchers=False, secret_store=MemorySecrets(),
                                  bundle_factory=lambda s: build_provider(s, key_getter=lambda: None))
    controller.start()
    assert controller.wait_idle(60)
    db_file = state_db_path(root)
    assert db_file.exists()
    assert controller.remove_folder(str(root))
    controller.stop()
    assert not db_file.exists()
    assert snapshot(root) == before


def test_dropped_folder_is_handed_over_once(web, tmp_path):
    client, controller, _, _ = web
    assert client.get("/api/folders/dropped", headers=HEADERS).json() == {"path": None}
    controller.drops.put(str(tmp_path))
    assert client.get("/api/folders/dropped", headers=HEADERS).json() == {"path": str(tmp_path)}
    assert client.get("/api/folders/dropped", headers=HEADERS).json() == {"path": None}
    clock = [0.0]
    box = DropBox(ttl=5, clock=lambda: clock[0])
    box.put("x")
    clock[0] = 10
    assert box.take() is None


def test_status_tells_whether_documents_leave_the_computer(web):
    client, _, _, _ = web
    status = client.get("/api/status", headers=HEADERS).json()
    assert status["provider"]["sends_externally"] is False and status["provider"]["local_only"] is True
    assert status["overall"]["state"] == "demo" and "실제 검사가 아닙니다" in status["overall"]["label"]
    assert status["registered_folders"] == 1 and status["onboarding_needed"] is False


def test_onboarding_is_needed_only_on_first_run(tmp_path, keys):
    controller = DecisionAgentApp(AppSettings(provider="mock"), start_watchers=False, secret_store=MemorySecrets())
    assert controller.status()["onboarding_needed"] is True
    controller.settings = AppSettings(provider="mock", onboarding_done=True)
    assert controller.status()["onboarding_needed"] is False


def test_profile_settings_are_editable_and_validated(web):
    client, controller, _, _ = web
    ok = client.post("/api/settings", headers=HEADERS, json={"display_name": "김하늘", "profile_color": "#0891B2",
                                                            "theme": "dark", "onboarding_done": True})
    assert ok.status_code == 200 and controller.settings.profile_color == "#0891b2"
    assert client.post("/api/settings", headers=HEADERS, json={"profile_color": "blue"}).status_code == 400
    assert client.post("/api/settings", headers=HEADERS, json={"theme": "sepia"}).status_code == 400
    assert controller.wait_idle(60)


def test_inbox_records_the_first_completion(web):
    client, _, _, _ = web
    items = client.get("/api/notifications", headers=HEADERS).json()
    assert any("데모 판정기" in i["text"] for i in items)


def test_diagnostics_contain_no_secret_values(web, keys):
    client, _, _, extra = web
    keys["k"] = "sk-secret-value-123456"
    extra["secrets"].set("connector.slack.bot_token", "xoxb-secret-token")
    text = client.get("/api/diagnostics", headers=HEADERS).json()["text"]
    assert "API 키: 저장됨" in text
    assert "sk-secret" not in text and "xoxb" not in text


def test_purge_needs_the_exact_phrase_and_keeps_watched_files(web, keys):
    client, controller, root, extra = web
    keys["k"] = "sk-secret-value-123456"
    extra["secrets"].set("connector.slack.bot_token", "xoxb-secret-token")
    extra["secrets"].set("connector.gmail.oauth", "{}")
    (app_home() / "digests").mkdir(exist_ok=True)
    (app_home() / "digests" / "d.md").write_text("x", encoding="utf-8")
    before = snapshot(root)
    assert client.post("/api/data/purge", headers=HEADERS, json={"confirm": "삭제"}).status_code == 400
    assert keys.get("k")
    done = client.post("/api/data/purge", headers=HEADERS, json={"confirm": "모두 삭제"})
    assert done.status_code == 200 and controller.wait_idle(60)
    assert "k" not in keys and extra["secrets"].values == {}
    assert controller.settings.allowed_folders == [] and not (app_home() / "digests").exists()
    assert client.get("/api/status", headers=HEADERS).json()["onboarding_needed"] is True
    assert snapshot(root) == before


def test_autostart_endpoints_use_the_current_user_key(web):
    client, controller, _, _ = web
    assert client.get("/api/autostart", headers=HEADERS).json() == {"supported": False, "enabled": False}
    assert client.post("/api/autostart", headers=HEADERS, json={"enabled": True}).status_code == 400

    class Reg:
        HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 3, 4

        def __init__(self):
            self.values = {}

        def OpenKey(self, *args):
            reg = self

            class Key:
                def __enter__(self):
                    return reg

                def __exit__(self, *exc):
                    return False
            return Key()

        def QueryValueEx(self, key, name):
            if name not in self.values:
                raise OSError
            return self.values[name], 1

        def SetValueEx(self, key, name, _, kind, value):
            self.values[name] = value

        def DeleteValue(self, key, name):
            self.values.pop(name)

    controller.autostart = Autostart(registry=Reg(), command='"app.exe" run --background')
    assert client.post("/api/autostart", headers=HEADERS, json={"enabled": True}).json()["enabled"] is True
    assert client.post("/api/autostart", headers=HEADERS, json={"enabled": False}).json()["enabled"] is False


def test_activate_passes_only_known_pages(web):
    client, controller, _, _ = web
    assert client.post("/api/app/activate", headers=HEADERS, json={}).json() == {"ok": False}
    pages = []
    controller.activate_hook = pages.append
    client.post("/api/app/activate", headers=HEADERS, json={"page": "#/folders"})
    client.post("/api/app/activate", headers=HEADERS, json={"page": "/quick"})
    client.post("/api/app/activate", headers=HEADERS, json={"page": "javascript:alert(1)"})
    assert pages == ["#/folders", "/quick", ""]
    assert client.post("/api/app/activate", json={}).status_code == 403


def test_oauth_login_opens_in_the_system_browser(web):
    client, _, _, extra = web
    client.post("/api/connectors/config", headers=HEADERS,
                json={"id": "gmail", "enabled": False, "options": {"client_id": "cid", "label": "회의록"}})
    result = client.post("/api/connectors/oauth/start", headers=HEADERS, json={"id": "gmail", "open_browser": True}).json()
    assert result["opened"] is True and extra["urls"] == [result["url"]]
    plain = client.post("/api/connectors/oauth/start", headers=HEADERS, json={"id": "gmail"}).json()
    assert plain["opened"] is False and len(extra["urls"]) == 1


# static files and page safety


def test_static_files_are_served_from_an_allow_list(web):
    client, _, _, _ = web
    js = client.get("/static/app.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
    assert client.get("/static/icon.svg").headers["content-type"].startswith("image/svg+xml")
    for name in ("webapp.py", "..%2Fwebapp.py", "index.html", "app.ico", "missing.js"):
        assert client.get("/static/" + name).status_code == 404, name


def test_pages_load_scripts_with_the_nonce_only(web):
    client, _, _, _ = web
    client.get(f"/?t={TOKEN}")
    page = client.get("/")
    csp = page.headers["content-security-policy"]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    scripts = re.findall(r"<script([^>]*)>", page.text)
    assert scripts and all(f'nonce="{nonce}"' in s for s in scripts)
    assert "script-src 'nonce-" in csp and "'unsafe-eval'" not in csp
    assert "https://" not in page.text and "http://" not in page.text


def test_ui_sources_never_inject_html_or_load_remote_code():
    sources = webpage.all_sources()
    assert {"index.html", "quick.html", "app.js", "common.js", "quick.js", "app.css"} <= set(sources)
    for name, text in sources.items():
        for banned in ("innerHTML", "outerHTML", "document.write", "eval(", "insertAdjacentHTML",
                       "new Function", "cdn.", "googleapis", "unpkg"):
            assert banned not in text, (name, banned)


EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\u2B50\u2B55\u2705\u274C]")


def test_no_emoji_in_ui_sources_and_desktop_code():
    targets = list(STATIC.glob("*")) + list(Path(webpage.__file__).parent.glob("*.py"))
    for path in targets:
        if path.suffix in {".ico", ".png"}:
            continue
        assert not EMOJI.search(path.read_text(encoding="utf-8")), path.name


def test_window_state_is_clamped_and_saved(tmp_path):
    target = tmp_path / "ui.json"
    target.write_text(json.dumps({"width": 300, "height": 99999, "x": 5, "y": "bad", "close_hint_shown": True}), encoding="utf-8")
    state = uistate.load_ui_state(target)
    assert state["width"] == uistate.MIN_WIDTH and state["height"] == 6000
    assert state["x"] == 5 and state["y"] is None and state["close_hint_shown"] is True
    state["width"] = 1400
    uistate.save_ui_state(state, target)
    assert uistate.load_ui_state(target)["width"] == 1400
    target.write_text("not json", encoding="utf-8")
    assert uistate.load_ui_state(target)["width"] == uistate.DEFAULTS["width"]


# native window shell


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        return [h(*args) for h in self.handlers]


class FakeWindow:
    def __init__(self, title, url, **kwargs):
        self.title, self.url, self.kwargs = title, url, kwargs
        self.events = type("Events", (), {})()
        for name in ("closing", "resized", "moved", "maximized", "restored", "loaded"):
            setattr(self.events, name, FakeEvent())
        self.calls = []
        self.on_top = kwargs.get("on_top", False)

    def hide(self):
        self.calls.append("hide")

    def show(self):
        self.calls.append("show")

    def restore(self):
        self.calls.append("restore")

    def destroy(self):
        self.calls.append("destroy")

    def load_url(self, url):
        self.calls.append(("load", url))

    def evaluate_js(self, code):
        self.calls.append(("js", code))

    def create_file_dialog(self, kind):
        self.calls.append(("dialog", kind))
        return ("D:\\team\\project",)


class FakeWebview:
    FOLDER_DIALOG = 20

    def __init__(self):
        self.windows = []
        self.started = None

    def create_window(self, title, url, **kwargs):
        window = FakeWindow(title, url, **kwargs)
        self.windows.append(window)
        return window

    def start(self, *args, **kwargs):
        self.started = kwargs


def make_shell(tmp_path, **kwargs):
    hints = []
    shell = DesktopShell(FakeWebview(), "http://127.0.0.1:1/?t=x", "http://127.0.0.1:1/?t=x&next=/quick",
                         storage_path=tmp_path / "wv", on_first_hide=lambda: hints.append(1),
                         state_path=tmp_path / "ui.json", **kwargs)
    return shell, hints


def test_closing_the_window_hides_it_and_hints_once(tmp_path):
    shell, hints = make_shell(tmp_path)
    window = shell.create_main()
    assert window.kwargs["min_size"] == (1100, 720) and window.kwargs["hidden"] is False
    assert window.events.closing.fire() == [False]
    assert window.events.closing.fire() == [False]
    assert window.calls.count("hide") == 2 and hints == [1]
    assert json.loads((tmp_path / "ui.json").read_text(encoding="utf-8"))["close_hint_shown"] is True
    shell.quit()
    assert window.events.closing.fire() == [True] and "destroy" in window.calls


def test_window_geometry_is_remembered(tmp_path):
    shell, _ = make_shell(tmp_path)
    window = shell.create_main()
    window.events.resized.fire(1500, 900)
    window.events.moved.fire(40, 60)
    shell.quit()
    again, _ = make_shell(tmp_path)
    reopened = again.create_main()
    assert (reopened.kwargs["width"], reopened.kwargs["height"]) == (1500, 900)
    assert (reopened.kwargs["x"], reopened.kwargs["y"]) == (40, 60)


def test_quick_window_is_small_fixed_and_on_top(tmp_path):
    shell, _ = make_shell(tmp_path)
    shell.create_main()
    shell.show_quick()
    quick = shell.webview.windows[-1]
    assert (quick.kwargs["width"], quick.kwargs["height"]) == QUICK_SIZE == (480, 640)
    assert quick.kwargs["resizable"] is False and quick.kwargs["on_top"] is True
    assert quick.events.closing.fire() == [False] and "hide" in quick.calls
    shell.show_quick()
    assert len(shell.webview.windows) == 2 and "show" in quick.calls


def test_shell_background_start_and_folder_dialog(tmp_path):
    shell, _ = make_shell(tmp_path, background=True)
    window = shell.create_main()
    assert window.kwargs["hidden"] is True
    assert shell.pick_folder() == "D:\\team\\project"
    shell.show_main("#/folders")
    assert ("js", "window.location.hash = '#/folders'") in window.calls and "show" in window.calls
    shell.run()
    assert shell.webview.started["storage_path"] == str(tmp_path / "wv")


def test_dropped_folder_path_is_forwarded(tmp_path):
    dropped = []
    shell, _ = make_shell(tmp_path, on_drop=dropped.append)
    shell._dropped({"dataTransfer": {"files": [{"name": "p", "pywebviewFullPath": "D:\\team\\p"}]}})
    shell._dropped({"dataTransfer": {"files": [{"name": "nopath"}]}})
    assert dropped == ["D:\\team\\p"]


def test_ui_mode_falls_back_to_the_browser():
    def broken():
        raise ImportError("no webview")

    assert choose_ui_mode("win32", broken)[0] == "browser"
    mode, reason = choose_ui_mode("win32", lambda: object(), lambda: None)
    assert mode == "browser" and "WebView2" in reason
    assert choose_ui_mode("win32", lambda: object(), lambda: "120.0.1")[0] == "window"
    assert choose_ui_mode("linux", lambda: object(), lambda: None)[0] == "window"


def test_webview2_version_reads_the_edgeupdate_keys():
    class Reg:
        HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER, KEY_READ = 1, 2, 3

        def __init__(self, found):
            self.found = found

        def OpenKey(self, hive, path, *_):
            if (hive, "WOW6432Node" in path) != self.found:
                raise OSError
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def QueryValueEx(self, key, name):
            return "121.0.2277.83", 1

    assert webview2_version(Reg((1, True))) == "121.0.2277.83"
    assert webview2_version(Reg((9, False))) is None


def test_single_instance_detects_a_running_copy():
    class Kernel:
        def __init__(self, error):
            self.error = error
            self.names = []

        def CreateMutexW(self, attrs, owner, name):
            self.names.append(name)
            return 42

        def GetLastError(self):
            return self.error

    first = Kernel(0)
    assert SingleInstance(platform="win32", kernel32=first).acquire() is True
    assert first.names == [MUTEX_NAME]
    assert SingleInstance(platform="win32", kernel32=Kernel(183)).acquire() is False
    assert SingleInstance(platform="linux").acquire() is True


# command line


def test_scan_report_is_written_outside_the_folder(tmp_path, capsys):
    root = dh.make_watch_dir(tmp_path)
    before = snapshot(root)
    report = tmp_path / "reports" / "scan.json"
    assert cli.main(["scan", "--folder", str(root), "--report", str(report)]) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["inconsistent"] >= 3 and data["provider"].startswith("mock")
    with pytest.raises(SystemExit):
        cli.main(["scan", "--folder", str(root), "--report", str(root / "scan.json")])
    assert snapshot(root) == before


def test_reset_data_requires_yes_and_clears_secrets(tmp_path, monkeypatch, capsys):
    deleted = []
    monkeypatch.setattr(secret_store, "delete_api_key", lambda: deleted.append("api"))
    monkeypatch.setattr(secret_store.KeyringSecrets, "delete", lambda self, name: deleted.append(name))
    monkeypatch.setattr(Autostart, "disable", lambda self: deleted.append("autostart"))
    root = dh.make_watch_dir(tmp_path)
    before = snapshot(root)
    cli.main(["folders", "add", str(root)])
    assert cli.main(["reset-data"]) == 1 and (app_home() / "settings.json").exists()
    assert cli.main(["reset-data", "--yes"]) == 0
    assert "api" in deleted and "autostart" in deleted and "connector.slack.bot_token" in deleted
    assert not (app_home() / "settings.json").exists()
    assert snapshot(root) == before


def test_serve_and_run_accept_a_port():
    parser = cli.build_parser()
    args = parser.parse_args(["serve", "--port", "9123"])
    assert args.port == 9123 and args.no_tray and args.no_window
    assert parser.parse_args(["run", "--no-window"]).no_window is True


def test_web_server_starts_without_console_streams(monkeypatch, tmp_path, keys):
    """The windowed exe has no stdout or stderr; the server must still start."""
    import socket
    import urllib.request

    from decision_agent.desktop.webapp import WebServer

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    controller = DecisionAgentApp(AppSettings(provider="mock"), start_watchers=False, secret_store=MemorySecrets())
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    server = WebServer(create_web_app(controller, TOKEN, port), port)
    server.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=5) as response:
            assert json.loads(response.read())["app"] == "decision-agent"
    finally:
        server.stop()


def test_window_fits_small_screens():
    state = {"width": 1280, "height": 820, "x": None, "y": None}
    width, height, x, y = fit_to_screen(state, (1440, 900))
    assert height <= 820 and y + height <= 900 - 40 and x + width <= 1440
    assert fit_to_screen({"width": 1200, "height": 760, "x": 3000, "y": 10}, (1920, 1080))[2] == 360
    assert fit_to_screen(state, None) == (1280, 820, None, None)
