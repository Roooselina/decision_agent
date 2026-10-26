"""Local pre-filter: answer obviously consistent lines without a model call.

The filter is deliberately narrow. It only fires for a plain replacement of one value by
another (a number, a date, a product or library name), when the line already shows the new
value and shows none of the old values. Everything else goes to the model: decisions that
cancel, exclude or restrict something, scoped decisions, reverts without a visible value, and
every line that mentions an old value or no value at all. It never answers INCONSISTENT, so it
can only hide a problem when a line repeats the new value and still contradicts the decision.
"""

from __future__ import annotations

import re

from decision_agent.consistency import PreparedCheck
from decision_agent.models import ConsistencyLabel, ConsistencyResult, Decision, ScopeKind

_UNITS = "만원|단계|장|개|명|일|월|시|분|초|주|회|건|원|배|차|번|%|MB|GB|KB"
_NUMBER = re.compile(rf"\d+(?:[.,:]\d+)*(?:{_UNITS})?")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_.+#]*")

# Decisions that remove, stop or limit something must be judged by the model: the value they
# mention is the thing that should disappear, so a line that repeats it may be wrong.
_NEGATION = (
    "않", "제외", "취소", "중단", "삭제", "폐기", "폐지", "없", "불가", "금지", "보류", "연기",
    "미적용", "하지 말", "빼", "철회", "no longer", "remove", "drop", "cancel",
    "disable", "deprecate", "postpone", "defer", "exclude", "stop",
)

REASON = "새 결정의 값({values})이 이미 반영되어 있고 이전 값은 없어 AI 확인 없이 일치로 분류했습니다."


def value_tokens(text: str) -> set[str]:
    """Numbers with a unit and Latin words, lower-cased. Generic Korean words are ignored."""
    tokens = {m.group(0).lower() for m in _NUMBER.finditer(text)}
    tokens |= {m.group(0).lower().rstrip(".") for m in _LATIN.finditer(text)}
    return {t for t in tokens if t}


def _has_negation(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _NEGATION)


class PrefilterContext:
    """Value tokens of one decision against the earlier decisions of its topic."""

    def __init__(self, history: list[Decision]) -> None:
        self.history = history
        self._cache: dict[str, tuple[set[str], set[str]] | None] = {}

    def values_for(self, decision: Decision) -> tuple[set[str], set[str]] | None:
        """(new values, old values) for the decision, or None when the filter must not run."""
        if decision.id in self._cache:
            return self._cache[decision.id]
        result = self._compute(decision)
        self._cache[decision.id] = result
        return result

    def _compute(self, decision: Decision) -> tuple[set[str], set[str]] | None:
        if decision.scope.scope_kind != ScopeKind.GLOBAL or decision.due_date:
            return None
        if _has_negation(decision.text):
            return None
        earlier = [d for d in self.history if (d.date, d.id) < (decision.date, decision.id)]
        earlier = [d for d in earlier if d.text.strip() != decision.text.strip()]
        if not earlier:
            return None
        if any(_has_negation(d.text) for d in earlier[-1:]):
            # The decision undoes a removal. The value it names may be the very thing that was
            # cut, so a line that repeats the old wording needs a model.
            return None
        current = value_tokens(decision.text)
        previous = value_tokens(earlier[-1].text)
        every_old: set[str] = set()
        for d in earlier:
            every_old |= value_tokens(d.text)
        new_values = current - previous
        old_values = every_old - current
        if not new_values:
            return None
        return new_values, old_values


def local_verdict(prepared: PreparedCheck, context: PrefilterContext) -> ConsistencyResult | None:
    """CONSISTENT when the line carries the new value and no old value, otherwise None."""
    if prepared.early is not None or prepared.decision is None:
        return None
    artifact = prepared.artifact
    if prepared.decision.due_date or artifact.metadata.get("date"):
        return None
    values = context.values_for(prepared.decision)
    if values is None:
        return None
    new_values, old_values = values
    found = value_tokens(artifact.text)
    hit = found & new_values
    if not hit or found & old_values:
        return None
    return ConsistencyResult(
        artifact_id=artifact.id,
        label=ConsistencyLabel.CONSISTENT,
        confidence=1.0,
        evidence=REASON.format(values=", ".join(sorted(hit))),
        expected_action="조치가 필요하지 않습니다.",
        effective_decision_id=prepared.decision.id,
    )
