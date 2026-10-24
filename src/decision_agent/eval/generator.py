from __future__ import annotations

import argparse
import datetime
import random
from pathlib import Path

from decision_agent.eval.builder import DecisionSpec, build_case, relation
from decision_agent.eval.dataset import (
    EvalCase,
    GoldArtifact,
    UserFact,
    make_artifact,
    save_cases,
    scoped,
    unknown_scope,
)
from decision_agent.models import Artifact, ArtifactKind, DecisionScope, RelationType

AUTHOR = "generator seed-based templates (assistant-designed)"
DECOYS = [
    "DB 인덱스 설계 문서 정리",
    "서버 배포 스크립트 작성",
    "API 문서 정리",
    "회원 탈퇴 정책 문서화",
    "디자인 시안 검토",
    "로그 수집 설정 점검",
]
PREFIX = {
    ArtifactKind.REQUIREMENT: "REQ",
    ArtifactKind.TASK: "TASK",
    ArtifactKind.TEST: "TEST",
    ArtifactKind.SCHEDULE: "SCHED",
}


class ArtifactSet:
    """Collects artifacts and their gold labels together so they cannot drift apart."""

    def __init__(self) -> None:
        self.artifacts: list[Artifact] = []
        self.gold: list[GoldArtifact] = []
        self._count: dict[ArtifactKind, int] = {}

    def add(
        self,
        kind: ArtifactKind,
        text: str,
        label: str,
        scope: DecisionScope | None = None,
        affected: bool = True,
        clarify: bool = False,
        trap: bool = False,
        **meta: str,
    ) -> str:
        self._count[kind] = self._count.get(kind, 0) + 1
        artifact_id = f"{PREFIX[kind]}_{self._count[kind]:02d}"
        self.artifacts.append(make_artifact(artifact_id, kind, text, scope, **meta))
        self.gold.append(
            GoldArtifact(
                artifact_id=artifact_id,
                label=label,  # type: ignore[arg-type]
                affected=affected,
                needs_clarification=clarify,
                scope_trap=trap,
            )
        )
        return artifact_id

    def add_decoys(self, rng: random.Random, low: int, high: int) -> None:
        for text in rng.sample(DECOYS, rng.randint(low, high)):
            self.add(ArtifactKind.TASK, text, "CONSISTENT", affected=False, status="진행 중")


def _dates(rng: random.Random, count: int) -> list[str]:
    day = datetime.date(2026, 9, 1) + datetime.timedelta(days=rng.randint(0, 8))
    result = []
    for _ in range(count):
        result.append(day.isoformat())
        day += datetime.timedelta(days=rng.randint(3, 9))
    return result


def _next_monday(date_text: str) -> str:
    day = datetime.date.fromisoformat(date_text)
    return (day + datetime.timedelta(days=7 - day.weekday())).isoformat()


def _other(framework: str) -> str:
    return "Vue" if framework == "React" else "React"


def _tag(case_id: str, level: int) -> dict:
    return {"case_id": case_id, "level": level, "source": "synthetic", "author": AUTHOR}


def level1(rng: random.Random, idx: int) -> EvalCase:
    fw = rng.choice(["React", "Vue"])
    other = _other(fw)
    (d1,) = _dates(rng, 1)
    hard = rng.random() < 0.5
    # The hard variant names no shared word between the decision and the stale tasks, so
    # a character TF-IDF retriever has nothing to match (a recall stress case).
    decision = f"{fw}를 사용한다." if hard else f"프론트엔드는 {fw}를 사용한다."
    infix = "" if hard else "프론트엔드 "
    arts = ArtifactSet()
    stale = [f"{other} {infix}로그인 화면 개발", f"{other} {infix}컴포넌트 구조 설계"]
    for text in rng.sample(stale, rng.randint(1, 2)):
        arts.add(ArtifactKind.TASK, text, "INCONSISTENT", status="진행 중")
    arts.add(ArtifactKind.TASK, f"{fw} {infix}대시보드 화면 개발", "CONSISTENT", status="진행 중")
    arts.add(ArtifactKind.REQUIREMENT, f"프론트엔드는 {fw} 기반으로 구현한다.", "CONSISTENT")
    arts.add(ArtifactKind.TEST, f"{fw} {infix}컴포넌트 렌더링 테스트", "CONSISTENT")
    arts.add_decoys(rng, 1, 3)
    return build_case(
        **_tag(f"syn_l1_{idx:02d}", 1),
        description=f"Single framework decision ({fw}); some tasks still use {other}.",
        topic="frontend_framework",
        decisions=[DecisionSpec(d1, decision)],
        relations=[],
        artifacts=arts.artifacts,
        gold=arts.gold,
        tags=["retrieval_hard"] if hard else [],
    )


def level2_swap(rng: random.Random, idx: int) -> EvalCase:
    first = rng.choice(["React", "Vue"])
    second = _other(first)
    d1, d2 = _dates(rng, 2)
    arts = ArtifactSet()
    arts.add(ArtifactKind.TASK, f"{first} 로그인 화면 개발", "INCONSISTENT", status="진행 중")
    if rng.random() < 0.6:
        arts.add(ArtifactKind.TEST, f"{first} 컴포넌트 스냅샷 테스트", "INCONSISTENT")
    arts.add(ArtifactKind.TASK, f"{second} 대시보드 화면 개발", "CONSISTENT", status="진행 중")
    arts.add(ArtifactKind.REQUIREMENT, f"프론트엔드는 {second} 기반으로 구현한다.", "CONSISTENT")
    arts.add_decoys(rng, 1, 2)
    return build_case(
        **_tag(f"syn_l2s_{idx:02d}", 2),
        description=f"Framework changed from {first} to {second}.",
        topic="frontend_framework",
        decisions=[
            DecisionSpec(d1, f"{first}를 사용한다."),
            DecisionSpec(d2, f"{second}로 변경한다."),
        ],
        relations=[relation("D01", "D02", RelationType.SUPERSEDE)],
        artifacts=arts.artifacts,
        gold=arts.gold,
    )


def level2_cancel(rng: random.Random, idx: int) -> EvalCase:
    d1, d2 = _dates(rng, 2)
    place = rng.choice(["로그인", "회원가입"])
    arts = ArtifactSet()
    arts.add(
        ArtifactKind.TASK, "이메일 인증 메일 발송 개발 진행 중", "INCONSISTENT", status="진행 중"
    )
    arts.add(ArtifactKind.TEST, "이메일 인증 링크 만료 테스트 필수", "INCONSISTENT")
    arts.add(ArtifactKind.TASK, "이메일 인증 화면 개발 (삭제됨)", "CONSISTENT", status="삭제")
    arts.add_decoys(rng, 1, 2)
    return build_case(
        **_tag(f"syn_l2c_{idx:02d}", 2),
        description="Email authentication was cancelled globally.",
        topic="email_auth",
        decisions=[
            DecisionSpec(d1, f"{place}에 이메일 인증을 적용한다."),
            DecisionSpec(d2, "이메일 인증 도입은 취소한다."),
        ],
        relations=[relation("D01", "D02", RelationType.CANCEL)],
        artifacts=arts.artifacts,
        gold=arts.gold,
    )


def _email_scope_artifacts(rng: random.Random, arts: ArtifactSet) -> None:
    mvp = scoped(release="mvp")
    prod = scoped(release="production")
    arts.add(
        ArtifactKind.TASK, "MVP 이메일 인증 개발 진행 중", "INCONSISTENT", mvp, status="진행 중"
    )
    if rng.random() < 0.6:
        arts.add(
            ArtifactKind.TASK,
            "MVP 이메일 인증 메일 템플릿 구현 예정",
            "INCONSISTENT",
            mvp,
            status="예정",
        )
    arts.add(ArtifactKind.TEST, "MVP 이메일 인증 발송 테스트 필수", "INCONSISTENT", mvp)
    arts.add(
        ArtifactKind.TASK,
        "MVP 이메일 인증 개발 삭제됨",
        "CONSISTENT",
        mvp,
        trap=False,
        status="삭제",
    )
    arts.add(
        ArtifactKind.TASK,
        "Production 이메일 인증 구현 예정",
        "CONSISTENT",
        prod,
        trap=True,
        status="예정",
    )
    if rng.random() < 0.6:
        arts.add(
            ArtifactKind.TEST,
            "Production 이메일 인증 재발송 테스트 필수",
            "CONSISTENT",
            prod,
            trap=True,
        )
    arts.add(
        ArtifactKind.REQUIREMENT,
        "로그인에 이메일 인증을 적용한다.",
        "CONSISTENT",
        trap=True,
    )


def level3(rng: random.Random, idx: int) -> EvalCase:
    d1, d2 = _dates(rng, 2)
    arts = ArtifactSet()
    _email_scope_artifacts(rng, arts)
    arts.add_decoys(rng, 1, 2)
    return build_case(
        **_tag(f"syn_l3_{idx:02d}", 3),
        description="Email authentication excluded from MVP only; Production still needs it.",
        topic="email_auth",
        decisions=[
            DecisionSpec(d1, "로그인에 이메일 인증을 적용한다."),
            DecisionSpec(
                d2,
                "일정 문제로 이번 MVP에서는 이메일 인증을 제외한다.",
                scoped(release="mvp"),
                reason="일정 부족",
            ),
        ],
        relations=[relation("D01", "D02", RelationType.SCOPE_RESTRICT, scoped(release="mvp"))],
        artifacts=arts.artifacts,
        gold=arts.gold,
        tags=["scope_trap"],
    )


def level4(rng: random.Random, idx: int) -> EvalCase:
    (d1,) = _dates(rng, 1)
    due = _next_monday(d1)
    due_day = datetime.date.fromisoformat(due)
    everything_moves = rng.random() < 0.5
    arts = ArtifactSet()
    stale = (due_day - datetime.timedelta(days=rng.randint(2, 5))).isoformat()
    arts.add(ArtifactKind.SCHEDULE, "발표 일정", "INCONSISTENT", date=stale)
    for text in ("리허설 일정", "자료 제출 일정"):
        date = (due_day - datetime.timedelta(days=rng.randint(3, 8))).isoformat()
        arts.add(
            ArtifactKind.SCHEDULE,
            text,
            "INCONSISTENT" if everything_moves else "CONSISTENT",
            clarify=True,
            date=date,
        )
    if rng.random() < 0.5:
        moved = (due_day + datetime.timedelta(days=rng.randint(1, 3))).isoformat()
        arts.add(ArtifactKind.SCHEDULE, "발표 후 회고 일정", "CONSISTENT", date=moved)
    arts.add_decoys(rng, 1, 2)
    answer = (
        "발표, 리허설, 자료 제출 일정이 모두 함께 변경됩니다."
        if everything_moves
        else "발표 일정만 변경됩니다. 나머지는 그대로입니다."
    )
    return build_case(
        **_tag(f"syn_l4_{idx:02d}", 4),
        description=(
            "Presentation postponed to next week; "
            + ("all related dates move." if everything_moves else "only the presentation moves.")
        ),
        topic="presentation_schedule",
        decisions=[DecisionSpec(d1, "발표 일정을 다음 주로 연기한다.", due_date=due)],
        relations=[],
        artifacts=arts.artifacts,
        gold=arts.gold,
        user_facts=[UserFact(keywords=["발표 일정만", "리허설", "함께"], answer=answer)],
    )


def level5(rng: random.Random, idx: int) -> EvalCase:
    first = rng.choice(["React", "Vue"])
    second = _other(first)
    d1, d2, d3 = _dates(rng, 3)
    arts = ArtifactSet()
    arts.add(ArtifactKind.TASK, f"{second} 로그인 화면 개발", "INCONSISTENT", status="진행 중")
    arts.add(ArtifactKind.TEST, f"{second} 컴포넌트 렌더링 테스트", "INCONSISTENT")
    arts.add(ArtifactKind.TASK, f"{first} 대시보드 화면 개발", "CONSISTENT", status="진행 중")
    arts.add(ArtifactKind.REQUIREMENT, f"프론트엔드는 {first} 기반으로 구현한다.", "CONSISTENT")
    arts.add_decoys(rng, 1, 2)
    return build_case(
        **_tag(f"syn_l5_{idx:02d}", 5),
        description=f"{first} -> {second} -> 'return to the old way' (means {first}).",
        topic="frontend_framework",
        decisions=[
            DecisionSpec(d1, f"{first}를 사용한다."),
            DecisionSpec(d2, f"{second}로 변경한다."),
            DecisionSpec(d3, "기존 방식으로 돌아간다."),
        ],
        relations=[
            relation("D01", "D02", RelationType.SUPERSEDE),
            relation("D01", "D03", RelationType.SUPERSEDE),
            relation("D02", "D03", RelationType.SUPERSEDE),
        ],
        artifacts=arts.artifacts,
        gold=arts.gold,
        user_facts=[
            UserFact(
                keywords=["기존 방식", "뜻하나요", "처음"],
                answer=f"처음에 정한 {first}를 뜻합니다.",
            )
        ],
        tags=["vague_reference"],
    )


def level6(rng: random.Random, idx: int) -> EvalCase:
    d1, d2 = _dates(rng, 2)
    release = rng.choice(["MVP", "Production"])
    arts = ArtifactSet()
    unknown = unknown_scope("범위 열 없음")
    if release == "MVP":
        arts.add(
            ArtifactKind.TASK,
            "MVP 이메일 인증 개발 진행 중",
            "INCONSISTENT",
            unknown,
            clarify=True,
            status="진행 중",
        )
        if rng.random() < 0.5:
            arts.add(
                ArtifactKind.TEST,
                "MVP 이메일 인증 발송 테스트 필수",
                "INCONSISTENT",
                unknown,
                clarify=True,
            )
    else:
        arts.add(
            ArtifactKind.TASK,
            "Production 이메일 인증 구현 예정",
            "CONSISTENT",
            unknown,
            clarify=True,
            trap=True,
            status="예정",
        )
        if rng.random() < 0.5:
            arts.add(
                ArtifactKind.TEST,
                "Production 이메일 인증 재발송 테스트 필수",
                "CONSISTENT",
                unknown,
                clarify=True,
                trap=True,
            )
    arts.add(ArtifactKind.REQUIREMENT, "로그인에 이메일 인증을 적용한다.", "CONSISTENT", trap=True)
    arts.add_decoys(rng, 1, 2)
    return build_case(
        **_tag(f"syn_l6_{idx:02d}", 6),
        description=f"Artifact scope column missing (UNKNOWN); the artifact belongs to {release}.",
        topic="email_auth",
        decisions=[
            DecisionSpec(d1, "로그인에 이메일 인증을 적용한다."),
            DecisionSpec(
                d2,
                "일정 문제로 이번 MVP에서는 이메일 인증을 제외한다.",
                scoped(release="mvp"),
                reason="일정 부족",
            ),
        ],
        relations=[relation("D01", "D02", RelationType.SCOPE_RESTRICT, scoped(release="mvp"))],
        artifacts=arts.artifacts,
        gold=arts.gold,
        user_facts=[
            UserFact(
                keywords=["표시된 범위", "적용되나요"],
                answer=f"이 항목은 {release} 범위 항목입니다.",
            )
        ],
        tags=["unknown_scope"],
    )


def level7(rng: random.Random, idx: int) -> EvalCase:
    d1, d2 = _dates(rng, 2)
    arts = ArtifactSet()
    _email_scope_artifacts(rng, arts)
    unknown = unknown_scope("범위 열 없음")
    arts.add(
        ArtifactKind.TASK,
        "MVP 이메일 인증 연동 개발 진행 중",
        "INCONSISTENT",
        unknown,
        clarify=True,
        status="진행 중",
    )
    arts.add(
        ArtifactKind.TEST,
        "MVP 이메일 인증 만료 테스트 필수",
        "INCONSISTENT",
        unknown,
        clarify=True,
    )
    arts.add_decoys(rng, 1, 2)
    return build_case(
        **_tag(f"syn_l7_{idx:02d}", 7),
        description="Scope restriction with decoys plus unmapped-scope artifacts.",
        topic="email_auth",
        decisions=[
            DecisionSpec(d1, "로그인에 이메일 인증을 적용한다."),
            DecisionSpec(
                d2,
                "일정 문제로 이번 MVP에서는 이메일 인증을 제외한다.",
                scoped(release="mvp"),
                reason="일정 부족",
            ),
        ],
        relations=[relation("D01", "D02", RelationType.SCOPE_RESTRICT, scoped(release="mvp"))],
        artifacts=arts.artifacts,
        gold=arts.gold,
        user_facts=[
            UserFact(
                keywords=["표시된 범위", "적용되나요"],
                answer="이 항목은 MVP 범위 항목입니다.",
            )
        ],
        tags=["scope_trap", "unknown_scope"],
    )


TEMPLATES = [
    (level1, 9),
    (level2_swap, 5),
    (level2_cancel, 4),
    (level3, 10),
    (level4, 9),
    (level5, 9),
    (level6, 8),
    (level7, 8),
]


def generate_cases(seed: int = 7, scale: int = 1) -> list[EvalCase]:
    """Deterministic synthetic cases. scale multiplies the per-template counts."""
    rng = random.Random(seed)
    cases: list[EvalCase] = []
    for template, count in TEMPLATES:
        for i in range(1, count * scale + 1):
            cases.append(template(random.Random(rng.randint(0, 10**9)), i))
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic evaluation cases.")
    parser.add_argument("--out", default="data/eval/synthetic")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--scale", type=int, default=1)
    args = parser.parse_args()
    cases = generate_cases(args.seed, args.scale)
    save_cases(cases, Path(args.out) / "cases.json")
    print(f"wrote {len(cases)} cases to {Path(args.out) / 'cases.json'}")


if __name__ == "__main__":
    main()
