"""Answer key of the BookSwap capstone demo, written by hand.

Markers are substrings; tools/make_demo_capstone.py resolves each one to the exact meeting
item or artifact text the parser produces, and fails if a marker matches nothing or more
than one item. A line is stale when the latest decision of its topic has another value.
"""

# Decisions in the order they were made. "mail" names the thread message that carries a
# decision only the mail threads know about.
DECISIONS: list[dict] = [
    {"topic": "project", "value": "BookSwap", "date": "2026-09-21",
     "file": "ideas/topic_decision_0921.md", "marker": "최종 주제는 BookSwap"},
    {"topic": "frontend", "value": "React Native", "date": "2026-09-24",
     "file": "meetings/2026-09-24_킥오프_회의록.md", "marker": "프론트엔드는 React Native로 시작한다"},
    {"topic": "backend", "value": "Spring Boot, MySQL", "date": "2026-09-24",
     "file": "meetings/2026-09-24_킥오프_회의록.md", "marker": "백엔드는 Spring Boot"},
    {"topic": "login", "value": "학교 이메일 인증", "date": "2026-09-24",
     "file": "meetings/2026-09-24_킥오프_회의록.md", "marker": "로그인은 학교 이메일 인증 방식으로 한다"},
    {"topic": "payment", "value": "토스페이먼츠 앱 내 결제", "date": "2026-09-24",
     "file": "meetings/2026-09-24_킥오프_회의록.md", "marker": "결제는 토스페이먼츠로"},
    {"topic": "chat", "value": "WebSocket 실시간 채팅", "date": "2026-09-24",
     "file": "meetings/2026-09-24_킥오프_회의록.md", "marker": "WebSocket 실시간 채팅으로 만든다"},
    {"topic": "photos", "value": "5장", "date": "2026-09-24",
     "file": "meetings/2026-09-24_킥오프_회의록.md", "marker": "매물 사진은 최대 5장까지 올릴 수 있게 한다"},
    {"topic": "deploy", "value": "AWS EC2", "date": "2026-09-24",
     "file": "meetings/2026-09-24_킥오프_회의록.md", "marker": "서버는 AWS EC2 프리티어에 올린다"},
    {"topic": "push", "value": "FCM", "date": "2026-09-24",
     "file": "meetings/2026-09-24_킥오프_회의록.md", "marker": "FCM 푸시로 보낸다"},
    {"topic": "color", "value": "파란색", "date": "2026-09-25",
     "file": "meetings/2026-09-25_주간회의.md", "marker": "앱 메인 색상은 파란색 계열"},
    {"topic": "frontend", "value": "Flutter", "date": "2026-09-28",
     "file": "meetings/2026-09-28_회의록.md", "marker": "Flutter로 전환하기로 했다"},
    {"topic": "midterm", "value": "10/29", "date": "2026-09-30",
     "file": "meetings/2026-09-30_교수님_면담.md", "marker": "중간 점검 발표는 10월 29일"},
    {"topic": "final", "value": "발표 11/10, 리허설 11/9", "date": "2026-09-30",
     "file": "meetings/2026-09-30_교수님_면담.md", "marker": "최종 발표는 11월 10일"},
    {"topic": "login", "value": "학교 구글 SSO", "date": "2026-10-03",
     "file": "meetings/2026-10-03_회의록.docx", "marker": "학교 구글 계정 SSO로 변경한다"},
    {"topic": "payment", "value": "계좌이체 직접 거래", "date": "2026-10-08",
     "file": "meetings/2026-10-08_회의록.md", "marker": "계좌이체로 직접 거래하는 걸로 하자"},
    {"topic": "chat", "value": "1:1 쪽지 폴링", "date": "2026-10-13",
     "file": "meetings/2026-10-13_회의록.txt", "marker": "1:1 쪽지와 10초 간격 폴링 방식으로 변경한다"},
    {"topic": "photos", "value": "3장", "date": "2026-10-16",
     "file": "meetings/2026-10-16_주간회의.md", "marker": "최대 5장에서 최대 3장으로 줄인다"},
    {"topic": "deploy", "value": "AWS Lightsail", "date": "2026-10-20",
     "file": "meetings/2026-10-20_회의록.md", "marker": "AWS Lightsail(2GB, 서울 리전)로 옮긴다"},
    {"topic": "photos", "value": "5장", "date": "2026-10-22",
     "file": "meetings/2026-10-22_회의록.md", "marker": "다시 최대 5장으로 되돌린다"},
    {"topic": "final", "value": "발표 11/17, 리허설 11/16", "date": "2026-10-27", "mail": "T1/3",
     "marker": "최종 발표는 11월 17일 오후 2시"},
    {"topic": "deploy", "value": "Render 유료 플랜", "date": "2026-10-27", "mail": "T2/4",
     "marker": "배포는 Render 유료 플랜으로 하자"},
]

# Artifact lines that state a value of a decided topic. Lines not listed carry no value of
# any decided topic. "item" numbers the ten planted stale lines.
LINES: list[dict] = [
    # Found from the files alone.
    {"item": 1, "file": "requirements/requirements.md", "marker": "FR-02 회원가입", "topic": "login", "value": "학교 이메일 인증"},
    {"item": 2, "file": "tests/test_plan.md", "marker": "이메일 인증 메일 수신 확인", "topic": "login", "value": "학교 이메일 인증"},
    {"item": 3, "file": "tasks/tasks.csv", "marker": "토스페이먼츠 결제 연동", "topic": "payment", "value": "토스페이먼츠 앱 내 결제"},
    {"item": 4, "file": "schedule/schedule.csv", "marker": "결제 연동 QA", "topic": "payment", "value": "토스페이먼츠 앱 내 결제"},
    {"item": 5, "file": "requirements/user_stories.md", "marker": "US-07", "topic": "chat", "value": "WebSocket 실시간 채팅"},
    {"item": 6, "file": "requirements/requirements_frontend.md", "marker": "FE-05", "topic": "photos", "value": "3장"},
    # Found only after the mail threads are read.
    {"item": 7, "file": "schedule/milestones.md", "marker": "M5 최종 발표", "topic": "final", "value": "발표 11/10, 리허설 11/9"},
    {"item": 8, "file": "schedule/team_calendar.ics", "marker": "최종 발표", "exact": True, "topic": "final", "value": "발표 11/10, 리허설 11/9"},
    {"item": 9, "file": "schedule/team_calendar.ics", "marker": "리허설", "exact": True, "topic": "final", "value": "발표 11/10, 리허설 11/9"},
    {"item": 10, "file": "deploy/deploy_guide.md", "marker": "운영 서버는 AWS Lightsail", "topic": "deploy", "value": "AWS Lightsail"},
    # Traps: these agree with the latest decision and must not be reported.
    {"file": "requirements/requirements.md", "marker": "FR-04 매물당", "topic": "photos", "value": "5장"},
    {"file": "requirements/user_stories.md", "marker": "US-02", "topic": "photos", "value": "5장"},
    {"file": "tests/test_plan.md", "marker": "사진 5장 첨부", "topic": "photos", "value": "5장"},
    {"file": "tests/test_plan.md", "marker": "사진 6장째", "topic": "photos", "value": "5장"},
    {"file": "tasks/tasks.csv", "marker": "사진 최대 5장 첨부", "topic": "photos", "value": "5장"},
    {"file": "requirements/feature_overview.pptx", "marker": "교재 사진 최대 5장", "topic": "photos", "value": "5장"},
    {"file": "requirements/requirements.md", "marker": "FR-01 학교", "topic": "login", "value": "학교 구글 SSO"},
    {"file": "requirements/requirements_frontend.md", "marker": "FE-03", "topic": "login", "value": "학교 구글 SSO"},
    {"file": "requirements/user_stories.md", "marker": "US-01", "topic": "login", "value": "학교 구글 SSO"},
    {"file": "tests/test_plan.md", "marker": "학교 구글 계정으로 로그인", "topic": "login", "value": "학교 구글 SSO"},
    {"file": "requirements/requirements.md", "marker": "FR-08 앱 안에서", "topic": "payment", "value": "계좌이체 직접 거래"},
    {"file": "requirements/requirements.md", "marker": "FR-06 구매자와", "topic": "chat", "value": "1:1 쪽지 폴링"},
    {"file": "requirements/requirements_frontend.md", "marker": "FE-07", "topic": "chat", "value": "1:1 쪽지 폴링"},
    {"file": "requirements/user_stories.md", "marker": "US-04", "topic": "chat", "value": "1:1 쪽지 폴링"},
    {"file": "requirements/requirements_frontend.md", "marker": "FE-01", "topic": "frontend", "value": "Flutter"},
    {"file": "tasks/tasks.csv", "marker": "Flutter 프로젝트 생성", "topic": "frontend", "value": "Flutter"},
    {"file": "requirements/feature_overview.pptx", "marker": "Flutter, Android 우선", "topic": "frontend", "value": "Flutter"},
    {"file": "requirements/requirements.md", "marker": "FR-09 거래 요청", "topic": "push", "value": "FCM"},
    {"file": "requirements/requirements_frontend.md", "marker": "FE-02", "topic": "color", "value": "파란색"},
    {"file": "schedule/milestones.md", "marker": "M4 중간 점검", "topic": "midterm", "value": "10/29"},
    {"file": "schedule/team_calendar.ics", "marker": "중간 점검", "exact": True, "topic": "midterm", "value": "10/29"},
    {"file": "schedule/schedule.csv", "marker": "중간 점검 발표", "topic": "midterm", "value": "10/29"},
]

TRAPS = [
    "사진 개수는 5장에서 3장으로 줄었다가 10/22에 다시 5장으로 돌아왔다. 5장이라고 적힌 줄은 모두 맞고, 3장이라고 적힌 FE-05만 반영 안 됨이다.",
    "React Native에서 Flutter로 바꾼 결정은 모든 산출물에 반영되어 있다. 프론트엔드 관련 지적은 0건이어야 한다.",
    "FCM 푸시와 파란색 메인 색상은 한 번도 바뀌지 않았다. 지적이 나오면 안 된다.",
    "10/9 잡담 메모의 '교재 가격 추천은 나중에', '점심 뭐 먹지'는 결정이 아니다.",
    "10/23 회의와 메일 T4의 메뉴 A/B는 의견만 오갔고 정해진 것이 없다.",
    "메일 T3의 두 번째 메일에 인용된 '토스페이먼츠로 앱 안에서 처리' 문장은 예전 메일을 인용한 것이라 결정이 아니다.",
    "메일 T5의 마감(10/28 밤 12시)과 중간 점검(10/29)은 일정 힌트일 뿐 결정이 바뀐 것이 아니다.",
    "날짜 없는 회의 메모는 날짜를 알 수 없어 건너뛰거나(문서만 읽을 때) 파일 날짜를 쓴다는 안내가 붙는다.",
]
