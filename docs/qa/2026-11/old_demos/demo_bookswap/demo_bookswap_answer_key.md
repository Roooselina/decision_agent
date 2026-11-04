# BookSwap 데모 정답표 (사람이 보는 용도, 점검 폴더에 넣지 마세요)

기준일은 2026-10-24 입니다. 아래는 "제대로 된 판정기라면 이렇게 나와야 한다"는 기대값입니다.
데모 판정기(시연용 모드)는 규칙 기반이라 일부 주제만 잡습니다. 실제 모델로 돌렸을 때 이 표와 비교해 보세요.

## 결정 변경 10건 (회의에서 바뀌었고 문서에 퍼져야 하는 것)

| 번호 | 변경된 결정 | 날짜 | 반영 안 됨 (잡아야 함) | 반영됨 (잡으면 안 됨) |
|---|---|---|---|---|
| 1 | 이메일 인증 -> 학교 구글 SSO | 10-03 | requirements.md, user_stories.md, midterm_requirements.pptx, api_requirements_memo.docx, tasks.csv T02, jira BS-102, test_plan(이메일 인증 메일 수신), qa_checklist(이메일 인증 링크), test_report_draft.pdf, schedule.csv S06 | requirements_spec R-01, tasks.csv T01, schedule.csv S08, test_plan(SSO 로그인), jira BS-103 |
| 2 | React Native -> Flutter | 10-05 | tasks.csv T04, test_cases_ui TC01, midterm_requirements.pptx, jira BS-104(완료 이력이라 확인 필요) | requirements_frontend, tasks.csv T03, test_cases_ui TC02, jira BS-105 |
| 3 | MySQL -> PostgreSQL | 10-06 | tasks_backend(MySQL 연결 설정), test_cases_backend BT01, tasks.csv T15 (모두 완료 이력이라 확인 필요) | requirements_backend, tasks.csv T14, tasks_backend(PostgreSQL 연결), BT02 |
| 4 | 결제는 MVP 제외, 계좌이체 직접 거래 | 10-08 (docx와 메일 두 곳) | requirements.md, requirements_spec R-04, tasks.csv T05, schedule.csv S05, test_plan(결제 승인), qa_checklist(결제 취소), api_requirements_memo.docx, midterm_requirements.pptx, test_report_draft.pdf, tasks_design D04, backlog(토스 테스트 계정) | tasks.csv T06, test_plan(계좌이체 안내) |
| 5 | WebSocket 채팅 -> 1:1 쪽지(폴링) | 10-13 | requirements.md, requirements_spec R-05, user_stories.md, tasks.csv T07, test_plan(실시간 채팅 1초), backlog(WebSocket 재시도), jira BS-106 | tasks.csv T08, test_plan(쪽지 전송) |
| 6 | 관리자 페이지는 Production으로 | 10-12 | requirements_admin(신고 내역 처리), tasks.csv T11, schedule.csv S07, test_cases_backend BT04 | requirements_spec R-06 |
| 7 | 사진 업로드 5장 -> 3장 -> 다시 5장 (최신은 5장) | 10-16, 10-22 | requirements_frontend(3장), requirements_spec R-03, tasks.csv T12, test_plan(3장 초과), test_report_draft.pdf | tasks_backend(5장), api_requirements_memo.docx(5장), test_cases_ui TC05 |
| 8 | 초성 검색 추가 | 10-19 | requirements.md (검색 요구사항에 초성 언급 없음, 확인 필요) | requirements_spec R-02, tasks.csv T10, BT05, jira BS-107 |
| 9 | AWS EC2 -> AWS Lightsail | 10-20 | requirements_backend, requirements_spec N-02, midterm_requirements.pptx, tasks.csv T13 | (없음) |
| 10 | 최종 발표 11/10 -> 11/17, 리허설 -> 11/16 | 10-15 (교수님 메일) | milestones.md, presentation_schedule.xlsx, team_calendar.ics(최종 발표 11/10) | schedule.csv S01, S02 |

## 변경이 없어서 "문제없음"이 나와야 하는 것
- FCM 푸시 알림 (requirements, requirements_backend, tasks.csv T09 모두 일치)
- 대표 색상 파란색 계열 (requirements, requirements_frontend, tasks_design D02, test_cases_ui TC04 모두 일치)

## 일부러 넣은 함정
- 이메일 인증은 SSO로 바뀌었는데 일정표에는 "이메일 인증 QA"와 "SSO 로그인 QA"가 함께 있음
- 사진 업로드는 번복되어 "가장 최신 결정"을 따라가야 함 (3장은 이미 폐기된 값)
- 완료된 과거 작업(T15, BT01 등)은 지금 고칠 일이 아니라 "확인 필요"에 가까움
- 결정이 아닌 논의와 잡담("교재 가격 추천은 나중에", "디자인은 다음 주에 이야기")은 결정으로 잡히면 안 됨
- 날짜 없는 메모(notes/meeting_ideas.md)는 건너뛰고 경고만 나와야 함
- 같은 결정이 docx와 메일 두 곳에 있어도 한 건으로 합쳐져야 함

## 형식
md, docx, xlsx, csv, pptx, pdf, eml, ics, txt 총 9종, 45개 파일, 하위 폴더 5개(meetings, requirements, tasks, tests, schedule) + emails, notes.
