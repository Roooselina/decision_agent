from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from decision_agent.eval import metrics, path_analysis
from decision_agent.eval.dataset import LEVEL_NAMES, EvalCase
from decision_agent.eval.methods import METHOD_DESCRIPTIONS

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
SERIES = "#2a78d6"
GRID = "#e4e3df"

MOCK_BANNER = (
    "> **Smoke test only.** These numbers come from MockProvider, a rule-based stand-in that only "
    "knows the demo vocabulary. They show that the pipeline, metrics, and report run end to end. "
    "They are not evidence of model quality. Run with `--provider anthropic` for real results."
)


def fmt(value: object) -> str:
    if isinstance(value, float):
        return "n/a" if pd.isna(value) else f"{value:.3f}"
    return str(value)


def md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(no data)\n"
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    rule = "| " + " | ".join("---" for _ in df.columns) + " |"
    body = ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([header, rule, *body]) + "\n"


def _style(ax, title: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    ax.tick_params(colors=INK_SECONDARY, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def bar_chart(
    path: Path,
    labels: list[str],
    values: list[float],
    title: str,
    errors: tuple[list[float], list[float]] | None = None,
) -> None:
    fig, ax = plt.subplots(figsize=(7, 3.6))
    _style(ax, title)
    x = range(len(labels))
    values = [0.0 if pd.isna(v) else v for v in values]
    ax.bar(x, values, color=SERIES, width=0.55)
    if errors is not None:
        lows = [max(v - lo, 0.0) for v, lo in zip(values, errors[0], strict=True)]
        highs = [max(hi - v, 0.0) for v, hi in zip(values, errors[1], strict=True)]
        ax.errorbar(x, values, yerr=[lows, highs], fmt="none", ecolor=INK_SECONDARY, capsize=3)
    tops = list(errors[1]) if errors is not None else values
    for i, v in enumerate(values):
        ax.text(i, max(v, tops[i]) + 0.02, f"{v:.2f}", ha="center", color=INK, fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylim(0, 1.1)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def reliability_chart(path: Path, rows: pd.DataFrame, method: str) -> bool:
    subset = rows[
        (rows["method"] == method) & (rows["pred"] != "UNCERTAIN") & rows["gold_affected"]
    ]
    table = metrics.reliability_bins(subset["confidence"], subset["pred"] == subset["gold_label"])
    if table.empty:
        return False
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    _style(ax, f"Reliability, {method}")
    ax.plot([0, 1], [0, 1], color=INK_SECONDARY, linewidth=1, linestyle="--")
    ax.plot(
        table["confidence"], table["accuracy"], color=SERIES, linewidth=2, marker="o", markersize=8
    )
    ax.set_xlabel("mean sample agreement", color=INK_SECONDARY)
    ax.set_ylabel("accuracy", color=INK_SECONDARY)
    ax.set_xlim(0, 1.02)
    ax.set_ylim(0, 1.05)
    ax.text(0.05, 0.9, "diagonal: perfectly calibrated", color=INK_SECONDARY, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


def _pick(summary: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return summary[["method", *columns]]


def write_report(
    out: Path,
    rows: pd.DataFrame,
    runs: pd.DataFrame,
    cases: list[EvalCase],
    provider: str,
    repeats: int,
    n_boot: int = 1000,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows.to_csv(out / "rows.csv", index=False)
    runs.to_csv(out / "runs.csv", index=False)
    lines: list[str] = ["# Evaluation report", ""]
    if provider == "mock":
        lines += [MOCK_BANNER, ""]
    lines += [f"Provider: {provider}. Repeats: {repeats}.", ""]

    composition = pd.DataFrame(
        [{"source": c.source, "level": c.level, "case_id": c.case_id} for c in cases]
    )
    pivot = composition.pivot_table(
        index="source", columns="level", values="case_id", aggfunc="count", fill_value=0
    ).reset_index()
    pivot.columns = [str(c) for c in pivot.columns]
    lines += ["## Dataset", md_table(pivot)]
    lines += [
        "Levels: " + "; ".join(f"{k} = {v}" for k, v in LEVEL_NAMES.items()) + ".",
        "",
        "Methods: " + "; ".join(f"{k} = {v}" for k, v in METHOD_DESCRIPTIONS.items()) + ".",
        "",
    ]
    if rows.empty:
        lines += ["No successful runs.", ""]
        (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
        return

    summary = metrics.summarize(rows, runs, n_boot)
    summary.to_csv(out / "summary.csv", index=False)
    main = summary[summary["oracle"] == "none"]
    base = rows[rows["oracle"] == "none"]
    base_runs = runs[runs["oracle"] == "none"]

    lines += ["## Experiment 1: main comparison (full pipeline, no oracle)"]
    cols = ["n_cases", "precision", "recall", "f1", "f1_ci_low", "f1_ci_high", "accuracy_affected"]
    lines += [md_table(_pick(main, cols))]
    lines += [
        (
            "F1 treats INCONSISTENT as the positive class; UNCERTAIN counts as not positive. "
            "The interval is a 95 percent bootstrap over cases."
        ),
        "",
        "Retrieval and calibration:",
        md_table(
            _pick(
                main,
                [
                    "affected_recall",
                    "affected_precision",
                    "uncertain_rate",
                    "confident_error_rate",
                    "ece",
                ],
            )
        ),
        "ECE uses sample agreement as confidence; it is a heuristic score, not a probability.",
        "",
    ]

    lines += ["## Experiment 2: by level (F1 / accuracy on affected artifacts)"]
    level_table = metrics.by_level(base)
    wide = level_table.pivot(index="method", columns="level", values="f1").reset_index()
    wide.columns = ["method", *[f"L{c}" for c in wide.columns[1:]]]
    lines += [md_table(wide)]

    lines += ["## Experiment 2b: by case tag"]
    tag_tables = [
        metrics.by_tag(base, tag)
        for tag in ("scope_trap", "retrieval_hard", "vague_reference", "unknown_scope")
    ]
    lines += [md_table(pd.concat(tag_tables, ignore_index=True))]

    lines += ["## Experiment 3: scope ablation (M1c versus A_scope_agnostic)"]
    if provider == "mock":
        lines += [
            (
                "With MockProvider this comparison cannot show a difference: the mock judge reads "
                "scope words such as MVP straight from the artifact text, so removing the stored "
                "scopes changes nothing. Only a real model run can measure the value of the "
                "scope resolver."
            ),
            "",
        ]
    scope_rows = metrics.by_tag(base, "scope_trap")
    lines += [
        (
            "Cases tagged scope_trap contain decoy artifacts (for example Production items next "
            "to a MVP-only exclusion). scope_trap_fp_rate is the share of decoys wrongly flagged."
        ),
        md_table(scope_rows),
    ]

    lines += ["## Experiment 4: clarification"]
    lines += [
        md_table(
            _pick(
                main,
                [
                    "uncertain_rate",
                    "clarification_resolution",
                    "avg_questions",
                    "unnecessary_question_rate",
                    "f1",
                ],
            )
        ),
        (
            "clarification_resolution = share of artifacts that need a user answer and end with "
            "the correct label. unnecessary_question_rate = share of cases needing no answer "
            "where a question was still asked."
        ),
        "",
    ]

    lines += ["## Experiment 5: agent path and cost"]
    lines += [md_table(path_analysis.path_table(base_runs))]
    lines += [md_table(path_analysis.sequence_table(base_runs))]
    lines += [
        "Cost per case:",
        md_table(_pick(main, ["avg_llm_calls", "avg_tool_calls", "avg_seconds", "error_runs"])),
    ]

    stages = sorted(summary["oracle"].unique())
    lines += ["## Experiment 6: error attribution by oracle stage"]
    if len(stages) > 1:
        attr = summary[["method", "oracle", "f1", "accuracy_affected", "affected_recall"]]
        lines += [md_table(attr)]
        ingest = base_runs.groupby("method")[["extraction_f1", "relation_f1"]].mean().reset_index()
        lines += ["Ingest stage quality without oracle (decision extraction and relation F1):"]
        lines += [md_table(ingest)]
    else:
        lines += ["Run with `--oracle-stage none,decisions,full` to fill this section.", ""]

    errors = metrics.classify_errors(base)
    if not errors.empty:
        counts = errors.groupby(["method", "category"]).size().reset_index(name="count")
        lines += ["## Error categories (no oracle)", md_table(counts)]
        keep = ["case_id", "method", "artifact_id", "gold_label", "pred", "category", "evidence"]
        (out / "errors.json").write_text(
            json.dumps(errors[keep].to_dict("records"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        (out / "errors.json").write_text("[]", encoding="utf-8")

    chart_main = main.reset_index(drop=True)
    bar_chart(
        out / "f1_by_method.png",
        list(chart_main["method"]),
        list(chart_main["f1"]),
        "F1 for INCONSISTENT by method (95% bootstrap interval)",
        (list(chart_main["f1_ci_low"].fillna(0)), list(chart_main["f1_ci_high"].fillna(0))),
    )
    lines += ["![F1 by method](f1_by_method.png)", ""]
    if not scope_rows.empty:
        bar_chart(
            out / "scope_trap_fp.png",
            list(scope_rows["method"]),
            list(scope_rows["scope_trap_fp_rate"]),
            "Share of scope decoys wrongly flagged (lower is better)",
        )
        lines += ["![Scope decoys](scope_trap_fp.png)", ""]
    if reliability_chart(out / "reliability_M1c.png", base, "M1c"):
        lines += ["![Reliability](reliability_M1c.png)", ""]

    lines += [
        "## Limits",
        "- Synthetic and assistant-written cases are not independent human-written evidence.",
        "- The simulated user answers from scripted facts and never disagrees or hesitates.",
        (
            "- Candidate retrieval is character TF-IDF; artifacts sharing no characters with the "
            "decision text are never retrieved (see the retrieval_hard tag)."
        ),
        "- B2_rag uses sparse TF-IDF in place of dense embeddings so the run needs no network.",
        "",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild a report from rows.csv and runs.csv.")
    parser.add_argument("--run", required=True)
    parser.add_argument("--provider", default="unknown")
    args = parser.parse_args()
    run = Path(args.run)
    rows = pd.read_csv(run / "rows.csv", keep_default_na=False, na_values=[""])
    runs = pd.read_csv(run / "runs.csv", keep_default_na=False, na_values=[""])
    for column in ("gold_affected", "gold_clar", "scope_trap", "flagged"):
        rows[column] = rows[column].astype(bool)
    for column in ("needs_history", "needs_scope", "needs_clarify"):
        runs[column] = runs[column].astype(bool)
    rows["tags"] = rows["tags"].fillna("")
    write_report(run, rows, runs, [], args.provider, int(rows["repeat"].max()) + 1)


if __name__ == "__main__":
    main()
