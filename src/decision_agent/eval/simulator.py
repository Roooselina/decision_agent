from __future__ import annotations

from dataclasses import dataclass, field

from decision_agent.eval.dataset import EvalCase


@dataclass
class UserSimulator:
    """Answers clarification questions from the case's scripted facts.

    The first fact with a keyword contained in the question wins. A question that matches no
    fact gets the case's default answer, so unhelpful questions cost a question without
    producing information. This keeps evaluation reproducible; it is not a human substitute.
    """

    case: EvalCase
    log: list[tuple[str, str, bool]] = field(default_factory=list)

    def ask(self, question: str) -> str:
        for fact in self.case.user_facts:
            if any(keyword in question for keyword in fact.keywords):
                self.log.append((question, fact.answer, True))
                return fact.answer
        self.log.append((question, self.case.default_answer, False))
        return self.case.default_answer

    @property
    def questions(self) -> int:
        return len(self.log)

    @property
    def informative_answers(self) -> int:
        return sum(1 for _, _, hit in self.log if hit)
