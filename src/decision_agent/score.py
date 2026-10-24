from __future__ import annotations

from decision_agent.models import ConsistencyLabel, ConsistencyResult


def consistency_score(results: list[ConsistencyResult]) -> int:
    if not results:
        return 100
    consistent = sum(r.label == ConsistencyLabel.CONSISTENT for r in results)
    uncertain = sum(r.label == ConsistencyLabel.UNCERTAIN for r in results)
    return round(100 * (consistent + 0.5 * uncertain) / len(results))
