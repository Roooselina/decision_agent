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

## 남은 것
- 단계 2. 바탕화면 도우미와 미니 패널
- 단계 3. 홈 캘린더와 데이터 표시 수정
- 단계 4. 마무리 보고

## 직접 확인할 것
- demo_big은 직접 푼 폴더를 찾지 못해, demo_big.zip을 임시 폴더에 풀고 별도 앱 데이터 폴더(DECISION_AGENT_HOME)로 점검했습니다. 실제 설정과 상태 DB는 건드리지 않았습니다.
  결과: 문서 351개, 결정 추출 253/253, 판정 4/4, 호출 851회, 약 9초, 로그의 ERROR/Traceback 0건, 점검 폴더 파일 변경 없음.
- 정답표(demo_big_answer_key.json/.md)는 zip 안에서 demo_big 폴더 밖에 있습니다. 직접 풀 때 demo_big 폴더만 등록하세요.
- 실제 모델로 묶음 판정을 켠 경우와 끈 경우의 품질 비교는 하지 않았습니다(패치 README의 한계와 같음).
- 설정에서 "빠른 점검(묶음 판정)"을 바꾸면 모든 폴더를 다시 점검합니다(다른 설정과 같은 동작).
