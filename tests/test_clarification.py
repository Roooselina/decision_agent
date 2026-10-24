from decision_agent.clarification import generate_questions, select_question
from decision_agent.config import Settings
from decision_agent.llm.mock_provider import MockProvider


def test_question_limit_and_min_gain():
    questions = generate_questions(MockProvider(), "ctx", ["A"])
    assert len(questions) <= 3
    assert select_question(questions) is not None
    assert select_question(questions, Settings(min_gain=1.0)) is None


def test_highest_gain_question_is_selected():
    questions = generate_questions(MockProvider(), "발표 일정", ["A"])
    chosen = select_question(questions)
    assert chosen.expected_gain == max(q.expected_gain for q in questions)
    assert "리허설" in chosen.text
