"""Optional reuse of earlier line verdicts and impact selections.

The core pipeline never needs this: with no store every line is judged as before. The desktop
engine passes a store bound to one topic's decision state, judging model and sampling settings,
so a small change re-judges only the lines that are new or changed.
"""

from __future__ import annotations

from typing import Protocol

from decision_agent.consistency import PreparedCheck
from decision_agent.models import Artifact, ConsistencyResult


class VerdictStore(Protocol):
    def get_verdicts(self, items: list[PreparedCheck]) -> list[ConsistencyResult | None]:
        """Stored results for the prepared checks, None where nothing reusable is stored."""
        ...

    def put_verdicts(self, items: list[tuple[PreparedCheck, ConsistencyResult]]) -> None:
        """Store fresh model results."""
        ...

    def get_selections(self, candidates: list[Artifact]) -> dict[str, bool]:
        """artifact id -> whether the impact selection chose it, for candidates seen before."""
        ...

    def put_selections(self, candidates: list[Artifact], selected: set[str]) -> None:
        """Store the impact answer for candidates that were just sent to the model."""
        ...

