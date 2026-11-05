# 평가 하네스 (2단계)

> 루트 README에서 분리한 상세 문서입니다. 원문 내용은 바꾸지 않고 위치만 옮겼습니다.

코드는 `src/decision_agent/eval/`, 데이터는 `data/eval/`에 있습니다.

## 케이스

| 폴더 | 내용 | 작성자 | 독립 증거 여부 |
| --- | --- | --- | --- |
| `synthetic/` | 템플릿 기반 62개 (`python -m decision_agent.eval.generator`, seed 7) | 생성기 | 아님 |
| `assistant_written/` | 직접 설계한 10개 (`build_cases.py`로 생성) | AI 어시스턴트 | 아님 |
| `human_written/` | 팀원이 작성할 폴더. `TEMPLATE.json.example` 참고 | 사람 | 해당 |
| `real/` | 익명화한 실제 문서 | 사람 | 해당 |

레벨: 1 단일 결정, 2 전체 교체/취소, 3 범위 제한(scope 함정 포함), 4 일정 연기, 5 이전 결정 참조(이력 필요), 6 UNKNOWN scope(질문 필요), 7 복합.
각 케이스는 회의 문장, 산출물, 정답 결정/관계(gold), 산출물별 정답(`label`, `affected`, `needs_clarification`, `scope_trap`), 질문에 대한 시뮬레이션 사용자의 답(`user_facts`)을 가집니다.
`tags`: `scope_trap`, `retrieval_hard`(검색이 못 찾도록 만든 케이스), `vague_reference`, `unknown_scope`.

## 비교 방법

| 이름 | 설명 |
| --- | --- |
| B1_tfidf | TF-IDF 상위 후보를 전부 불일치로 표시 (검색만) |
| B2_rag | 검색 후 가장 비슷한 결정 하나와 LLM 판정 1회. scope와 이력 없음. 임베딩 대신 희소 TF-IDF를 써서 오프라인으로 재현 가능 |
| B3_long_context | 원문 결정 로그 전체를 넣고 산출물마다 LLM 판정 1회 |
| P | 고정 순서 Pipeline, 질문 없음 |
| M2 | Pipeline + 확인 질문 |
| M1 | Agent, 질문 없음 |
| M1c | Agent + 확인 질문 |
| A_scope_agnostic | M1c에서 모든 scope를 GLOBAL로 지운 저장소를 사용 (scope 제거 실험) |

## 재현

```bash
PYTHONPATH=src python3 -m decision_agent.eval.generator          # 합성 케이스 재생성
PYTHONPATH=src python3 data/eval/assistant_written/build_cases.py
PYTHONPATH=src python3 -m decision_agent.eval.runner \
  --sources synthetic,assistant_written --methods all \
  --oracle-stage none,decisions,full --repeats 1 --out out/eval_run
```

옵션: `--provider mock|anthropic`, `--repeats N`, `--n-samples N`(자기일관성 표본 수), `--levels 3,6`, `--limit N`, `--bootstrap N`.
출력: `report.md`, `rows.csv`(산출물 단위), `runs.csv`(실행 단위), `summary.csv`, `errors.json`, 그래프 PNG. 기존 결과로 리포트만 다시 만들려면 `python -m decision_agent.eval.report --run out/eval_run`.

실험: (1) 방법별 주 비교(F1과 95% 부트스트랩 구간, 케이스 단위 재표집), (2) 레벨과 태그별, (3) scope 제거 실험, (4) 확인 질문 효과, (5) Agent 경로 분석과 비용, (6) oracle 단계별 오류 귀속(`none`은 전부 LLM, `decisions`는 정답 결정 사용, `full`은 정답 결정과 관계 사용).

지표: INCONSISTENT를 양성으로 한 정밀도/재현율/F1(UNCERTAIN은 양성 아님), 영향 후보 재현율, 3-way 정확도, UNCERTAIN 비율, 확신 오답률, scope 함정 오탐률, 질문 수, 불필요 질문률, 질문 해소율, ECE(신뢰도는 표본 일치율이며 보정된 확률이 아님), LLM 호출 수, 시간.

## 읽는 법과 주의

- MockProvider 결과는 배선 확인용입니다. Mock은 데모 어휘만 알아서 합성 케이스에서는 높게, 새 어휘의 `assistant_written` 케이스에서는 아무것도 못 찾아 0에 가깝게 나옵니다. 둘 다 성능 근거가 아닙니다. 리포트 첫머리에 경고 문구가 자동으로 붙습니다.
- Mock에서는 scope 제거 실험(3)이 차이를 못 만듭니다. Mock 판정기가 산출물 문장 속 "MVP"를 직접 읽기 때문입니다.
- `GoldOracleProvider`(`eval/oracle_provider.py`)는 정답을 아는 테스트용 대역입니다. 모든 케이스의 정답이 실제 resolver, 질문 게이트, 근거 흐름을 거쳐 도달 가능한지(케이스 설계 오류 점검)와 하네스 배선을 검증하며, 모델 성능과는 무관합니다.
- 합성 케이스와 어시스턴트가 쓴 케이스는 시스템과 같은 쪽에서 설계했으므로 독립 증거가 아닙니다. 발표용 주장에는 `human_written`과 `real`이 필요합니다.
- 시뮬레이션 사용자는 대본대로만 답하며 망설이거나 틀리게 답하지 않습니다. 질문이 대본과 안 맞으면 "잘 모르겠습니다"를 돌려줍니다.
- "불필요 질문"은 정답 기준입니다. 이력을 읽으면 풀리는 레벨 5에서 Pipeline(M2)이 묻는 것도 불필요 질문으로 셉니다.
- 후보 검색(문자 TF-IDF)이 못 찾은 산출물은 판정 기회가 없습니다. `retrieval_hard` 태그가 이 한계를 따로 보여 줍니다.
- 실제 모델 결과는 `ANTHROPIC_API_KEY`와 `DECISION_AGENT_MODEL`을 설정하고 `--provider anthropic`으로 실행해야 얻습니다. 이 저장소에서는 실행해 보지 않았습니다.
