from decision_agent.models import DecisionScope, ProjectConfig, ScopeKind
from decision_agent.scope import normalize_scope, scope_matches, scope_overlaps, scope_specificity

ALIASES = {
    "release": {"mvp": ["MVP", "이번 버전"], "production": ["정식 버전"]},
    "module": {"login": ["로그인"]},
    "user_group": {},
    "environment": {},
}


def test_normalize_alias_and_relative():
    cfg = ProjectConfig(current_release="mvp")
    scope = normalize_scope({"release": "이번 버전", "module": "로그인"}, cfg, ALIASES)
    assert scope.scope_kind == ScopeKind.SCOPED
    assert scope.release == "mvp"
    assert scope.module == "login"


def test_unmapped_is_unknown():
    scope = normalize_scope({"release": "알파"}, ProjectConfig(current_release="mvp"), ALIASES)
    assert scope.scope_kind == ScopeKind.UNKNOWN
    assert scope.unmapped_terms == ["알파"]


def test_scope_matching():
    glob = DecisionScope(scope_kind=ScopeKind.GLOBAL)
    mvp = DecisionScope(scope_kind=ScopeKind.SCOPED, release="mvp")
    prod = DecisionScope(scope_kind=ScopeKind.SCOPED, release="production")
    unknown = DecisionScope(scope_kind=ScopeKind.UNKNOWN)
    assert scope_matches(glob, mvp) is True
    assert scope_matches(mvp, mvp) is True
    assert scope_matches(mvp, prod) is False
    assert scope_matches(unknown, mvp) is None
    assert scope_overlaps(mvp, prod) is False
    assert scope_specificity(mvp) == 1
