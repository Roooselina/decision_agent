"""Deterministic ordering of similarity scores.

numpy's argsort is not stable, and scores of templated lines are often equal or differ only by
float noise. Adding one unrelated document then moves array positions and reorders ties, which
changes candidate sets that have nothing to do with the new document. Ordering by the rounded
score and then by a stable id keeps the order identical for the same inputs.
"""

from __future__ import annotations

from collections.abc import Sequence

SCORE_DIGITS = 9


def stable_order(scores: Sequence[float], keys: Sequence[str]) -> list[int]:
    """Indexes sorted by score descending, ties broken by key ascending."""
    return sorted(range(len(scores)), key=lambda i: (-round(float(scores[i]), SCORE_DIGITS), keys[i]))
