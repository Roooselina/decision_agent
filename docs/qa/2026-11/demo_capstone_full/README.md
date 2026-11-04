# demo_capstone_full

BookSwap(캠퍼스 중고 교재 거래 앱)을 만드는 5인 캡스톤 팀의 프로젝트 폴더를 흉내 낸 데모입니다.
이야기 속 오늘은 2026-10-28(수)이고, 파일 기록은 2026-10-27에서 끝납니다. 등장인물과 주소는 모두 가상입니다.

## 들어 있는 것
- `demo_capstone_full/`: 점검할 폴더. 앱에서 이 폴더 하나만 추가하세요. 파일 120개.
- `demo_capstone_full_answer_key.json`, `demo_capstone_full_answer_key.md`: 정답표. 점검 폴더 밖에 두세요.
- `google_calendar_import.ics`: 내 구글 캘린더에 가져올 일정 (설정, 가져오기 및 내보내기).

## 메일 스레드
메일 다섯 스레드(17통)는 저장소의 `demo/mail_threads.json`에 있습니다.
`python tools/seed_mail_threads.py --me 내주소 --other 다른주소`로 계획을 먼저 보고, `--send`를 붙여 실제로 보냅니다.
앱 비밀번호가 없으면 `tools/mail_thread_kit.html`을 열어 손으로 보낼 수 있습니다.

## 캘린더를 연결할 때 주의
`google_calendar_import.ics`를 구글 캘린더에 가져오고 앱에서 구글 캘린더를 연결했다면,
설정의 제외 패턴에 `schedule/team_calendar.ics`를 추가하세요. 그렇지 않으면 같은 옛 일정(최종 발표 11/10, 리허설 11/9)이 두 번 지적됩니다.

## 기대 결과
- 문서만 읽으면 반영 안 됨 6건 (정답표 1~6번)
- 메일 T1, T2를 읽은 뒤 4건이 더해져 10건 (7~10번)
- 날짜 없는 회의 메모 하나는 날짜를 알 수 없다는 안내가 붙는 것이 정상입니다.
