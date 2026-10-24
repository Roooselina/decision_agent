from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable


class UserInterface(ABC):
    """Interface used by Agent and Pipeline for one-at-a-time clarification."""

    @abstractmethod
    def ask(self, question: str) -> str:
        raise NotImplementedError


class CLIUserInterface(UserInterface):
    def ask(self, question: str) -> str:
        return input(f"{question}\n> ").strip()


class CallbackUserInterface(UserInterface):
    """Answers questions with a callback; used by tests and the evaluation simulator."""

    def __init__(self, callback: Callable[[str], str]) -> None:
        self.callback = callback

    def ask(self, question: str) -> str:
        return str(self.callback(question))


class NeedUserInput(Exception):
    """Raised by ReplayUserInterface when a question has no recorded answer yet."""

    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


class ReplayUserInterface(UserInterface):
    """Supplies recorded answers in order and raises NeedUserInput for the next question.

    Streamlit cannot block on input, so the app reruns the analysis with the answers collected
    so far. Combined with CachingProvider, earlier LLM calls are replayed rather than repeated.
    """

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def ask(self, question: str) -> str:
        index = len(self.asked)
        self.asked.append(question)
        if index < len(self.answers):
            return self.answers[index]
        raise NeedUserInput(question)
