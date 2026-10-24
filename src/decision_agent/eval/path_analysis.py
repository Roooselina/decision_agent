from __future__ import annotations

import json

import pandas as pd

AGENT_METHODS = ("M1", "M1c", "A_scope_agnostic")


def _steps(value: str) -> list[str]:
    try:
        return list(json.loads(value))
    except (TypeError, ValueError):
        return []


def path_table(runs: pd.DataFrame) -> pd.DataFrame:
    """How the agent's tool choices relate to what each case actually needed.

    A rate is NaN when no case of that kind exists. Only agent methods are analysed because a
    fixed pipeline has no choices to analyse.
    """
    out = []
    agent = runs[runs["method"].isin(AGENT_METHODS) & (runs["oracle"] == "none")]
    for method, group in agent.groupby("method", sort=False):
        steps = group["steps"].map(_steps)
        history = steps.map(lambda s: "get_decision_history" in s)
        scope = steps.map(lambda s: "resolve_scope" in s)
        asked = steps.map(lambda s: "ask_user" in s)
        checks = steps.map(lambda s: s.count("check_consistency"))

        def rate(flag: pd.Series, mask: pd.Series) -> float:
            return float(flag[mask].mean()) if mask.any() else float("nan")

        out.append(
            {
                "method": method,
                "runs": len(group),
                "avg_steps": float(steps.map(len).mean()),
                "history_when_needed": rate(history, group["needs_history"]),
                "history_when_not_needed": rate(history, ~group["needs_history"]),
                "scope_check_when_needed": rate(scope, group["needs_scope"]),
                "scope_check_when_not_needed": rate(scope, ~group["needs_scope"]),
                "ask_when_needed": rate(asked, group["needs_clarify"]),
                "ask_when_not_needed": rate(asked, ~group["needs_clarify"]),
                "redundant_checks": float((checks - 1).clip(lower=0).mean()),
                "distinct_paths": int(steps.map(lambda s: " > ".join(s)).nunique()),
            }
        )
    return pd.DataFrame(out)


def sequence_table(runs: pd.DataFrame, top: int = 8) -> pd.DataFrame:
    """Most frequent tool sequences per agent method, to show whether paths adapt to the case."""
    agent = runs[runs["method"].isin(AGENT_METHODS) & (runs["oracle"] == "none")].copy()
    agent["path"] = agent["steps"].map(lambda v: " > ".join(_steps(v)))
    table = agent.groupby(["method", "path"]).size().reset_index(name="count")
    table = table.sort_values(["method", "count"], ascending=[True, False])
    return table.groupby("method").head(top).reset_index(drop=True)
