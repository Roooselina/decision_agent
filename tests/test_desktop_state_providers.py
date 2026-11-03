from __future__ import annotations

import datetime

import pytest

from decision_agent.desktop.providers import (
    BudgetExceeded,
    CountingProvider,
    PersistentCachingProvider,
    build_provider,
)
from decision_agent.desktop.settings import AppSettings
from decision_agent.desktop.state import FindingInput, StateDB
from decision_agent.llm.base import LLMProvider


class Echo(LLMProvider):
    def __init__(self):
        self.calls = 0

    def complete(self, system, user, temperature=0.0, json_schema=None):
        self.calls += 1
        return f"{system}|{user}|{self.calls}"


def item(artifact_id, label, text="t"):
    return FindingInput(artifact_id, "TASK", text, "f.md", "1", label, 1.0, "ev", "act", None)


def test_findings_change_detection_and_status_persistence():
    db = StateDB()
    assert db.replace_findings("t", [item("a", "INCONSISTENT"), item("b", "CONSISTENT")]) == ["a", "b"]
    assert db.replace_findings("t", [item("a", "INCONSISTENT"), item("b", "CONSISTENT")]) == []
    db.mark_notified([("t", "a")])
    db.set_finding_status("t", "a", "dismissed")
    assert [r["artifact_id"] for r in db.list_findings()] == ["b"]
    db.replace_findings("t", [item("a", "INCONSISTENT"), item("b", "CONSISTENT")])
    assert [r["artifact_id"] for r in db.list_findings()] == ["b"]
    changed = db.replace_findings("t", [item("a", "CONSISTENT"), item("b", "CONSISTENT")])
    assert changed == ["a"]
    assert {r["artifact_id"] for r in db.list_findings()} == {"a", "b"}
    db.replace_findings("t", [item("b", "CONSISTENT")])
    assert [r["artifact_id"] for r in db.list_findings(include_hidden=True)] == ["b"]


def test_snooze_hides_until_deadline():
    db = StateDB()
    db.replace_findings("t", [item("a", "INCONSISTENT")])
    future = (datetime.datetime.now() + datetime.timedelta(hours=2)).isoformat()
    db.set_finding_status("t", "a", "snoozed", future)
    assert db.list_findings() == []
    past = (datetime.datetime.now() - datetime.timedelta(hours=2)).isoformat()
    db.set_finding_status("t", "a", "snoozed", past)
    assert len(db.list_findings()) == 1


def test_questions_and_answers_follow_decision_fingerprint():
    db = StateDB()
    assert db.set_question("t", "q?") is True
    assert db.set_question("t", "q?") is False
    db.add_answer("t", "yes", "fp1")
    assert db.get_answers("t", "fp1") == ["yes"]
    assert db.get_answers("t", "fp2") == []
    db.clear_stale_answers("t", "fp2")
    assert db.get_answers("t", "fp1") == []


def test_persistent_cache_only_for_temperature_zero():
    db = StateDB()
    inner = Echo()
    provider = PersistentCachingProvider(inner, db, "p1")
    first = provider.complete("s", "u", 0.0)
    assert provider.complete("s", "u", 0.0) == first and inner.calls == 1
    provider.complete("s", "u", 0.7)
    provider.complete("s", "u", 0.7)
    assert inner.calls == 3
    other = PersistentCachingProvider(inner, db, "p2")
    other.complete("s", "u", 0.0)
    assert inner.calls == 4


def test_persistent_cache_skips_cut_off_json_so_retries_ask_again():
    """QA E01/E04: a cut-off JSON answer was cached, so every retry got the same broken text."""

    class Flaky(LLMProvider):
        def __init__(self):
            self.answers = ['```json\n{"items": [{"index": 1, "te', '{"items": []}']
            self.calls = 0

        def complete(self, system, user, temperature=0.0, json_schema=None):
            self.calls += 1
            return self.answers.pop(0)

    db = StateDB()
    inner = Flaky()
    provider = PersistentCachingProvider(inner, db, "p1")
    schema = {"type": "object"}
    assert provider.complete("s", "u", 0.0, schema).startswith("```json")
    assert provider.complete("s", "u", 0.0, schema) == '{"items": []}'
    assert provider.complete("s", "u", 0.0, schema) == '{"items": []}'
    assert inner.calls == 2

    # A broken answer stored by an older version is not reused either.
    key = provider._key("s", "old")
    db.cache_put(key, '{"items": [')
    inner.answers = ['{"items": [1]}']
    assert provider.complete("s", "old", 0.0, schema) == '{"items": [1]}'
    assert db.cache_get(key) == '{"items": [1]}'


def test_counting_provider_budget():
    counter = CountingProvider(Echo(), 2)
    counter.complete("s", "1")
    counter.complete("s", "2")
    with pytest.raises(BudgetExceeded):
        counter.complete("s", "3")
    counter.reset()
    counter.complete("s", "3")


def test_build_provider_requires_every_condition_before_sending_documents():
    made = []

    def factory(key, model):
        made.append((key, model))
        return Echo()

    def attempt(**settings):
        return build_provider(
            AppSettings(**settings), key_getter=lambda: settings_key[0], anthropic_factory=factory
        )

    settings_key = ["k" * 12]
    assert attempt(provider="mock", allow_external_send=True, model_name="m").local_only
    assert "꺼져" in attempt(model_name="m").reason
    settings_key[0] = None
    assert "키" in attempt(allow_external_send=True, model_name="m").reason
    settings_key[0] = "k" * 12
    assert "모델" in attempt(allow_external_send=True).reason
    assert made == []
    bundle = attempt(allow_external_send=True, model_name="m1")
    assert not bundle.local_only and bundle.provider_id == "anthropic:m1"
    assert made == [("k" * 12, "m1")]
