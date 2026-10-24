# Assistant-written evaluation cases

Written by the AI assistant; not independent evidence. Generated from build_cases.py.
Gold: label (CONSISTENT or INCONSISTENT after any needed answer), affected (on topic),
clar (needs a user answer), trap (decoy for scope-ignoring systems).

## aw_01_payment_provider (level 1)

One decision picks the payment provider; one task builds a different one.

Decisions:
- D01 2026-09-02 [GLOBAL] 결제는 토스페이먼츠를 사용한다.

Artifacts and gold:

| id | text | scope | label | flags |
| --- | --- | --- | --- | --- |
| REQ_01 | 결제 모듈은 토스페이먼츠 API로 승인과 취소를 처리한다. | GLOBAL | CONSISTENT |  |
| TASK_01 | 카카오페이 결제 연동 개발 | GLOBAL | INCONSISTENT |  |
| TEST_01 | 토스페이먼츠 결제 승인 테스트 케이스 작성 | GLOBAL | CONSISTENT |  |
| TASK_02 | 토스페이먼츠 웹훅 서명 검증 구현 | GLOBAL | CONSISTENT |  |
| TASK_03 | 관리자 대시보드 차트 구현 | GLOBAL | CONSISTENT | off-topic |

## aw_02_db_supersede (level 2)

Database changed from MySQL to PostgreSQL; MySQL work remains.

Decisions:
- D01 2026-09-03 [GLOBAL] 데이터베이스는 MySQL을 사용한다.
- D02 2026-09-12 [GLOBAL] 데이터베이스를 PostgreSQL로 변경한다.

Relations:
- D01 -> D02: SUPERSEDE

Artifacts and gold:

| id | text | scope | label | flags |
| --- | --- | --- | --- | --- |
| REQ_01 | 시스템은 PostgreSQL 14 이상을 지원한다. | GLOBAL | CONSISTENT |  |
| TASK_01 | MySQL 스키마 마이그레이션 스크립트 작성 | GLOBAL | INCONSISTENT |  |
| TASK_02 | PostgreSQL 인덱스 튜닝 | GLOBAL | CONSISTENT |  |
| TEST_01 | MySQL 연결 풀 부하 테스트 | GLOBAL | INCONSISTENT |  |
| TASK_03 | 로그인 화면 문구 수정 | GLOBAL | CONSISTENT | off-topic |

## aw_03_push_cancel (level 2)

Push notification feature cancelled; task, test, and requirement remain.

Decisions:
- D01 2026-09-05 [GLOBAL] 푸시 알림 기능을 개발한다.
- D02 2026-09-18 [GLOBAL] 푸시 알림 기능 개발은 취소한다.

Relations:
- D01 -> D02: CANCEL

Artifacts and gold:

| id | text | scope | label | flags |
| --- | --- | --- | --- | --- |
| TASK_01 | 푸시 알림 서버 구현 | GLOBAL | INCONSISTENT |  |
| TASK_02 | 푸시 알림 서버 구현 (삭제됨) | GLOBAL | CONSISTENT |  |
| TEST_01 | 푸시 알림 수신 테스트 | GLOBAL | INCONSISTENT |  |
| REQ_01 | 사용자는 설정 화면에서 푸시 알림 수신 여부를 선택할 수 있다. | GLOBAL | INCONSISTENT |  |
| TASK_03 | 이메일 알림 템플릿 정리 | GLOBAL | CONSISTENT | off-topic |

## aw_04_i18n_mvp_only (level 3)

Multi-language support restricted to Korean for the MVP only.

Decisions:
- D01 2026-09-04 [GLOBAL] 다국어 지원을 적용한다.
- D02 2026-09-14 [SCOPED {'release': 'mvp'}] 이번 MVP에서는 다국어 지원을 한국어만 제공한다.

Relations:
- D01 -> D02: SCOPE_RESTRICT

Artifacts and gold:

| id | text | scope | label | flags |
| --- | --- | --- | --- | --- |
| REQ_01 | 시스템은 다국어 지원을 제공한다. | GLOBAL | CONSISTENT | trap |
| TASK_01 | MVP 영어 번역 파일 작성 | SCOPED | INCONSISTENT |  |
| TASK_02 | 정식 출시용 영어, 일본어 번역 파일 작성 | SCOPED | CONSISTENT | trap |
| TEST_01 | MVP 한국어 화면 표시 테스트 | SCOPED | CONSISTENT |  |
| TEST_02 | 정식 출시 다국어 언어 전환 테스트 | SCOPED | CONSISTENT | trap |
| TASK_03 | 서버 로그 정리 | GLOBAL | CONSISTENT | off-topic |

## aw_05_beta_exemption (level 3)

Paid-feature limits apply to everyone except the beta tester group.

Decisions:
- D01 2026-09-05 [GLOBAL] 유료 기능 제한을 전체 사용자에게 적용한다.
- D02 2026-09-17 [SCOPED {'user_group': 'beta'}] 베타 테스터 그룹에는 유료 기능 제한을 적용하지 않는다.

Relations:
- D01 -> D02: SCOPE_RESTRICT

Artifacts and gold:

| id | text | scope | label | flags |
| --- | --- | --- | --- | --- |
| TEST_01 | 베타 테스터 유료 기능 차단 테스트 | SCOPED | INCONSISTENT |  |
| TEST_02 | 일반 사용자 유료 기능 차단 테스트 | GLOBAL | CONSISTENT | trap |
| TASK_01 | 유료 기능 제한 모듈 개발 | GLOBAL | CONSISTENT | trap |
| TASK_02 | 베타 테스터 계정에 유료 기능 제한 플래그 적용 | SCOPED | INCONSISTENT |  |
| TASK_03 | 이용약관 문구 수정 | GLOBAL | CONSISTENT | off-topic |

## aw_06_release_postpone (level 4)

Release date postponed; a dependent notice date may or may not move.

Decisions:
- D01 2026-09-08 [GLOBAL] 정식 출시일은 10월 20일로 한다.
- D02 2026-09-22 [GLOBAL] 정식 출시를 11월 10일로 연기한다.

Relations:
- D01 -> D02: POSTPONE

Artifacts and gold:

| id | text | scope | label | flags |
| --- | --- | --- | --- | --- |
| SCHED_01 | 정식 출시 (2026-10-20) | GLOBAL | INCONSISTENT |  |
| SCHED_02 | 출시 공지 메일 발송 (2026-10-18) | GLOBAL | INCONSISTENT | clar |
| SCHED_03 | 정식 출시용 사용자 매뉴얼 배포 (2026-11-12) | GLOBAL | CONSISTENT |  |
| SCHED_04 | 정식 출시 후 만족도 설문 (2026-11-24) | GLOBAL | CONSISTENT |  |
| TASK_01 | 서버 인프라 비용 정산 | GLOBAL | CONSISTENT | off-topic |

Scripted user answers:
- keywords ['공지', '함께', '같이', '다른', '관련', '연동']: 출시 공지 메일도 출시일에 맞춰 11월로 옮깁니다.

## aw_07_ci_revert (level 5)

CI tool switched, then reverted with a vague sentence that means Jenkins.

Decisions:
- D01 2026-09-02 [GLOBAL] CI는 Jenkins를 사용한다.
- D02 2026-09-13 [GLOBAL] CI를 GitHub Actions로 변경한다.
- D03 2026-09-25 [GLOBAL] CI는 원래대로 되돌린다.

Relations:
- D01 -> D02: SUPERSEDE
- D02 -> D03: SUPERSEDE

Artifacts and gold:

| id | text | scope | label | flags |
| --- | --- | --- | --- | --- |
| TASK_01 | GitHub Actions 워크플로 작성 | GLOBAL | INCONSISTENT |  |
| TEST_01 | GitHub Actions 빌드 검증 시나리오 | GLOBAL | INCONSISTENT |  |
| TASK_02 | Jenkins 파이프라인 유지보수 | GLOBAL | CONSISTENT |  |
| REQ_01 | 모든 커밋은 CI 빌드를 통과해야 병합할 수 있다. | GLOBAL | CONSISTENT |  |
| TASK_03 | DB 백업 스크립트 점검 | GLOBAL | CONSISTENT | off-topic |

Scripted user answers:
- keywords ['원래', '되돌', '가리키', '무엇']: 원래는 처음에 정한 Jenkins를 뜻합니다.

## aw_08_log_retention_unknown (level 6)

A decision applies to 'some customer environments', which no alias maps.

Decisions:
- D01 2026-09-07 [GLOBAL] 로그 보관 기간은 90일로 한다.
- D02 2026-09-19 [UNKNOWN] 일부 고객사 환경에서는 로그 보관 기간을 30일로 줄인다.

Relations:
- D01 -> D02: SCOPE_RESTRICT

Artifacts and gold:

| id | text | scope | label | flags |
| --- | --- | --- | --- | --- |
| REQ_01 | 로그는 90일간 보관한 뒤 삭제한다. | GLOBAL | CONSISTENT | clar |
| TEST_01 | 90일 경과 로그 자동 삭제 테스트 | GLOBAL | CONSISTENT | clar |
| TASK_01 | A사 온프레미스 로그 보관 기간 30일 설정 적용 | UNKNOWN | CONSISTENT | clar |
| TASK_02 | 로그 수집 에이전트 설치 가이드 작성 | GLOBAL | CONSISTENT | off-topic |

Scripted user answers:
- keywords ['일부 고객사', '어떤', '범위', '환경', '고객']: 30일은 A사 온프레미스 환경에만 적용합니다. 일반 환경은 계속 90일입니다.

## aw_09_auth_chain (level 7)

SMS auth excluded from MVP, then replaced by OTP globally; whether the OTP change reaches the MVP must be asked.

Decisions:
- D01 2026-09-01 [GLOBAL] 로그인은 SMS 인증을 사용한다.
- D02 2026-09-11 [SCOPED {'release': 'mvp'}] 이번 MVP에서는 SMS 인증을 제외한다.
- D03 2026-09-21 [GLOBAL] 인증 방식을 OTP 앱 인증으로 변경한다.

Relations:
- D01 -> D02: SCOPE_RESTRICT
- D01 -> D03: SUPERSEDE

Artifacts and gold:

| id | text | scope | label | flags |
| --- | --- | --- | --- | --- |
| REQ_01 | 로그인은 SMS 인증을 사용한다. | GLOBAL | INCONSISTENT |  |
| TASK_01 | MVP SMS 발송 연동 개발 | SCOPED | INCONSISTENT | clar |
| TASK_02 | MVP OTP 앱 인증 개발 | SCOPED | CONSISTENT | clar |
| TASK_03 | 정식 출시 SMS 인증 구현 | SCOPED | INCONSISTENT |  |
| TASK_04 | 정식 출시 OTP 앱 인증 구현 | SCOPED | CONSISTENT |  |
| TASK_05 | 회원 탈퇴 정책 문서화 | GLOBAL | CONSISTENT | off-topic |

Scripted user answers:
- keywords ['MVP', 'OTP', '적용', '포함']: OTP 앱 인증 전환은 MVP에도 적용합니다. MVP에서도 OTP로 만들어야 합니다.

## aw_10_upload_limit (level 1)

False-positive control: almost everything is consistent; one test is not.

Decisions:
- D01 2026-09-06 [GLOBAL] 파일 업로드 용량은 10MB로 제한한다.

Artifacts and gold:

| id | text | scope | label | flags |
| --- | --- | --- | --- | --- |
| REQ_01 | 첨부 파일은 최대 10MB까지 업로드할 수 있다. | GLOBAL | CONSISTENT |  |
| TASK_01 | 10MB 초과 시 오류 메시지 표시 구현 | GLOBAL | CONSISTENT |  |
| TEST_01 | 20MB 파일 업로드 성공 테스트 | GLOBAL | INCONSISTENT |  |
| TEST_02 | 10MB 경계값 업로드 테스트 | GLOBAL | CONSISTENT |  |
| TASK_02 | 업로드 진행률 표시줄 구현 | GLOBAL | CONSISTENT |  |
| TASK_03 | 파일 다운로드 속도 개선 | GLOBAL | CONSISTENT | off-topic |
| TASK_04 | 프로필 이미지 크롭 UI 개선 | GLOBAL | CONSISTENT | off-topic |

