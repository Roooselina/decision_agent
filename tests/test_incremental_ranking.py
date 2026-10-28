"""Candidate ranking must not depend on artifact order or on unrelated artifacts."""

from __future__ import annotations

import random

from decision_agent.impact import _rank_candidates, find_candidate_artifacts
from decision_agent.models import Artifact, ArtifactKind, Decision, DecisionScope, ScopeKind
from decision_agent.ranking import stable_order

GLOBAL = DecisionScope(scope_kind=ScopeKind.GLOBAL)


def line(artifact_id: str, text: str) -> Artifact:
    return Artifact(
        id=artifact_id, kind=ArtifactKind.REQUIREMENT, text=text, source_doc="r.md",
        location="1", scope=GLOBAL,
    )


def decision(text: str) -> Decision:
    return Decision(id="D1", text=text, date="2026-01-01", source_doc="m.md", topic="t", scope=GLOBAL)


def templated(count: int) -> list[Artifact]:
    # Identical texts give exactly equal scores, the worst case for an unstable sort.
    return [line(f"REQ_{i:03d}", "사진은 최대 5장까지 업로드한다") for i in range(count)]


def test_stable_order_breaks_ties_by_key():
    assert stable_order([0.5, 0.9, 0.5, 0.5000000000001], ["c", "z", "a", "b"]) == [1, 2, 3, 0]


def test_equal_scores_give_the_same_top_k_regardless_of_order():
    artifacts = templated(40)
    query = decision("사진은 최대 3장까지 업로드한다")
    expected = [a.id for a in find_candidate_artifacts(query, artifacts, 8)]
    assert expected == [f"REQ_{i:03d}" for i in range(8)]
    for seed in range(5):
        shuffled = list(artifacts)
        random.Random(seed).shuffle(shuffled)
        assert [a.id for a in find_candidate_artifacts(query, shuffled, 8)] == expected


def corpus() -> list[Artifact]:
    texts = [
        "사진은 최대 5장까지 업로드한다", "로그인은 이메일 인증으로 한다", "결제는 카드만 지원한다",
        "검색은 키워드 검색으로 한다", "채팅 알림은 푸시로 보낸다", "관리자 페이지는 다음 릴리스에 낸다",
        "로그는 30일 보관한다", "지도는 카카오 지도를 쓴다", "가격은 9900원이다", "리뷰는 별점 5점제다",
    ]
    items = []
    # 110 lines: one more line does not cross a step of effective_k (it grows every 30 lines).
    for round_number in range(11):
        for index, text in enumerate(texts):
            items.append(line(f"A{round_number:02d}_{index:02d}", f"{text} ({round_number}차 문서)"))
    return items


def topics() -> list[Decision]:
    return [
        decision("사진은 최대 3장까지 업로드한다"),
        decision("로그는 90일 보관한다"),
        decision("가격은 12000원으로 바꾼다"),
        decision("검색은 의미 검색으로 바꾼다"),
    ]


def ranked(query: Decision, artifacts: list[Artifact]) -> list[str]:
    return [a.id for a in _rank_candidates(query.text, [query], artifacts, 8)]


def test_unrelated_artifact_changes_no_candidate_set():
    base = corpus()
    before = {d.text: ranked(d, base) for d in topics()}
    extra = base + [line("ZZ_NOTE", "server logs are kept 30 days")]
    after = {d.text: ranked(d, extra) for d in topics()}
    assert before == after


def test_matching_artifact_enters_and_nothing_else_moves_in_unrelated_topics():
    base = corpus()
    before = {d.text: ranked(d, base) for d in topics()}
    new = line("ZZ_NEW", "사진은 최대 3장까지 업로드한다")
    after = {d.text: ranked(d, base + [new]) for d in topics()}
    photo = topics()[0].text
    assert "ZZ_NEW" in after[photo]
    # Exactly one line made room for it: the new line is the only addition.
    assert set(after[photo]) - set(before[photo]) == {"ZZ_NEW"}
    for text, ids in before.items():
        if text != photo:
            assert after[text] == ids
