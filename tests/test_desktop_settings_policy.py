from __future__ import annotations

import os
import sys

import pytest

from decision_agent.desktop import secrets as secret_store
from decision_agent.desktop.policy import FolderPolicy
from decision_agent.desktop.settings import (
    AppSettings,
    FolderRejected,
    add_folder,
    load_settings,
    remove_folder,
    save_settings,
    validate_folder,
)


def test_validate_folder_rejects_unsafe_locations(tmp_path):
    with pytest.raises(FolderRejected):
        validate_folder(tmp_path / "missing")
    with pytest.raises(FolderRejected):
        validate_folder("/")
    with pytest.raises(FolderRejected):
        validate_folder(os.path.expanduser("~"))
    with pytest.raises(FolderRejected):
        validate_folder(os.environ["DECISION_AGENT_HOME"])
    (tmp_path / "ok").mkdir()
    assert validate_folder(tmp_path / "ok") == (tmp_path / "ok").resolve()


def test_add_folder_rejects_nested_and_parent(tmp_path):
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    settings = AppSettings()
    add_folder(settings, parent)
    with pytest.raises(FolderRejected, match="포함"):
        add_folder(settings, child)
    other = AppSettings()
    add_folder(other, child)
    with pytest.raises(FolderRejected, match="상위"):
        add_folder(other, parent)
    assert add_folder(settings, parent) == parent.resolve()
    assert len(settings.allowed_folders) == 1
    assert remove_folder(settings, parent) is True
    assert remove_folder(settings, parent) is False


def test_settings_roundtrip_and_broken_file(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings(allowed_folders=["x"], debounce_seconds=5.0, allow_external_send=True)
    save_settings(settings, path)
    loaded = load_settings(path)
    assert loaded.debounce_seconds == 5.0 and loaded.allow_external_send is True
    assert not list(tmp_path.glob("settings-*.tmp"))
    path.write_text("{not json", encoding="utf-8")
    fresh = load_settings(path)
    assert fresh.allowed_folders == [] and fresh.allow_external_send is False
    assert (tmp_path / "settings.broken.json").exists()


def test_external_send_is_off_by_default():
    assert AppSettings().allow_external_send is False


def test_policy_exclusions_hidden_and_config(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "b.md").write_text("x", encoding="utf-8")
    (tmp_path / "~$draft.docx").write_text("x", encoding="utf-8")
    (tmp_path / "notes.md").write_text("- a b c d", encoding="utf-8")
    (tmp_path / "scope_aliases.json").write_text("{}", encoding="utf-8")
    (tmp_path / "photo.png").write_bytes(b"x")
    (tmp_path / "old.hwp").write_bytes(b"x")
    policy = FolderPolicy(tmp_path, AppSettings().exclude_patterns, 25)
    assert policy.check_file(tmp_path / "notes.md").status == "ok"
    for name in ("node_modules/a.md", ".git/b.md", "~$draft.docx", "scope_aliases.json", "photo.png"):
        assert policy.check_file(tmp_path / name).status == "skip", name
    assert policy.check_file(tmp_path / "old.hwp").status == "unsupported"
    walked = {p.relative_to(tmp_path).as_posix() for p in policy.iter_files()}
    assert "node_modules/a.md" not in walked and ".git/b.md" not in walked
    assert "notes.md" in walked


def test_policy_size_limit(tmp_path):
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * (2 * 1024 * 1024))
    assert FolderPolicy(tmp_path, [], 1).check_file(big).status == "skip"
    assert FolderPolicy(tmp_path, [], 5).check_file(big).status == "ok"


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_policy_blocks_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("- secret decision text", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.md").symlink_to(outside / "secret.md")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    policy = FolderPolicy(root, [], 25)
    assert policy.check_file(root / "link.md").status == "skip"
    assert policy.check_file(root / "linkdir" / "secret.md").status == "skip"
    assert list(policy.iter_files()) == [root / "link.md"]  # listed but never readable
    assert policy.relative_parts(root / "link.md") is None


class _FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, name, value):
        self.store[(service, name)] = value

    def get_password(self, service, name):
        return self.store.get((service, name))

    def delete_password(self, service, name):
        self.store.pop((service, name), None)


def test_api_key_storage_prefers_keyring_then_env(monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(secret_store, "_keyring", lambda: (fake, RuntimeError))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert secret_store.get_api_key() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-12345")
    assert secret_store.get_api_key() == "env-key-12345"
    secret_store.set_api_key("keyring-key-12345")
    assert secret_store.get_api_key() == "keyring-key-12345"
    secret_store.delete_api_key()
    assert secret_store.get_api_key() == "env-key-12345"


def test_settings_file_never_contains_the_api_key(tmp_path, monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(secret_store, "_keyring", lambda: (fake, RuntimeError))
    secret_store.set_api_key("super-secret-key-1")
    path = tmp_path / "s.json"
    save_settings(AppSettings(), path)
    assert "super-secret" not in path.read_text(encoding="utf-8")
