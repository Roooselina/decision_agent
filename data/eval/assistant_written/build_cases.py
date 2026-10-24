"""Builds data/eval/assistant_written/cases.json.

These ten cases were written by the AI assistant (Claude) as a first hand-made set. They are
not independent evidence: the same system that is evaluated was designed alongside them.
Teammates should add cases under data/eval/human_written/ in the same schema.

Run from the project root:  PYTHONPATH=src python3 data/eval/assistant_written/build_cases.py
"""

from __future__ import annotations

from pathlib import Path

from decision_agent.eval.builder import DecisionSpec, build_case, relation
from decision_agent.eval.dataset import (
    GoldArtifact,
    UserFact,
    make_artifact,
    save_cases,
    scoped,
    unknown_scope,
)
from decision_agent.models import ArtifactKind as K
from decision_agent.models import RelationType as R

AUTHOR = "assistant (Claude), not independent"
SRC = "assistant_written"


def g(artifact_id, label, affected=True, clar=False, trap=False, note=""):
    return GoldArtifact(
        artifact_id=artifact_id,
        label=label,
        affected=affected,
        needs_clarification=clar,
        scope_trap=trap,
        note=note,
    )


def case_01():
    arts = [
        make_artifact(
            "REQ_01", K.REQUIREMENT, "결제 모듈은 토스페이먼츠 API로 승인과 취소를 처리한다."
        ),
        make_artifact("TASK_01", K.TASK, "카카오페이 결제 연동 개발", status="진행 중"),
        make_artifact("TEST_01", K.TEST, "토스페이먼츠 결제 승인 테스트 케이스 작성"),
        make_artifact("TASK_02", K.TASK, "토스페이먼츠 웹훅 서명 검증 구현", status="예정"),
        make_artifact("TASK_03", K.TASK, "관리자 대시보드 차트 구현", status="진행 중"),
    ]
    gold = [
        g("REQ_01", "CONSISTENT"),
        g("TASK_01", "INCONSISTENT", note="uses a payment provider the team did not choose"),
        g("TEST_01", "CONSISTENT"),
        g("TASK_02", "CONSISTENT"),
        g("TASK_03", "CONSISTENT", affected=False),
    ]
    return build_case(
        case_id="aw_01_payment_provider",
        level=1,
        source=SRC,
        author=AUTHOR,
        description="One decision picks the payment provider; one task builds a different one.",
        topic="payment_provider",
        decisions=[DecisionSpec("2026-09-02", "결제는 토스페이먼츠를 사용한다.")],
        relations=[],
        artifacts=arts,
        gold=gold,
    )


def case_02():
    arts = [
        make_artifact("REQ_01", K.REQUIREMENT, "시스템은 PostgreSQL 14 이상을 지원한다."),
        make_artifact(
            "TASK_01", K.TASK, "MySQL 스키마 마이그레이션 스크립트 작성", status="진행 중"
        ),
        make_artifact("TASK_02", K.TASK, "PostgreSQL 인덱스 튜닝", status="예정"),
        make_artifact("TEST_01", K.TEST, "MySQL 연결 풀 부하 테스트"),
        make_artifact("TASK_03", K.TASK, "로그인 화면 문구 수정", status="진행 중"),
    ]
    gold = [
        g("REQ_01", "CONSISTENT"),
        g("TASK_01", "INCONSISTENT"),
        g("TASK_02", "CONSISTENT"),
        g("TEST_01", "INCONSISTENT"),
        g("TASK_03", "CONSISTENT", affected=False),
    ]
    return build_case(
        case_id="aw_02_db_supersede",
        level=2,
        source=SRC,
        author=AUTHOR,
        description="Database changed from MySQL to PostgreSQL; MySQL work remains.",
        topic="db_choice",
        decisions=[
            DecisionSpec("2026-09-03", "데이터베이스는 MySQL을 사용한다."),
            DecisionSpec("2026-09-12", "데이터베이스를 PostgreSQL로 변경한다."),
        ],
        relations=[relation("D01", "D02", R.SUPERSEDE)],
        artifacts=arts,
        gold=gold,
    )


def case_03():
    arts = [
        make_artifact("TASK_01", K.TASK, "푸시 알림 서버 구현", status="진행 중"),
        make_artifact("TASK_02", K.TASK, "푸시 알림 서버 구현 (삭제됨)", status="삭제"),
        make_artifact("TEST_01", K.TEST, "푸시 알림 수신 테스트"),
        make_artifact(
            "REQ_01", K.REQUIREMENT, "사용자는 설정 화면에서 푸시 알림 수신 여부를 선택할 수 있다."
        ),
        make_artifact("TASK_03", K.TASK, "이메일 알림 템플릿 정리", status="진행 중"),
    ]
    gold = [
        g("TASK_01", "INCONSISTENT"),
        g("TASK_02", "CONSISTENT", note="already removed"),
        g("TEST_01", "INCONSISTENT"),
        g("REQ_01", "INCONSISTENT"),
        g("TASK_03", "CONSISTENT", affected=False, note="a different notification channel"),
    ]
    return build_case(
        case_id="aw_03_push_cancel",
        level=2,
        source=SRC,
        author=AUTHOR,
        description="Push notification feature cancelled; task, test, and requirement remain.",
        topic="push_notification",
        decisions=[
            DecisionSpec("2026-09-05", "푸시 알림 기능을 개발한다."),
            DecisionSpec("2026-09-18", "푸시 알림 기능 개발은 취소한다."),
        ],
        relations=[relation("D01", "D02", R.CANCEL)],
        artifacts=arts,
        gold=gold,
    )


def case_04():
    mvp = scoped(release="mvp")
    prod = scoped(release="production")
    arts = [
        make_artifact("REQ_01", K.REQUIREMENT, "시스템은 다국어 지원을 제공한다."),
        make_artifact("TASK_01", K.TASK, "MVP 영어 번역 파일 작성", mvp, status="진행 중"),
        make_artifact(
            "TASK_02", K.TASK, "정식 출시용 영어, 일본어 번역 파일 작성", prod, status="예정"
        ),
        make_artifact("TEST_01", K.TEST, "MVP 한국어 화면 표시 테스트", mvp),
        make_artifact("TEST_02", K.TEST, "정식 출시 다국어 언어 전환 테스트", prod),
        make_artifact("TASK_03", K.TASK, "서버 로그 정리", status="진행 중"),
    ]
    gold = [
        g("REQ_01", "CONSISTENT", trap=True, note="global requirement still holds for Production"),
        g("TASK_01", "INCONSISTENT", note="MVP ships Korean only"),
        g("TASK_02", "CONSISTENT", trap=True),
        g("TEST_01", "CONSISTENT"),
        g("TEST_02", "CONSISTENT", trap=True),
        g("TASK_03", "CONSISTENT", affected=False),
    ]
    return build_case(
        case_id="aw_04_i18n_mvp_only",
        level=3,
        source=SRC,
        author=AUTHOR,
        description="Multi-language support restricted to Korean for the MVP only.",
        topic="i18n",
        decisions=[
            DecisionSpec("2026-09-04", "다국어 지원을 적용한다."),
            DecisionSpec("2026-09-14", "이번 MVP에서는 다국어 지원을 한국어만 제공한다.", mvp),
        ],
        relations=[relation("D01", "D02", R.SCOPE_RESTRICT, mvp)],
        artifacts=arts,
        gold=gold,
        tags=["scope_trap"],
    )


def case_05():
    beta = scoped(user_group="beta")
    arts = [
        make_artifact("TEST_01", K.TEST, "베타 테스터 유료 기능 차단 테스트", beta),
        make_artifact("TEST_02", K.TEST, "일반 사용자 유료 기능 차단 테스트"),
        make_artifact("TASK_01", K.TASK, "유료 기능 제한 모듈 개발", status="진행 중"),
        make_artifact(
            "TASK_02", K.TASK, "베타 테스터 계정에 유료 기능 제한 플래그 적용", beta, status="예정"
        ),
        make_artifact("TASK_03", K.TASK, "이용약관 문구 수정", status="진행 중"),
    ]
    gold = [
        g("TEST_01", "INCONSISTENT", note="expects blocking for a group that is exempt"),
        g("TEST_02", "CONSISTENT", trap=True),
        g("TASK_01", "CONSISTENT", trap=True),
        g("TASK_02", "INCONSISTENT"),
        g("TASK_03", "CONSISTENT", affected=False),
    ]
    return build_case(
        case_id="aw_05_beta_exemption",
        level=3,
        source=SRC,
        author=AUTHOR,
        description="Paid-feature limits apply to everyone except the beta tester group.",
        topic="paid_feature_limit",
        decisions=[
            DecisionSpec("2026-09-05", "유료 기능 제한을 전체 사용자에게 적용한다."),
            DecisionSpec(
                "2026-09-17", "베타 테스터 그룹에는 유료 기능 제한을 적용하지 않는다.", beta
            ),
        ],
        relations=[relation("D01", "D02", R.SCOPE_RESTRICT, beta)],
        artifacts=arts,
        gold=gold,
        tags=["scope_trap"],
    )


def case_06():
    arts = [
        make_artifact("SCHED_01", K.SCHEDULE, "정식 출시", date="2026-10-20"),
        make_artifact("SCHED_02", K.SCHEDULE, "출시 공지 메일 발송", date="2026-10-18"),
        make_artifact("SCHED_03", K.SCHEDULE, "정식 출시용 사용자 매뉴얼 배포", date="2026-11-12"),
        make_artifact("SCHED_04", K.SCHEDULE, "정식 출시 후 만족도 설문", date="2026-11-24"),
        make_artifact("TASK_01", K.TASK, "서버 인프라 비용 정산", status="예정"),
    ]
    gold = [
        g("SCHED_01", "INCONSISTENT", note="still on the old release date"),
        g(
            "SCHED_02",
            "INCONSISTENT",
            clar=True,
            note="semantic ambiguity: does the notice move with the release? (not a scope issue)",
        ),
        g("SCHED_03", "CONSISTENT", note="already after the new date"),
        g("SCHED_04", "CONSISTENT"),
        g("TASK_01", "CONSISTENT", affected=False),
    ]
    return build_case(
        case_id="aw_06_release_postpone",
        level=4,
        source=SRC,
        author=AUTHOR,
        description="Release date postponed; a dependent notice date may or may not move.",
        topic="release_date",
        decisions=[
            DecisionSpec("2026-09-08", "정식 출시일은 10월 20일로 한다.", due_date="2026-10-20"),
            DecisionSpec("2026-09-22", "정식 출시를 11월 10일로 연기한다.", due_date="2026-11-10"),
        ],
        relations=[relation("D01", "D02", R.POSTPONE)],
        artifacts=arts,
        gold=gold,
        user_facts=[
            UserFact(
                keywords=["공지", "함께", "같이", "다른", "관련", "연동"],
                answer="출시 공지 메일도 출시일에 맞춰 11월로 옮깁니다.",
            )
        ],
    )


def case_07():
    arts = [
        make_artifact("TASK_01", K.TASK, "GitHub Actions 워크플로 작성", status="진행 중"),
        make_artifact("TEST_01", K.TEST, "GitHub Actions 빌드 검증 시나리오"),
        make_artifact("TASK_02", K.TASK, "Jenkins 파이프라인 유지보수", status="진행 중"),
        make_artifact("REQ_01", K.REQUIREMENT, "모든 커밋은 CI 빌드를 통과해야 병합할 수 있다."),
        make_artifact("TASK_03", K.TASK, "DB 백업 스크립트 점검", status="예정"),
    ]
    gold = [
        g("TASK_01", "INCONSISTENT", note="built for the tool that was reverted away"),
        g("TEST_01", "INCONSISTENT"),
        g("TASK_02", "CONSISTENT"),
        g("REQ_01", "CONSISTENT", note="tool independent"),
        g("TASK_03", "CONSISTENT", affected=False),
    ]
    return build_case(
        case_id="aw_07_ci_revert",
        level=5,
        source=SRC,
        author=AUTHOR,
        description="CI tool switched, then reverted with a vague sentence that means Jenkins.",
        topic="ci_tool",
        decisions=[
            DecisionSpec("2026-09-02", "CI는 Jenkins를 사용한다."),
            DecisionSpec("2026-09-13", "CI를 GitHub Actions로 변경한다."),
            DecisionSpec("2026-09-25", "CI는 원래대로 되돌린다."),
        ],
        relations=[
            relation("D01", "D02", R.SUPERSEDE),
            relation("D02", "D03", R.SUPERSEDE),
        ],
        artifacts=arts,
        gold=gold,
        user_facts=[
            UserFact(
                keywords=["원래", "되돌", "가리키", "무엇"],
                answer="원래는 처음에 정한 Jenkins를 뜻합니다.",
            )
        ],
        tags=["vague_reference"],
    )


def case_08():
    unk = unknown_scope("일부 고객사 환경")
    arts = [
        make_artifact("REQ_01", K.REQUIREMENT, "로그는 90일간 보관한 뒤 삭제한다."),
        make_artifact("TEST_01", K.TEST, "90일 경과 로그 자동 삭제 테스트"),
        make_artifact(
            "TASK_01", K.TASK, "A사 온프레미스 로그 보관 기간 30일 설정 적용", unk, status="예정"
        ),
        make_artifact("TASK_02", K.TASK, "로그 수집 에이전트 설치 가이드 작성", status="진행 중"),
    ]
    gold = [
        g("REQ_01", "CONSISTENT", clar=True, note="general environment keeps 90 days"),
        g("TEST_01", "CONSISTENT", clar=True),
        g("TASK_01", "CONSISTENT", clar=True, note="matches the answer: only A on-premise uses 30"),
        g("TASK_02", "CONSISTENT", affected=False),
    ]
    return build_case(
        case_id="aw_08_log_retention_unknown",
        level=6,
        source=SRC,
        author=AUTHOR,
        description="A decision applies to 'some customer environments', which no alias maps.",
        topic="data_retention",
        decisions=[
            DecisionSpec("2026-09-07", "로그 보관 기간은 90일로 한다."),
            DecisionSpec(
                "2026-09-19", "일부 고객사 환경에서는 로그 보관 기간을 30일로 줄인다.", unk
            ),
        ],
        relations=[relation("D01", "D02", R.SCOPE_RESTRICT, unk)],
        artifacts=arts,
        gold=gold,
        user_facts=[
            UserFact(
                keywords=["일부 고객사", "어떤", "범위", "환경", "고객"],
                answer="30일은 A사 온프레미스 환경에만 적용합니다. 일반 환경은 계속 90일입니다.",
            )
        ],
        tags=["unknown_scope"],
    )


def case_09():
    mvp = scoped(release="mvp")
    prod = scoped(release="production")
    arts = [
        make_artifact("REQ_01", K.REQUIREMENT, "로그인은 SMS 인증을 사용한다."),
        make_artifact("TASK_01", K.TASK, "MVP SMS 발송 연동 개발", mvp, status="진행 중"),
        make_artifact("TASK_02", K.TASK, "MVP OTP 앱 인증 개발", mvp, status="예정"),
        make_artifact("TASK_03", K.TASK, "정식 출시 SMS 인증 구현", prod, status="예정"),
        make_artifact("TASK_04", K.TASK, "정식 출시 OTP 앱 인증 구현", prod, status="예정"),
        make_artifact("TASK_05", K.TASK, "회원 탈퇴 정책 문서화", status="진행 중"),
    ]
    gold = [
        g("REQ_01", "INCONSISTENT", note="stale: SMS was replaced by OTP globally"),
        g("TASK_01", "INCONSISTENT", clar=True),
        g("TASK_02", "CONSISTENT", clar=True),
        g("TASK_03", "INCONSISTENT"),
        g("TASK_04", "CONSISTENT"),
        g("TASK_05", "CONSISTENT", affected=False),
    ]
    return build_case(
        case_id="aw_09_auth_chain",
        level=7,
        source=SRC,
        author=AUTHOR,
        description=(
            "SMS auth excluded from MVP, then replaced by OTP globally; whether the OTP "
            "change reaches the MVP must be asked."
        ),
        topic="auth_method",
        decisions=[
            DecisionSpec("2026-09-01", "로그인은 SMS 인증을 사용한다."),
            DecisionSpec("2026-09-11", "이번 MVP에서는 SMS 인증을 제외한다.", mvp),
            DecisionSpec("2026-09-21", "인증 방식을 OTP 앱 인증으로 변경한다."),
        ],
        relations=[
            relation("D01", "D02", R.SCOPE_RESTRICT, mvp),
            relation("D01", "D03", R.SUPERSEDE),
        ],
        artifacts=arts,
        gold=gold,
        user_facts=[
            UserFact(
                keywords=["MVP", "OTP", "적용", "포함"],
                answer="OTP 앱 인증 전환은 MVP에도 적용합니다. MVP에서도 OTP로 만들어야 합니다.",
            )
        ],
        tags=["scope_trap"],
    )


def case_10():
    arts = [
        make_artifact("REQ_01", K.REQUIREMENT, "첨부 파일은 최대 10MB까지 업로드할 수 있다."),
        make_artifact("TASK_01", K.TASK, "10MB 초과 시 오류 메시지 표시 구현", status="진행 중"),
        make_artifact("TEST_01", K.TEST, "20MB 파일 업로드 성공 테스트"),
        make_artifact("TEST_02", K.TEST, "10MB 경계값 업로드 테스트"),
        make_artifact("TASK_02", K.TASK, "업로드 진행률 표시줄 구현", status="진행 중"),
        make_artifact("TASK_03", K.TASK, "파일 다운로드 속도 개선", status="진행 중"),
        make_artifact("TASK_04", K.TASK, "프로필 이미지 크롭 UI 개선", status="예정"),
    ]
    gold = [
        g("REQ_01", "CONSISTENT"),
        g("TASK_01", "CONSISTENT"),
        g("TEST_01", "INCONSISTENT", note="expects success above the limit"),
        g("TEST_02", "CONSISTENT"),
        g("TASK_02", "CONSISTENT", note="same feature, size independent"),
        g("TASK_03", "CONSISTENT", affected=False, note="shares the word file only"),
        g("TASK_04", "CONSISTENT", affected=False),
    ]
    return build_case(
        case_id="aw_10_upload_limit",
        level=1,
        source=SRC,
        author=AUTHOR,
        description="False-positive control: almost everything is consistent; one test is not.",
        topic="file_upload_limit",
        decisions=[DecisionSpec("2026-09-06", "파일 업로드 용량은 10MB로 제한한다.")],
        relations=[],
        artifacts=arts,
        gold=gold,
    )


def render_markdown(cases) -> str:
    """Readable version of the cases for reviewers who do not read JSON."""
    lines = ["# Assistant-written evaluation cases", ""]
    lines += [
        "Written by the AI assistant; not independent evidence. Generated from build_cases.py.",
        "Gold: label (CONSISTENT or INCONSISTENT after any needed answer), affected (on topic),",
        "clar (needs a user answer), trap (decoy for scope-ignoring systems).",
        "",
    ]
    for case in cases:
        lines += [f"## {case.case_id} (level {case.level})", "", case.description, ""]
        lines += ["Decisions:"]
        for d in case.gold_decisions:
            scope = d.scope.scope_kind.value
            if d.scope.scope_kind.value == "SCOPED":
                dims = {
                    k: v
                    for k, v in d.scope.model_dump().items()
                    if k in ("release", "module", "user_group", "environment") and v
                }
                scope += f" {dims}"
            lines.append(f"- {d.id} {d.date} [{scope}] {d.text}")
        if case.gold_relations:
            lines += ["", "Relations:"]
            for r in case.gold_relations:
                lines.append(f"- {r.old_id} -> {r.new_id}: {r.relation_type.value}")
        lines += [
            "",
            "Artifacts and gold:",
            "",
            "| id | text | scope | label | flags |",
            "| --- | --- | --- | --- | --- |",
        ]
        gold = case.gold_by_id()
        for a in case.artifacts:
            g = gold[a.id]
            flags = [
                name
                for name, on in (
                    ("off-topic", not g.affected),
                    ("clar", g.needs_clarification),
                    ("trap", g.scope_trap),
                )
                if on
            ]
            meta = f" ({a.metadata['date']})" if a.metadata.get("date") else ""
            lines.append(
                f"| {a.id} | {a.text}{meta} | {a.scope.scope_kind.value} | {g.label} | {', '.join(flags)} |"
            )
        if case.user_facts:
            lines += ["", "Scripted user answers:"]
            for fact in case.user_facts:
                lines.append(f"- keywords {fact.keywords}: {fact.answer}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    cases = [
        f()
        for f in (
            case_01,
            case_02,
            case_03,
            case_04,
            case_05,
            case_06,
            case_07,
            case_08,
            case_09,
            case_10,
        )
    ]
    folder = Path(__file__).resolve().parent
    save_cases(cases, folder / "cases.json")
    (folder / "CASES.md").write_text(render_markdown(cases) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases to {folder}")


if __name__ == "__main__":
    main()
