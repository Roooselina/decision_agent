from __future__ import annotations

import json
import threading

import numpy as np
import pytest

from decision_agent.connectors.http import HttpError, HttpResponse
from decision_agent.desktop import hotkey as hotkey_module
from decision_agent.desktop.embeddings import MIN_SIMILARITY, OllamaEmbedder, make_extra_ranker
from decision_agent.desktop.hotkey import HotkeyListener, parse_hotkey
from decision_agent.desktop.providers import build_provider, check_provider, is_loopback_url
from decision_agent.desktop.settings import AppSettings
from decision_agent.desktop.state import StateDB
from decision_agent.llm.ollama_provider import (
    OllamaConnectionError,
    OllamaModelMissing,
    OllamaProvider,
)
from decision_agent.models import Artifact, ArtifactKind, DecisionScope, ScopeKind


class ScriptedHttp:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def request(self, method, url, *, headers=None, params=None, form=None, json_body=None, timeout=30.0):
        self.calls.append((method, url, json_body))
        result = self.handler(method, url, json_body)
        if isinstance(result, Exception):
            raise result
        status, body = result
        return HttpResponse(status, json.dumps(body).encode("utf-8"))


# hotkey


def test_parse_hotkey_accepts_letters_digits_and_function_keys():
    assert parse_hotkey("ctrl+alt+d") == (hotkey_module.MOD_CONTROL | hotkey_module.MOD_ALT, ord("D"))
    assert parse_hotkey("Win + Shift + 7") == (hotkey_module.MOD_WIN | hotkey_module.MOD_SHIFT, ord("7"))
    assert parse_hotkey("ctrl+f12") == (hotkey_module.MOD_CONTROL, 0x7B)


@pytest.mark.parametrize("spec", ["d", "ctrl+", "super+d", "ctrl+f13", "ctrl+enter", ""])
def test_parse_hotkey_rejects_bad_specs(spec):
    with pytest.raises(ValueError):
        parse_hotkey(spec)


class FakeBackend:
    def __init__(self, ok=True, reason=""):
        self.ok, self.reason = ok, reason
        self.fire = None
        self.stopped = threading.Event()

    def run(self, modifiers, vk, on_hotkey, stop, ready):
        self.args = (modifiers, vk)
        self.fire = on_hotkey
        ready(self.ok, self.reason)
        if self.ok:
            stop.wait(5)
            self.stopped.set()


def test_listener_reports_registration_and_stops_cleanly():
    backend = FakeBackend()
    hits = []
    listener = HotkeyListener("ctrl+alt+d", lambda: hits.append(1), backend)
    assert listener.start() and listener.active
    backend.fire()
    assert hits == [1]
    listener.stop()
    assert backend.stopped.wait(2) and not listener.active


def test_listener_surfaces_a_taken_hotkey_and_a_bad_spec():
    taken = HotkeyListener("ctrl+alt+d", lambda: None, FakeBackend(False, "다른 프로그램이 사용 중입니다."))
    assert taken.start() is False and "사용 중" in taken.reason
    bad = HotkeyListener("nonsense", lambda: None, FakeBackend())
    assert bad.start() is False and bad.reason


def test_listener_is_unsupported_off_windows_without_a_backend(monkeypatch):
    monkeypatch.setattr(hotkey_module.sys, "platform", "linux")
    listener = HotkeyListener("ctrl+alt+d", lambda: None)
    assert listener.start() is False and "Windows" in listener.reason


# ollama provider


def test_ollama_provider_sends_schema_and_returns_text():
    http = ScriptedHttp(lambda m, u, b: (200, {"message": {"content": " {\"a\": 1} "}}))
    provider = OllamaProvider("http://127.0.0.1:11434/", "qwen", http)
    text = provider.complete("system", "user", 0.0, {"type": "object"})
    assert text == '{"a": 1}'
    method, url, body = http.calls[0]
    assert (method, url) == ("POST", "http://127.0.0.1:11434/api/chat")
    assert body["model"] == "qwen" and body["stream"] is False and body["format"] == "json"
    assert body["options"]["temperature"] == 0.0 and "JSON Schema" in body["messages"][0]["content"]


def test_ollama_provider_maps_errors():
    missing = OllamaProvider("http://127.0.0.1:11434", "x", ScriptedHttp(lambda m, u, b: (404, {})))
    with pytest.raises(OllamaModelMissing):
        missing.complete("s", "u")
    down = OllamaProvider("http://127.0.0.1:11434", "x", ScriptedHttp(lambda m, u, b: HttpError("refused")))
    with pytest.raises(OllamaConnectionError):
        down.complete("s", "u")
    broken = OllamaProvider("http://127.0.0.1:11434", "x", ScriptedHttp(lambda m, u, b: (500, {})))
    with pytest.raises(OllamaConnectionError):
        broken.complete("s", "u")
    with pytest.raises(RuntimeError):
        OllamaProvider("http://127.0.0.1:11434", "")


# provider gating


def settings(**values):
    return AppSettings(**values)


def test_ollama_on_this_computer_is_real_but_does_not_send_externally():
    bundle = build_provider(
        settings(provider="ollama", model_name="qwen"), key_getter=lambda: None,
        ollama_factory=lambda url, name: OllamaProvider(url, name, ScriptedHttp(lambda m, u, b: (200, {"message": {"content": "OK"}}))),
    )
    assert bundle.local_only is False and bundle.sends_externally is False
    assert bundle.provider_id == "ollama:qwen"
    assert check_provider(bundle)["verified"] is True


def test_remote_ollama_needs_the_external_switch():
    off = build_provider(settings(provider="ollama", model_name="q", ollama_url="http://192.168.0.5:11434"), key_getter=lambda: None)
    assert off.local_only is True
    on = build_provider(
        settings(provider="ollama", model_name="q", ollama_url="http://192.168.0.5:11434", allow_external_send=True),
        key_getter=lambda: None, ollama_factory=lambda url, name: object(),
    )
    assert on.local_only is False and on.sends_externally is True


def test_ollama_without_a_model_falls_back_to_demo():
    bundle = build_provider(settings(provider="ollama"), key_getter=lambda: None)
    assert bundle.local_only is True and "모델 이름" in bundle.reason


def test_anthropic_marks_external_sending_and_needs_every_condition():
    needs_switch = build_provider(settings(provider="anthropic", model_name="m"), key_getter=lambda: "k")
    assert needs_switch.local_only is True
    needs_key = build_provider(settings(provider="anthropic", model_name="m", allow_external_send=True), key_getter=lambda: None)
    assert needs_key.local_only is True
    ready = build_provider(
        settings(provider="anthropic", model_name="m", allow_external_send=True),
        key_getter=lambda: "k", anthropic_factory=lambda key, name: object(),
    )
    assert ready.sends_externally is True and ready.local_only is False


def test_loopback_detection():
    assert is_loopback_url("http://127.0.0.1:11434") and is_loopback_url("http://localhost:1")
    assert not is_loopback_url("http://192.168.0.5") and not is_loopback_url("http://localhost.evil.com")


def test_settings_validators():
    with pytest.raises(ValueError):
        AppSettings(digest_time="24:00")
    with pytest.raises(ValueError):
        AppSettings(hotkey="d")
    with pytest.raises(ValueError):
        AppSettings(ollama_url="ftp://127.0.0.1")
    assert AppSettings(digest_time="07:05").digest_time == "07:05"


# embeddings


def artifact(index, text):
    return Artifact(
        id=f"a{index}", kind=ArtifactKind.TASK, text=text, source_doc="t.csv", location="1",
        scope=DecisionScope(scope_kind=ScopeKind.GLOBAL),
    )


def vector_for(text):
    return [1.0, 0.0] if "인증" in text or "로그인" in text else [0.0, 1.0]


def embed_handler(method, url, body):
    return 200, {"embeddings": [vector_for(t) for t in body["input"]]}


def test_embedding_ranker_adds_semantically_close_items_and_caches():
    http = ScriptedHttp(embed_handler)
    db = StateDB(":memory:")
    ranker = make_extra_ranker(OllamaEmbedder("http://127.0.0.1:11434", "m", db, http))
    items = [artifact(1, "로그인 화면 개발"), artifact(2, "발표 자료 정리"), artifact(3, "인증 메일 발송")]
    picked = ranker("이메일 인증 제외", items)
    assert {a.id for a in picked} == {"a1", "a3"}
    assert MIN_SIMILARITY > 0
    calls = len(http.calls)
    assert ranker("이메일 인증 제외", items)
    assert len(http.calls) == calls


def test_embedding_failure_falls_back_to_nothing_extra():
    http = ScriptedHttp(lambda m, u, b: HttpError("down"))
    ranker = make_extra_ranker(OllamaEmbedder("http://127.0.0.1:11434", "m", StateDB(":memory:"), http))
    assert ranker("q", [artifact(1, "x")]) == []
    bad_count = ScriptedHttp(lambda m, u, b: (200, {"embeddings": [[1.0]]}))
    ranker = make_extra_ranker(OllamaEmbedder("http://127.0.0.1:11434", "m", StateDB(":memory:"), bad_count))
    assert ranker("q", [artifact(1, "x")]) == []
    assert ranker("q", []) == []


def test_embedding_vectors_are_numeric_arrays():
    http = ScriptedHttp(embed_handler)
    vectors = OllamaEmbedder("http://127.0.0.1:11434", "m", StateDB(":memory:"), http).embed(["인증", "발표"])
    assert np.array(vectors).shape == (2, 2)
