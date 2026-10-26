# 작업 진행 상황

## 완료한 것
### 단계 1. 속도 패치 적용
- speed_patch의 speed.diff를 git apply로 적용했습니다. files/를 통째로 복사하지 않은 이유: connectors/google.py와 microsoft.py에 기존 변경(clock_of로 일정 시각 읽기)이 있어 복사하면 사라졌습니다. diff 적용 결과는 이 두 파일만 files/와 다르고(기존 변경 유지), 나머지는 files/와 같습니다.
- desktop/providers.py는 CountingProvider의 threading.Lock 부분만 들어갔습니다(기존에 잠금이 없었고 충돌 없음).
- max_llm_calls_per_cycle 기본값 400 -> 3000.
- AppSettings에 llm_concurrency(8, 1~32), batch_size(8, 1이면 묶음 끔), adaptive_sampling(켜짐) 추가. ProjectEngine 생성 시(시작, 설정 변경 후 재구성 모두 해당) DEFAULT_SETTINGS의 concurrency, extract_batch, judge_batch, adaptive_min_samples에 반영. DECISION_AGENT_* 환경 변수가 있으면 환경 변수가 우선.
- 설정 "알림 및 데이터" 탭의 "실행" 카드에 "빠른 점검(묶음 판정)" 스위치(batch_size 8/1). 웹 API는 batch_size만 추가로 허용.
- 점검 진행 표시: "결정 추출 n/m", "판정 n/m". extract_decisions의 on_progress를 ProgressTracker.set_extract에 연결.
- 호출 한도로 끊기면 overall 상태 "partial", 홈 상단에 "일부만 점검했습니다. 호출 한도를 늘리거나 다시 점검하세요." 표시, "검사 완료"는 표시하지 않음.
- 시연용 판정기 문구를 "시연용 모드, 실제 검사가 아닙니다."로 맞춤.
- 이 작업 전의 커밋 안 된 변경(supervisor, debounce max wait)은 별도 커밋으로 먼저 보존했습니다.

### 단계 2. 바탕화면 도우미와 미니 패널
- desktop/helper.py: 위치 계산(SPI_GETWORKAREA, 주 모니터 작업 영역 기준 오른쪽 아래), 화면 밖 좌표 무시, 상태별 색, heartbeat(90초), 방해 금지 판정, 패널 위치(화면 안쪽으로 밀기), HelperWindow(pywebview 두 번째 창), Win32Metrics.
- static/helper.html, helper.js, helper.css: textContent만 사용, 기존 CSP nonce, 외부 리소스 없음. /helper 페이지와 정적 파일 허용 목록에 추가.
- 상태 갱신은 컨트롤러 listeners 구독으로 하고, 0.4초 안의 이벤트는 한 번으로 모아 창에 넣습니다(폴링 없음). 패널이 열려 있을 때만 15초마다 heartbeat 표시를 다시 계산합니다.
- 클릭하면 320x440 패널, 우클릭하면 메뉴(앱 열기, 빠른 점검, 지금 점검, 일시 중지, 도우미 숨기기, 종료). 바깥 클릭(blur)이나 Esc를 누르면 접힘.
- 드래그 위치는 settings.json의 helper_x, helper_y에 저장(전체 재점검 없이 저장하는 update_settings_light 경로). 도우미 켜기/끄기와 방해 금지 설정도 이 가벼운 경로를 탑니다.
- 앱 창 X는 기존대로 숨기기만 합니다. 첫 닫기 안내 문구를 "백그라운드에서 계속 확인합니다"로 바꿨습니다.
- 방해 금지 시간: 시스템 알림과 흔들림을 생략하고 배지와 받은 알림 목록만 갱신합니다.
- 시작 때 로그에 "tray icon registered"(실패하면 "tray icon not registered: ...")를 남깁니다.
- 지시와 다르게 한 것:
  - easy_drag 대신 원형 버튼에만 pywebview 드래그 영역을 지정했습니다. easy_drag를 켜면 패널의 버튼을 누를 때 조금만 움직여도 창이 끌려갑니다.
  - 투명 창(transparent) 대신 Win32 창 영역(SetWindowRgn)으로 창 모양을 원과 둥근 사각형으로 잘랐습니다. pywebview의 WinForms 투명 처리는 실제로 흰 사각형이 남았습니다. 원 가장자리에 계단 현상이 조금 있을 수 있습니다.
  - 드래그 위치는 창 이동 이벤트가 아니라 "드래그가 끝났을 때(mouseup)" 현재 창 좌표로 저장합니다. 이동 이벤트로 저장하면 Windows가 창을 만들면서 옮긴 좌표가 사용자 위치로 저장되는 문제를 실제로 확인했습니다.

## 남은 것
- 단계 3. 홈 캘린더와 데이터 표시 수정
- 단계 4. 마무리 보고

## 직접 확인할 것
- demo_big은 직접 푼 폴더를 찾지 못해, demo_big.zip을 임시 폴더에 풀고 별도 앱 데이터 폴더(DECISION_AGENT_HOME)로 점검했습니다. 실제 설정과 상태 DB는 건드리지 않았습니다.
  결과: 문서 351개, 결정 추출 253/253, 판정 4/4, 호출 851회, 약 9초, 로그의 ERROR/Traceback 0건, 점검 폴더 파일 변경 없음.
- 정답표(demo_big_answer_key.json/.md)는 zip 안에서 demo_big 폴더 밖에 있습니다. 직접 풀 때 demo_big 폴더만 등록하세요.
- 실제 모델로 묶음 판정을 켠 경우와 끈 경우의 품질 비교는 하지 않았습니다(패치 README의 한계와 같음).
- 설정에서 "빠른 점검(묶음 판정)"을 바꾸면 모든 폴더를 다시 점검합니다(다른 설정과 같은 동작).
- 도우미는 이 PC(2880x1800, 배율 200%, 단일 모니터)에서 격리된 앱 데이터 폴더로 실제 실행해 확인했습니다: 오른쪽 아래 표시, 작업 표시줄 항목 없음, 클릭 시 패널, 우클릭 메뉴, Esc로 접기, 드래그 후 재실행 시 위치 유지, 메뉴의 앱 열기, 메인 창 닫기 후 도우미와 프로세스 유지, 메뉴의 종료, 확인 필요 상태의 주황색과 배지.
- 직접 확인할 것: 다중 모니터와 100%, 125% 배율에서의 위치, 흔들림 애니메이션(새 이슈가 생길 때), 회전 링(점검 중), 다크 모드 패널.
- 불확실한 것: 첫 실행에서 클릭 한 번이 패널을 열지 못한 적이 있습니다. 이후 4번은 모두 열렸고 다시 재현하지 못했습니다.
- Windows 알림 토스트가 같은 오른쪽 아래에 떠서 도우미를 잠시 가립니다(Windows 동작).
- 저장된 좌표는 전체 가상 화면 사각형 안이면 받아들입니다. 크기가 다른 모니터 사이의 빈 영역은 걸러내지 못합니다.
- 방해 금지 시간이 끝난 뒤 생략한 알림을 모아서 보내는 기능은 없습니다(설정 도움말 문구를 실제 동작에 맞게 고쳤습니다).
