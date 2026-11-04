# BookSwap 대형 데모 정답표 (사람이 보는 용도, 점검 폴더에 넣지 마세요)

기준일은 2026-10-24 입니다. 6개월(2026-05-04부터 10-23까지) 동안 회의에서 결정이 바뀐 주제 29개와, 그 결정이 문서에 퍼졌는지(또는 안 퍼졌는지)를 정리했습니다.

- 정답이 있는 산출물 줄: 389개 (반영됨 196, 반영 안 됨 160, 과거 이력이라 확인 필요 33)
- 반영 안 됨은 '옛 값'을 그대로 적은 줄입니다. 이미 완료된 작업(DONE)에 옛 값이 적힌 줄은 지금 고칠 일이 아니라서 '확인 필요'로 분류했습니다.
- 같은 결정이 회의록과 메일에 두 번 나오는 경우가 많습니다. 한 건으로 합쳐져야 합니다.

## 주제별 결정 이력과 기대 결과

| 주제 | 결정 이력 (날짜: 값) | 최신 값 | 줄 수 | 반영됨 | 반영 안 됨 | 확인 필요 |
|---|---|---|---|---|---|---|
| auth | 2026-05-14: 이메일 인증 -> 2026-06-17: 학교 구글 SSO -> 2026-07-22: 카카오 로그인 -> 2026-07-23: 학번과 비밀번호 로그인 | 학번과 비밀번호 로그인 | 15 | 7 | 7 | 1 |
| frontend | 2026-05-06: React Native -> 2026-06-15: Flutter -> 2026-07-20: Kotlin Android 네이티브 | Kotlin Android 네이티브 | 14 | 10 | 4 | 0 |
| backend | 2026-05-25: FastAPI -> 2026-06-10: Spring Boot -> 2026-07-29: Django | Django | 15 | 10 | 4 | 1 |
| database | 2026-05-11: MySQL -> 2026-06-10: PostgreSQL -> 2026-07-02: MongoDB | MongoDB | 13 | 7 | 6 | 0 |
| payment | 2026-05-18: 토스페이먼츠 -> 2026-07-01: 카카오페이 -> 2026-07-06: 계좌이체 직접 거래 | 계좌이체 직접 거래 | 13 | 5 | 7 | 1 |
| chat | 2026-05-11: WebSocket -> 2026-06-15: 1:1 쪽지 폴링 -> 2026-08-03: Firebase 실시간 채팅 | Firebase 실시간 채팅 | 14 | 8 | 5 | 1 |
| push | 2026-05-18: FCM 푸시 -> 2026-06-10: 카카오 알림톡 -> 2026-08-03: 앱 내 알림함 | 앱 내 알림함 | 13 | 7 | 3 | 3 |
| image_limit | 2026-05-07: 5장 -> 2026-06-22: 3장 -> 2026-08-06: 10장 | 10장 | 11 | 7 | 3 | 1 |
| deploy | 2026-05-25: AWS EC2 -> 2026-06-10: AWS Lightsail -> 2026-08-03: Render -> 2026-09-07: 학교 서버 | 학교 서버 | 13 | 8 | 5 | 0 |
| search | 2026-05-06: 키워드 검색 -> 2026-06-22: 키워드와 초성 검색 -> 2026-06-29: 형태소 기반 검색 | 형태소 기반 검색 | 13 | 7 | 6 | 0 |
| admin_release | 2026-05-21: MVP에 포함 -> 2026-06-15: 정식 출시 이후로 연기 | 정식 출시 이후로 연기 | 12 | 2 | 7 | 3 |
| final_date | 2026-05-13: 11월 10일 -> 2026-07-06: 11월 17일 -> 2026-07-08: 11월 24일 | 11월 24일 | 13 | 7 | 6 | 0 |
| midterm_date | 2026-05-21: 10월 22일 -> 2026-06-15: 10월 29일 | 10월 29일 | 16 | 9 | 5 | 2 |
| color | 2026-05-25: 파란색 계열 -> 2026-06-29: 초록색 계열 -> 2026-08-03: 보라색 계열 | 보라색 계열 | 11 | 4 | 5 | 2 |
| ci | 2026-05-11: GitHub Actions -> 2026-06-15: Jenkins -> 2026-08-03: GitLab CI | GitLab CI | 12 | 4 | 6 | 2 |
| review | 2026-05-25: PR당 1명 승인 -> 2026-06-22: PR당 2명 승인 | PR당 2명 승인 | 13 | 5 | 6 | 2 |
| standup | 2026-05-18: 오전 10시 -> 2026-06-15: 오후 9시 -> 2026-08-03: 오전 9시 30분 | 오전 9시 30분 | 13 | 7 | 5 | 1 |
| storage | 2026-05-06: 서버 로컬 디스크 -> 2026-06-10: AWS S3 -> 2026-08-05: Cloudinary | Cloudinary | 13 | 5 | 7 | 1 |
| map | 2026-05-11: 카카오맵 API -> 2026-07-08: 네이버 지도 API -> 2026-08-10: 학교 건물 목록 선택 | 학교 건물 목록 선택 | 12 | 7 | 4 | 1 |
| retention | 2026-05-21: 탈퇴 후 30일 -> 2026-07-06: 탈퇴 후 7일 -> 2026-08-17: 탈퇴 즉시 | 탈퇴 즉시 | 13 | 6 | 6 | 1 |
| test_fw | 2026-05-14: pytest -> 2026-07-06: unittest -> 2026-08-03: Jest | Jest | 13 | 5 | 5 | 3 |
| design_tool | 2026-05-18: Figma -> 2026-07-01: Adobe XD | Adobe XD | 12 | 4 | 7 | 1 |
| app_name | 2026-05-13: BookSwap -> 2026-06-22: 북스왑 -> 2026-08-17: 교환서점 | 교환서점 | 13 | 8 | 4 | 1 |
| price | 2026-05-11: 정가의 50% 이하로 제한 -> 2026-06-22: 가격 자유 설정 | 가격 자유 설정 | 14 | 8 | 4 | 2 |
| report | 2026-05-06: 신고 3회 누적 시 숨김 -> 2026-06-29: 신고 5회 누적 시 숨김 | 신고 5회 누적 시 숨김 | 15 | 6 | 9 | 0 |
| onboarding | 2026-05-20: 약관 동의 후 학번 입력 -> 2026-07-08: 학번 입력 없이 바로 시작 | 학번 입력 없이 바로 시작 | 13 | 7 | 5 | 1 |
| log | 2026-05-14: Sentry -> 2026-07-06: 자체 로그 파일 -> 2026-07-15: CloudWatch | CloudWatch | 13 | 4 | 8 | 1 |
| rehearsal | 2026-05-25: 11월 5일 -> 2026-06-11: 11월 16일 | 11월 16일 | 17 | 9 | 8 | 0 |
| beta | 2026-05-07: 11월 3일 -> 2026-07-06: 11월 12일 | 11월 12일 | 17 | 13 | 3 | 1 |

(표의 날짜는 회의록에 실제로 기록된 결정 날짜입니다.)

## 반영 안 됨으로 나와야 하는 줄 예시 (주제별 최대 3개)

### auth (최신 값: 학번과 비밀번호 로그인)
- `requirements/requirements_auth.md`: 로그인은 카카오 로그인을 사용한다
- `requirements/requirements_legal.md`: 로그인은 학교 구글 SSO를 사용한다
- `requirements/requirements_slides_06.pptx`: 로그인은 이메일 인증을 사용한다
### frontend (최신 값: Kotlin Android 네이티브)
- `requirements/requirements_infra.md`: 모바일 앱은 Flutter로 개발한다
- `requirements/requirements_spec_01.xlsx`: 모바일 앱은 React Native로 개발한다
- `requirements/requirements_spec_04.xlsx`: 모바일 앱은 Flutter로 개발한다
### backend (최신 값: Django)
- `requirements/requirements_memo_05.docx`: API 서버는 Spring Boot로 개발한다
- `requirements/requirements_spec_10.xlsx`: API 서버는 Spring Boot로 개발한다
- `tasks/jira_export_08.csv`: Spring Boot 서버 프로젝트 구성
### database (최신 값: MongoDB)
- `requirements/requirements_memo_09.docx`: 데이터베이스는 MySQL을 사용한다
- `requirements/requirements_memo_10.docx`: 데이터베이스는 PostgreSQL을 사용한다
- `requirements/requirements_spec_03.xlsx`: 데이터베이스는 MySQL을 사용한다
### payment (최신 값: 계좌이체 직접 거래)
- `requirements/requirements_report.md`: 결제는 카카오페이로 처리한다
- `requirements/requirements_memo_02.docx`: 결제는 토스페이먼츠로 처리한다
- `requirements/requirements_memo_05.docx`: 결제는 토스페이먼츠로 처리한다
### chat (최신 값: Firebase 실시간 채팅)
- `requirements/requirements_home.md`: 채팅은 1:1 쪽지 폴링으로 제공한다
- `requirements/requirements_spec_08.xlsx`: 채팅은 1:1 쪽지 폴링으로 제공한다
- `tasks/tasks_sprint04.csv`: WebSocket 채팅 서버 구현
### push (최신 값: 앱 내 알림함)
- `requirements/requirements_notification.md`: 알림은 카카오 알림톡으로 보낸다
- `requirements/requirements_memo_06.docx`: 알림은 카카오 알림톡으로 보낸다
- `tests/test_report_03.pdf`: 카카오 알림톡 알림 수신 테스트 예정
### image_limit (최신 값: 10장)
- `requirements/requirements_payment.md`: 교재 사진은 최대 3장으로 업로드한다
- `requirements/requirements_spec_04.xlsx`: 교재 사진은 최대 5장으로 업로드한다
- `tests/test_plan_security.md`: 사진 3장 초과 업로드 차단 테스트 예정
### deploy (최신 값: 학교 서버)
- `requirements/requirements_home.md`: 서버는 AWS Lightsail에 배포한다
- `tasks/tasks_sprint11.csv`: Render 서버 세팅
- `tasks/tasks_sprint18.csv`: Render 서버 세팅
### search (최신 값: 형태소 기반 검색)
- `requirements/requirements_ops.md`: 교재 검색은 키워드 검색을 지원한다
- `requirements/requirements_spec_06.xlsx`: 교재 검색은 키워드와 초성 검색을 지원한다
- `tasks/tasks_sprint08.csv`: 키워드와 초성 검색 구현
### admin_release (최신 값: 정식 출시 이후로 연기)
- `requirements/requirements_spec_02.xlsx`: 관리자 페이지는 MVP에 포함한다
- `requirements/requirements_spec_06.xlsx`: 관리자 페이지는 MVP에 포함한다
- `requirements/requirements_spec_09.xlsx`: 관리자 페이지는 MVP에 포함한다
### final_date (최신 값: 11월 24일)
- `requirements/requirements_onboarding.md`: 최종 발표 일정은 11월 17일
- `requirements/requirements_spec_02.xlsx`: 최종 발표 일정은 11월 17일
- `requirements/requirements_slides_08.pptx`: 최종 발표 일정은 11월 10일
### midterm_date (최신 값: 10월 29일)
- `requirements/requirements_memo_08.docx`: 중간 점검 일정은 10월 22일
- `requirements/requirements_spec_05.xlsx`: 중간 점검 일정은 10월 22일
- `tasks/tasks_sprint07.csv`: 중간 점검 자료 작성 (10월 22일)
### color (최신 값: 보라색 계열)
- `requirements/requirements_notification.md`: 앱의 대표 색상은 초록색 계열로 한다
- `requirements/requirements_settings.md`: 앱의 대표 색상은 파란색 계열로 한다
- `tasks/tasks_sprint04.csv`: 초록색 계열 팔레트 정리
### ci (최신 값: GitLab CI)
- `requirements/requirements_memo_07.docx`: CI는 Jenkins를 사용한다
- `tasks/tasks_sprint02.csv`: Jenkins 파이프라인 구성
- `tasks/tasks_sprint23.csv`: GitHub Actions 파이프라인 구성
### review (최신 값: PR당 2명 승인)
- `requirements/requirements_memo_02.docx`: 코드 리뷰는 PR당 1명 승인을 원칙으로 한다
- `requirements/requirements_memo_06.docx`: 코드 리뷰는 PR당 1명 승인을 원칙으로 한다
- `requirements/requirements_slides_07.pptx`: 코드 리뷰는 PR당 1명 승인을 원칙으로 한다
### standup (최신 값: 오전 9시 30분)
- `requirements/requirements_memo_10.docx`: 스탠드업은 매일 오후 9시에 진행한다
- `requirements/requirements_spec_07.xlsx`: 스탠드업은 매일 오전 10시에 진행한다
- `tasks/tasks_sprint18.csv`: 스탠드업 알림 설정 (오전 10시)
### storage (최신 값: Cloudinary)
- `requirements/requirements_memo_07.docx`: 이미지는 AWS S3에 저장한다
- `requirements/requirements_memo_09.docx`: 이미지는 서버 로컬 디스크에 저장한다
- `requirements/requirements_spec_01.xlsx`: 이미지는 AWS S3에 저장한다
### map (최신 값: 학교 건물 목록 선택)
- `requirements/requirements_memo_10.docx`: 거래 장소는 카카오맵 API로 선택한다
- `tasks/jira_export_04.csv`: 카카오맵 API 거래 장소 화면 구현
- `tests/test_cases_02.csv`: 카카오맵 API 장소 선택 테스트 예정
### retention (최신 값: 탈퇴 즉시)
- `requirements/requirements_ops.md`: 개인정보 보관 기준: 탈퇴 후 7일
- `requirements/requirements_support.md`: 개인정보 보관 기준: 탈퇴 후 30일
- `tasks/tasks_sprint18.csv`: 개인정보 삭제 배치 구현 (탈퇴 후 30일)
### test_fw (최신 값: Jest)
- `requirements/requirements_admin.md`: 서버 테스트는 pytest로 작성한다
- `requirements/requirements_admin.md`: 서버 테스트는 unittest로 작성한다
- `requirements/requirements_settings.md`: 서버 테스트는 pytest로 작성한다
### design_tool (최신 값: Adobe XD)
- `requirements/requirements_spec_05.xlsx`: 디자인 시안은 Figma로 관리한다
- `requirements/requirements_slides_02.pptx`: 디자인 시안은 Figma로 관리한다
- `tasks/tasks_sprint04.csv`: Figma 시안 파일 정리
### app_name (최신 값: 교환서점)
- `requirements/requirements_auth.md`: 앱 이름은 BookSwap으로 표기한다
- `requirements/requirements_search.md`: 앱 이름은 BookSwap으로 표기한다
- `requirements/requirements_spec_02.xlsx`: 앱 이름은 BookSwap으로 표기한다
### price (최신 값: 가격 자유 설정)
- `requirements/requirements_payment.md`: 판매 가격 정책: 정가의 50% 이하로 제한
- `requirements/requirements_spec_03.xlsx`: 판매 가격 정책: 정가의 50% 이하로 제한
- `requirements/requirements_slides_05.pptx`: 판매 가격 정책: 정가의 50% 이하로 제한
### report (최신 값: 신고 5회 누적 시 숨김)
- `requirements/requirements_memo_05.docx`: 게시글 처리 기준: 신고 3회 누적 시 숨김
- `requirements/requirements_spec_03.xlsx`: 게시글 처리 기준: 신고 3회 누적 시 숨김
- `requirements/requirements_spec_08.xlsx`: 게시글 처리 기준: 신고 3회 누적 시 숨김
### onboarding (최신 값: 학번 입력 없이 바로 시작)
- `requirements/requirements_profile.md`: 가입 절차: 약관 동의 후 학번 입력
- `requirements/requirements_analytics.md`: 가입 절차: 약관 동의 후 학번 입력
- `tasks/tasks_sprint10.csv`: 가입 화면 구현 (약관 동의 후 학번 입력)
### log (최신 값: CloudWatch)
- `requirements/requirements_chat.md`: 오류 수집은 Sentry를 사용한다
- `requirements/requirements_payment.md`: 오류 수집은 Sentry를 사용한다
- `requirements/requirements_profile.md`: 오류 수집은 Sentry를 사용한다
### rehearsal (최신 값: 11월 16일)
- `requirements/requirements_spec_06.xlsx`: 리허설 일정은 11월 5일
- `tasks/jira_export_01.csv`: 리허설 준비 (11월 5일)
- `tasks/backlog_02.md`: 리허설 준비 (11월 5일)
### beta (최신 값: 11월 12일)
- `tasks/tasks_sprint22.csv`: 베타 테스터 모집 (11월 3일 시작)
- `tests/test_plan_security.md`: 베타 테스트 시나리오 (11월 3일 시작)
- `tests/test_plan_ux.md`: 베타 테스트 시나리오 (11월 3일 시작)

## 파일 구성
meetings(회의록 md, docx, xlsx, txt), emails(메일 eml: 회의 결과 공유 메일과 잡다한 메일), calendar(월별 ics와 학교 일정 ics), requirements(md, docx, xlsx, pptx), tasks(스프린트 csv, Jira 내보내기 csv, 백로그 md), tests(테스트 계획 md, 테스트 케이스 csv, QA 체크리스트, pdf), schedule(csv, md, xlsx).

전체 줄 단위 정답은 demo_big_answer_key.json 에 있습니다.
