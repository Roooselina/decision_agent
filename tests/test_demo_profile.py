"""Demo profile: apply the preset, keep the first backup, restore the previous values exactly."""

import json

from decision_agent.desktop import demo_profile
from decision_agent.desktop.about import about
from decision_agent.desktop.settings import AppSettings, save_settings


def test_on_off_restores_previous_values(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_AGENT_HOME", str(tmp_path))
    target = tmp_path / "settings.json"
    save_settings(AppSettings(debounce_seconds=30.0, mail_poll_seconds=60, confirm_over_calls=400), target)
    before = json.loads(target.read_text(encoding="utf-8"))

    on = demo_profile.turn_on(target)
    assert on["on"] is True
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["debounce_seconds"] == 3.0
    assert data["mail_poll_seconds"] == 15
    assert data["confirm_over_calls"] == 100
    assert about()["demo_profile"]["on"] is True

    # A second "on" must not overwrite the backup with demo values.
    demo_profile.turn_on(target)
    off = demo_profile.turn_off(target)
    assert off["on"] is False
    assert json.loads(target.read_text(encoding="utf-8")) == before
    assert not demo_profile.backup_path().exists()


def test_preset_file_is_editable(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_AGENT_HOME", str(tmp_path))
    target = tmp_path / "settings.json"
    save_settings(AppSettings(), target)
    demo_profile.preset_path().write_text(
        json.dumps({"values": {"debounce_seconds": 5.0}}), encoding="utf-8"
    )
    demo_profile.turn_on(target)
    assert json.loads(target.read_text(encoding="utf-8"))["debounce_seconds"] == 5.0
    assert demo_profile.status()["values"] == {"debounce_seconds": 5.0}
    demo_profile.turn_off(target)
    assert json.loads(target.read_text(encoding="utf-8"))["debounce_seconds"] == AppSettings().debounce_seconds
