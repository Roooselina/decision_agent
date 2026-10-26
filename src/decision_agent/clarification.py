from __future__ import annotations

from pydantic import BaseModel

from decision_agent.config import DEFAULT_SETTINGS, Settings
from decision_agent.llm.base import LLMProvider
from decision_agent.models import Question

QUESTION_SYSTEM = """TASK:QUESTION_GENERATE
너는 불확실한 판단을 해결하기 위해 사용자에게 던질 확인 질문 후보를 만든다.
입력은 현재 결정과 판단이 불확실한 산출물 목록이다.

규칙:
1. 질문 후보를 최대 3개 만든다. 각 질문은 판단을 막고 있는 부족한 정보 하나만 묻는다.
2. 예/아니오 또는 짧은 선택지로 답할 수 있게 쓴다. 한국어로 쓴다.
3. "더 설명해 주세요" 같은 열린 질문은 만들지 않는다.
4. 서로 다른 정보를 묻는 후보로 만든다(범위, 대상 항목, 적용 시점 등).

출력: {"questions": ["질문1", "질문2", "질문3"]}
"""

GAIN_SYSTEM = """TASK:QUESTION_GAIN
주어진 질문에 사용자가 ASSUMED_ANSWER로 답했다고 가정하자.
그 답변이 CONTEXT의 불확실한 판단들을 얼마나 해소하는지 0에서 1 사이 gain으로 추정한다.
답변이 판단에 거의 도움이 되지 않으면 0에 가깝게, 대부분 해소하면 1에 가깝게 쓴다.
출력: {"gain": 0.0}
"""

GAIN_BATCH_SYSTEM = """TASK:QUESTION_GAIN_BATCH
각 질문에 대해 사용자가 "예", "아니오", "일부만" 중 하나로 답했다고 가정하자.
각 가정 답변이 CONTEXT의 불확실한 판단들을 얼마나 해소하는지 0에서 1 사이 gain으로 추정한다.
답변이 판단에 거의 도움이 되지 않으면 0에 가깝게, 대부분 해소하면 1에 가깝게 쓴다.
모든 질문 번호에 대해 결과를 하나씩 낸다. 번호를 빼거나 만들지 않는다.
출력: {"gains": [{"question": 1, "yes": 0.0, "no": 0.0, "partial": 0.0}]}
"""

ASSUMED_ANSWERS = ("예", "아니오", "일부만")
ANSWER_KEYS = ("yes", "no", "partial")


class QuestionPayload(BaseModel):
    questions: list[str]


class GainPayload(BaseModel):
    gain: float


class GainBatchRow(BaseModel):
    question: int
    yes: float
    no: float
    partial: float


class GainBatchPayload(BaseModel):
    gains: list[GainBatchRow]


def _simulate_gain(provider: LLMProvider, context: str, question: str) -> float:
    """Average the simulated gain over equally likely assumed answers."""
    gains: list[float] = []
    for answer in ASSUMED_ANSWERS:
        payload = provider.complete_json(
            GAIN_SYSTEM,
            f"CONTEXT={context}\nQUESTION={question}\nASSUMED_ANSWER={answer}",
            GainPayload,
            0.0,
        )
        gains.append(max(0.0, min(1.0, payload.gain)))
    return sum(gains) / len(gains) if gains else 0.0


def build_gain_batch_prompt(context: str, questions: list[str]) -> str:
    lines = ["CONTEXT_BEGIN", context, "CONTEXT_END", "QUESTIONS:"]
    lines.extend(f"[{number}] {text}" for number, text in enumerate(questions, start=1))
    return "\n".join(lines)


def _simulate_gain_batch(
    provider: LLMProvider, context: str, questions: list[str]
) -> list[float] | None:
    """Score every question in one call. None means the answer was unusable (fall back)."""
    try:
        payload = provider.complete_json(
            GAIN_BATCH_SYSTEM, build_gain_batch_prompt(context, questions), GainBatchPayload, 0.0
        )
    except Exception as exc:
        if exc.__class__.__name__ == "BudgetExceeded":
            raise
        return None
    by_number = {row.question: row for row in payload.gains}
    gains: list[float] = []
    for number in range(1, len(questions) + 1):
        row = by_number.get(number)
        if row is None:
            return None
        values = [max(0.0, min(1.0, v)) for v in (row.yes, row.no, row.partial)]
        gains.append(sum(values) / len(values))
    return gains


def generate_questions(
    provider: LLMProvider,
    context: str,
    target_ids: list[str],
    settings: Settings = DEFAULT_SETTINGS,
) -> list[Question]:
    """Generate up to three candidate questions and score each by expected gain."""
    payload = provider.complete_json(QUESTION_SYSTEM, context, QuestionPayload, 0.0)
    texts = payload.questions[:3]
    gains: list[float] | None = None
    if texts and settings.clarify_gain == "none":
        # No scoring: the first question is asked. Its gain is a fixed placeholder above min_gain.
        gains = [max(settings.min_gain, 0.5)] + [0.0] * (len(texts) - 1)
    elif texts and settings.clarify_gain == "batch":
        gains = _simulate_gain_batch(provider, context, texts)
    if gains is None:
        gains = [_simulate_gain(provider, context, text) for text in texts]
    return [
        Question(id=f"Q{i}", text=text, target_artifact_ids=target_ids, expected_gain=gain)
        for i, (text, gain) in enumerate(zip(texts, gains, strict=True), start=1)
    ]


def select_question(
    questions: list[Question],
    settings: Settings = DEFAULT_SETTINGS,
) -> Question | None:
    """Pick the highest expected-gain question, or None when the gain is below min_gain."""
    if not questions:
        return None
    best = max(questions, key=lambda q: q.expected_gain)
    return best if best.expected_gain >= settings.min_gain else None
