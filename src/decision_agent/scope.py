from __future__ import annotations

from typing import Any

from decision_agent.models import DecisionScope, ProjectConfig, ScopeKind

DIMENSIONS = ("release", "module", "user_group", "environment")


def _alias_reverse(aliases: dict[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for dim, values in aliases.items():
        result[dim] = {}
        for canonical, items in values.items():
            result[dim][canonical.lower()] = canonical
            for item in items:
                result[dim][str(item).lower()] = canonical
    return result


def normalize_scope(
    raw_terms: dict[str, str | None], project_config: ProjectConfig, aliases: dict[str, Any]
) -> DecisionScope:
    reverse = _alias_reverse(aliases)
    values: dict[str, str | None] = {d: None for d in DIMENSIONS}
    unmapped: list[str] = []
    for dim in DIMENSIONS:
        raw = raw_terms.get(dim)
        if not raw:
            continue
        text = str(raw).strip()
        lower = text.lower()
        if (
            dim == "release"
            and lower in {"이번 버전", "이번 mvp"}
            and project_config.current_release
        ):
            values[dim] = project_config.current_release
            continue
        canonical = reverse.get(dim, {}).get(lower)
        if canonical is None:
            unmapped.append(text)
        else:
            values[dim] = canonical
    if unmapped:
        kind = ScopeKind.UNKNOWN
    elif any(values.values()):
        kind = ScopeKind.SCOPED
    else:
        kind = ScopeKind.GLOBAL
    return DecisionScope(scope_kind=kind, unmapped_terms=unmapped, **values)


def scope_specificity(scope: DecisionScope) -> int:
    return sum(getattr(scope, d) is not None for d in DIMENSIONS)


def scope_is_global(scope: DecisionScope) -> bool:
    return scope.scope_kind == ScopeKind.GLOBAL


def scope_is_unknown(scope: DecisionScope) -> bool:
    return scope.scope_kind == ScopeKind.UNKNOWN


def scope_matches(decision_scope: DecisionScope, target_scope: DecisionScope) -> bool | None:
    if (
        decision_scope.scope_kind == ScopeKind.UNKNOWN
        or target_scope.scope_kind == ScopeKind.UNKNOWN
    ):
        return None
    if decision_scope.scope_kind == ScopeKind.GLOBAL:
        return True
    for dim in DIMENSIONS:
        expected = getattr(decision_scope, dim)
        if expected is not None and expected != getattr(target_scope, dim):
            return False
    return True


def scope_overlaps(a: DecisionScope, b: DecisionScope) -> bool | None:
    if a.scope_kind == ScopeKind.UNKNOWN or b.scope_kind == ScopeKind.UNKNOWN:
        return None
    if a.scope_kind == ScopeKind.GLOBAL or b.scope_kind == ScopeKind.GLOBAL:
        return True
    for dim in DIMENSIONS:
        av = getattr(a, dim)
        bv = getattr(b, dim)
        if av is not None and bv is not None and av != bv:
            return False
    return True
