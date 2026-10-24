from __future__ import annotations

import numpy as np
import pandas as pd

KEY = ["method", "oracle"]


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else float("nan")


def f1_from_counts(tp: float, fp: float, fn: float) -> float:
    denom = 2 * tp + fp + fn
    return 2 * tp / denom if denom else float("nan")


def confusion(rows: pd.DataFrame) -> tuple[int, int, int]:
    """tp, fp, fn with INCONSISTENT as the positive class; UNCERTAIN counts as not positive."""
    pred = rows["pred"] == "INCONSISTENT"
    gold = rows["gold_label"] == "INCONSISTENT"
    return int((pred & gold).sum()), int((pred & ~gold).sum()), int((~pred & gold).sum())


def expected_calibration_error(confidence: pd.Series, correct: pd.Series, bins: int = 10) -> float:
    """ECE over equal-width bins. Confidence here is sample agreement, not a probability."""
    data = pd.DataFrame({"c": confidence.astype(float), "ok": correct.astype(float)}).dropna()
    if data.empty:
        return float("nan")
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.digitize(data["c"], edges[1:-1], right=True), 0, bins - 1)
    total = len(data)
    ece = 0.0
    for b in range(bins):
        part = data[index == b]
        if len(part):
            ece += len(part) / total * abs(part["ok"].mean() - part["c"].mean())
    return float(ece)


def reliability_bins(confidence: pd.Series, correct: pd.Series, bins: int = 5) -> pd.DataFrame:
    data = pd.DataFrame({"c": confidence.astype(float), "ok": correct.astype(float)}).dropna()
    if data.empty:
        return pd.DataFrame(columns=["confidence", "accuracy", "count"])
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.digitize(data["c"], edges[1:-1], right=True), 0, bins - 1)
    out = []
    for b in range(bins):
        part = data[index == b]
        if len(part):
            out.append((part["c"].mean(), part["ok"].mean(), len(part)))
    return pd.DataFrame(out, columns=["confidence", "accuracy", "count"])


def bootstrap_f1(rows: pd.DataFrame, n_boot: int = 1000, seed: int = 0) -> tuple[float, float]:
    """95 percent cluster bootstrap interval for F1, resampling whole cases."""
    if rows.empty:
        return float("nan"), float("nan")
    per_case = []
    for _, group in rows.groupby("case_id"):
        per_case.append(confusion(group))
    counts = np.array(per_case, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(counts), size=(n_boot, len(counts)))
    sums = counts[idx].sum(axis=1)
    denom = 2 * sums[:, 0] + sums[:, 1] + sums[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        f1 = np.where(denom > 0, 2 * sums[:, 0] / denom, np.nan)
    f1 = f1[~np.isnan(f1)]
    if len(f1) == 0:
        return float("nan"), float("nan")
    return float(np.percentile(f1, 2.5)), float(np.percentile(f1, 97.5))


def group_metrics(rows: pd.DataFrame, runs: pd.DataFrame, n_boot: int, seed: int) -> dict:
    tp, fp, fn = confusion(rows)
    aff = rows[rows["gold_affected"]]
    flagged = rows[rows["flagged"]]
    lo, hi = bootstrap_f1(rows, n_boot, seed)
    confident = aff[aff["pred"] != "UNCERTAIN"]
    traps = rows[rows["scope_trap"]]
    clar = rows[rows["gold_clar"]]
    no_clar_runs = runs[~runs["needs_clarify"]]
    rated = rows[(rows["pred"] != "UNCERTAIN") & rows["confidence"].notna() & rows["gold_affected"]]
    return {
        "n_cases": rows["case_id"].nunique(),
        "n_artifacts": len(rows),
        "precision": _safe_div(tp, tp + fp),
        "recall": _safe_div(tp, tp + fn),
        "f1": f1_from_counts(tp, fp, fn),
        "f1_ci_low": lo,
        "f1_ci_high": hi,
        "affected_recall": _safe_div(int(flagged["gold_affected"].sum()), len(aff)),
        "affected_precision": _safe_div(int(flagged["gold_affected"].sum()), len(flagged)),
        "accuracy_affected": _safe_div(int((aff["pred"] == aff["gold_label"]).sum()), len(aff)),
        "uncertain_rate": _safe_div(int((aff["pred"] == "UNCERTAIN").sum()), len(aff)),
        "confident_error_rate": _safe_div(
            int((confident["pred"] != confident["gold_label"]).sum()), len(aff)
        ),
        "scope_trap_fp_rate": _safe_div(int((traps["pred"] == "INCONSISTENT").sum()), len(traps)),
        "clarification_resolution": _safe_div(
            int((clar["pred"] == clar["gold_label"]).sum()), len(clar)
        ),
        "avg_questions": float(runs["questions"].mean()) if len(runs) else float("nan"),
        "unnecessary_question_rate": (
            float((no_clar_runs["questions"] > 0).mean()) if len(no_clar_runs) else float("nan")
        ),
        "ece": expected_calibration_error(
            rated["confidence"], rated["pred"] == rated["gold_label"]
        ),
        "avg_llm_calls": float(runs["llm_calls"].mean()) if len(runs) else float("nan"),
        "avg_tool_calls": float(runs["tool_calls"].mean()) if len(runs) else float("nan"),
        "avg_seconds": float(runs["elapsed"].mean()) if len(runs) else float("nan"),
        "error_runs": int(runs["error"].astype(bool).sum()) if len(runs) else 0,
    }


def summarize(
    rows: pd.DataFrame, runs: pd.DataFrame, n_boot: int = 1000, seed: int = 0
) -> pd.DataFrame:
    """One metrics row per (method, oracle stage)."""
    out = []
    for (method, oracle), group in rows.groupby(KEY, sort=False):
        group_runs = runs[(runs["method"] == method) & (runs["oracle"] == oracle)]
        out.append(
            {"method": method, "oracle": oracle, **group_metrics(group, group_runs, n_boot, seed)}
        )
    return pd.DataFrame(out)


def by_level(rows: pd.DataFrame) -> pd.DataFrame:
    """Per-level F1 for each method (oracle none only)."""
    out = []
    for (method, level), group in rows.groupby(["method", "level"], sort=True):
        tp, fp, fn = confusion(group)
        acc_rows = group[group["gold_affected"]]
        out.append(
            {
                "method": method,
                "level": level,
                "f1": f1_from_counts(tp, fp, fn),
                "accuracy_affected": _safe_div(
                    int((acc_rows["pred"] == acc_rows["gold_label"]).sum()), len(acc_rows)
                ),
            }
        )
    return pd.DataFrame(out)


def by_tag(rows: pd.DataFrame, tag: str) -> pd.DataFrame:
    """Metrics restricted to cases carrying a tag (for example scope_trap or retrieval_hard)."""
    subset = rows[rows["tags"].str.contains(tag, regex=False)]
    out = []
    for method, group in subset.groupby("method", sort=False):
        tp, fp, fn = confusion(group)
        traps = group[group["scope_trap"]]
        aff = group[group["gold_affected"]]
        out.append(
            {
                "tag": tag,
                "method": method,
                "n_cases": group["case_id"].nunique(),
                "f1": f1_from_counts(tp, fp, fn),
                "accuracy_affected": _safe_div(
                    int((aff["pred"] == aff["gold_label"]).sum()), len(aff)
                ),
                "affected_recall": _safe_div(int(aff["flagged"].sum()), len(aff)),
                "scope_trap_fp_rate": _safe_div(
                    int((traps["pred"] == "INCONSISTENT").sum()), len(traps)
                ),
            }
        )
    return pd.DataFrame(out)


def classify_errors(rows: pd.DataFrame) -> pd.DataFrame:
    """Attach an error category to every wrong or missed row."""
    wrong = []
    for row in rows.to_dict("records"):
        category = None
        if row["gold_affected"] and not row["flagged"]:
            category = "missed_affected"
        elif row["scope_trap"] and row["pred"] == "INCONSISTENT":
            category = "scope_trap_false_positive"
        elif row["gold_label"] == "CONSISTENT" and row["pred"] == "INCONSISTENT":
            category = "false_positive"
        elif row["gold_label"] == "INCONSISTENT" and row["pred"] == "CONSISTENT":
            category = "missed_inconsistency"
        elif row["pred"] == "UNCERTAIN" and row["gold_affected"]:
            category = (
                "uncertain_needs_clarification" if row["gold_clar"] else "uncertain_determinable"
            )
        if category:
            wrong.append({**row, "category": category})
    return pd.DataFrame(wrong)
