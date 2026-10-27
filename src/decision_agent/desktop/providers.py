from __future__ import annotations

import hashlib
import os
import re
import threading
import urllib.parse
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from decision_agent.desktop.secrets import get_api_key
from decision_agent.desktop.settings import AppSettings
from decision_agent.desktop.state import StateDB
from decision_agent.llm.base import LLMProvider
from decision_agent.llm.mock_provider import MockProvider


class BudgetExceeded(RuntimeError):
    """Raised when one analysis cycle would exceed the configured number of live LLM calls."""


class CheckCancelled(BudgetExceeded):
    """Raised instead of a model call once the app is stopping or the folder was removed.

    It is a BudgetExceeded so every place that already stops a cycle early on the budget also
    stops on it, and no further calls are spent.
    """


@dataclass
class ProviderBundle:
    """local_only is True only for the built-in demo judge, which is not a real check.

    sends_externally is True when document text leaves the computer for another service.
    """

    provider: LLMProvider
    provider_id: str
    local_only: bool
    reason: str
    sends_externally: bool = False


def describe_llm_error(exc: Exception) -> tuple[str, bool]:
    """Turn an exception from a model call into a plain Korean message.

    The second value tells whether retrying later is pointless until the user changes a setting
    (wrong key, unknown model), so the program does not keep calling a service that refuses.
    """
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if name in {"AuthenticationError", "PermissionDeniedError"} or status in {401, 403}:
        return "API 키가 올바르지 않거나 권한이 없습니다. 키를 다시 저장해 주세요.", True
    if name == "OllamaModelMissing":
        return f"{exc} 터미널에서 'ollama pull 모델이름'으로 받아 주세요.", True
    if name == "OllamaConnectionError":
        return "Ollama 서버에 연결하지 못했습니다. Ollama가 실행 중인지 확인해 주세요.", False
    if name == "NotFoundError" or status == 404:
        return "모델 이름을 찾을 수 없습니다. 설정의 모델 이름을 확인해 주세요.", True
    if name == "RateLimitError" or status == 429:
        return "요청 한도에 걸렸습니다. 잠시 뒤 자동으로 다시 시도합니다.", False
    if name in {"APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout"}:
        return "모델 서비스에 연결하지 못했습니다. 네트워크를 확인해 주세요.", False
    return f"모델 호출에 실패했습니다: {str(exc)[:200]}", False


def check_provider(bundle: ProviderBundle) -> dict:
    """Make one tiny call to see whether the chosen provider works. Used by the settings page."""
    if bundle.local_only:
        return {
            "ok": True,
            "verified": False,
            "message": f"실제 모델이 아니라 로컬 규칙 판정기입니다. {bundle.reason}",
        }
    try:
        text = bundle.provider.complete(
            "Reply with the single word OK.", "ping", 0.0, None
        )
    except Exception as exc:  # noqa: BLE001 - report any failure to the user
        message, _ = describe_llm_error(exc)
        return {"ok": False, "verified": False, "message": message}
    if not text:
        return {"ok": False, "verified": False, "message": "모델이 빈 응답을 돌려줬습니다."}
    return {"ok": True, "verified": True, "message": f"연결 확인: {bundle.provider_id}"}


_TASK = re.compile(r"TASK:([A-Z_]+)")

# Work that a smaller model does well: reading one sentence, naming a relation, picking from a
# short list, routing, and scoring questions. The consistency judgement stays on the main model.
CHEAP_TASKS = frozenset(
    {
        "DECISION_EXTRACT", "DECISION_EXTRACT_BATCH", "RELATION_CLASSIFY", "RELATION_CLASSIFY_BATCH", "IMPACT_SELECT",
        "QUESTION_GENERATE", "QUESTION_GAIN", "QUESTION_GAIN_BATCH", "TOOL_SELECT",
    }
)


def task_of(system: str) -> str:
    match = _TASK.search(system)
    return match.group(1) if match else "OTHER"


class TieredProvider(LLMProvider):
    """Sends easy tasks to a cheaper model and the consistency judgement to the main model."""

    def __init__(self, main: LLMProvider, cheap: LLMProvider) -> None:
        self.main = main
        self.cheap = cheap

    def complete(
        self, system: str, user: str, temperature: float = 0.0, json_schema: dict | None = None
    ) -> str:
        target = self.cheap if task_of(system) in CHEAP_TASKS else self.main
        return target.complete(system, user, temperature, json_schema)


class CountingProvider(LLMProvider):
    """Counts calls that actually reach the wrapped provider and enforces a per-cycle budget."""

    def __init__(
        self,
        inner: LLMProvider,
        budget: int,
        max_parallel: int = 0,
        cancelled: threading.Event | None = None,
    ) -> None:
        self.inner = inner
        self.budget = budget
        self.cancelled = cancelled
        self.calls = 0
        self.by_task: Counter[str] = Counter()
        self._lock = threading.Lock()
        # Several topics run at once, each with its own parallel calls. The semaphore keeps the
        # total number of requests in flight at max_parallel so the account rate limit is not hit.
        self._slots = threading.Semaphore(max_parallel) if max_parallel > 0 else None

    def reset(self) -> None:
        with self._lock:
            self.calls = 0
            self.by_task = Counter()

    def complete(
        self, system: str, user: str, temperature: float = 0.0, json_schema: dict | None = None
    ) -> str:
        self._check_cancelled()
        with self._lock:
            if self.calls >= self.budget:
                raise BudgetExceeded(f"한 번의 점검에서 사용할 LLM 호출 한도({self.budget}회)에 도달했습니다.")
            self.calls += 1
            self.by_task[task_of(system)] += 1
        if self._slots is None:
            return self.inner.complete(system, user, temperature, json_schema)
        with self._slots:
            # A call that waited for a free slot must not start after the check was cancelled.
            self._check_cancelled()
            return self.inner.complete(system, user, temperature, json_schema)

    def _check_cancelled(self) -> None:
        if self.cancelled is not None and self.cancelled.is_set():
            raise CheckCancelled("점검을 중단했습니다.")


class PersistentCachingProvider(LLMProvider):
    """Stores temperature-zero responses in the state database.

    Rebuilding the decision store after a file change then costs no new calls for text that
    was already seen. Sampling calls (temperature above zero) are never cached, because each
    sample must stay independent. The key includes the provider id, so switching between the
    local mock and a real model never mixes their answers.
    """

    def __init__(self, inner: LLMProvider, db: StateDB, provider_id: str) -> None:
        self.inner = inner
        self.db = db
        self.provider_id = provider_id

    def _key(self, system: str, user: str) -> str:
        raw = f"{self.provider_id}\x00{system}\x00{user}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def complete(
        self, system: str, user: str, temperature: float = 0.0, json_schema: dict | None = None
    ) -> str:
        if temperature > 0.0:
            return self.inner.complete(system, user, temperature, json_schema)
        key = self._key(system, user)
        cached = self.db.cache_get(key)
        if cached is not None:
            return cached
        response = self.inner.complete(system, user, temperature, json_schema)
        self.db.cache_put(key, response)
        return response


def is_loopback_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _build_ollama(
    settings: AppSettings,
    local: ProviderBundle,
    factory: Callable[[str, str], LLMProvider] | None,
) -> ProviderBundle:
    model = settings.model_name
    if not model:
        local.reason = "Ollama 모델 이름이 없어 규칙 기반 로컬 판정기를 사용합니다."
        return local
    remote = not is_loopback_url(settings.ollama_url)
    if remote and not settings.allow_external_send:
        local.reason = "Ollama 주소가 이 컴퓨터가 아니고 외부 전송이 꺼져 있어 로컬 판정기를 사용합니다."
        return local
    if factory is None:
        from decision_agent.llm.ollama_provider import OllamaProvider

        def factory(url: str, name: str) -> LLMProvider:
            return OllamaProvider(url, name)

    reason = (
        "다른 컴퓨터의 Ollama 서버를 사용합니다." if remote
        else "이 컴퓨터의 Ollama 모델을 사용합니다. 문서가 컴퓨터 밖으로 나가지 않습니다."
    )
    main = factory(settings.ollama_url, model)
    provider_id = f"ollama:{model}"
    cheap_name = settings.cheap_model_name.strip()
    if cheap_name and cheap_name != model:
        main = TieredProvider(main, factory(settings.ollama_url, cheap_name))
        provider_id = f"{provider_id}+{cheap_name}"
    return ProviderBundle(main, provider_id, False, reason, remote)


def build_provider(
    settings: AppSettings,
    key_getter: Callable[[], str | None] = get_api_key,
    anthropic_factory: Callable[[str, str], LLMProvider] | None = None,
    ollama_factory: Callable[[str, str], LLMProvider] | None = None,
) -> ProviderBundle:
    """Choose the provider. Documents leave the computer only when every condition holds."""
    local = ProviderBundle(
        MockProvider(), "mock", True, "규칙 기반 로컬 판정기를 사용합니다. 실제 문서에는 거의 탐지하지 못합니다."
    )
    if settings.provider == "mock":
        return local
    if settings.provider == "ollama":
        return _build_ollama(settings, local, ollama_factory)
    if not settings.allow_external_send:
        local.reason = "외부 전송이 꺼져 있어 규칙 기반 로컬 판정기를 사용합니다."
        return local
    key = key_getter()
    if not key:
        local.reason = "API 키가 없어 규칙 기반 로컬 판정기를 사용합니다."
        return local
    model = settings.model_name or os.getenv("DECISION_AGENT_MODEL") or ""
    if not model:
        local.reason = "모델 이름이 없어 규칙 기반 로컬 판정기를 사용합니다."
        return local
    if anthropic_factory is None:
        from decision_agent.llm.anthropic_provider import AnthropicProvider

        def anthropic_factory(api_key: str, model_name: str) -> LLMProvider:
            return AnthropicProvider(api_key=api_key, model=model_name)

    main = anthropic_factory(key, model)
    provider_id = f"anthropic:{model}"
    cheap_name = settings.cheap_model_name.strip()
    if cheap_name and cheap_name != model:
        main = TieredProvider(main, anthropic_factory(key, cheap_name))
        provider_id = f"{provider_id}+{cheap_name}"
    return ProviderBundle(main, provider_id, False, "외부 LLM을 사용합니다.", True)
