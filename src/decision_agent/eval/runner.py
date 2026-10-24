from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from decision_agent.config import DEFAULT_SETTINGS, Settings
from decision_agent.eval.dataset import EvalCase, load_data_dir
from decision_agent.eval.methods import ALL_METHODS, ORACLE_STAGES, prepare, run_method
from decision_agent.llm.base import LLMProvider
from decision_agent.llm.mock_provider import MockProvider


def make_provider(name: str, seed: int) -> LLMProvider:
    if name == "mock":
        return MockProvider(seed=seed)
    if name == "anthropic":
        from decision_agent.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    raise ValueError(f"unknown provider {name}")


def evaluate(
    cases: list[EvalCase],
    methods: list[str],
    provider_name: str = "mock",
    repeats: int = 1,
    oracle_stages: list[str] | None = None,
    settings: Settings = DEFAULT_SETTINGS,
    log=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run every (case, oracle stage, repeat, method) and return artifact rows and run rows."""
    rows: list[dict] = []
    runs: list[dict] = []
    for oracle in oracle_stages or ["none"]:
        for repeat in range(repeats):
            for case in cases:
                provider = make_provider(provider_name, repeat)
                try:
                    prepared = prepare(case, provider, oracle)
                except Exception as exc:  # noqa: BLE001 - one bad case must not stop the run
                    for method in methods:
                        runs.append(_error_run(case, method, oracle, repeat, f"ingest: {exc}"))
                    continue
                gold = case.gold_by_id()
                for method in methods:
                    meta = {
                        "case_id": case.case_id,
                        "level": case.level,
                        "source": case.source,
                        "tags": ",".join(case.tags),
                        "method": method,
                        "oracle": oracle,
                        "repeat": repeat,
                    }
                    try:
                        result = run_method(method, case, prepared, provider, settings)
                    except Exception as exc:  # noqa: BLE001
                        runs.append(_error_run(case, method, oracle, repeat, str(exc)))
                        continue
                    for artifact in case.artifacts:
                        g = gold[artifact.id]
                        rows.append(
                            {
                                **meta,
                                "artifact_id": artifact.id,
                                "gold_label": g.label,
                                "gold_affected": g.affected,
                                "gold_clar": g.needs_clarification,
                                "scope_trap": g.scope_trap,
                                "pred": result.predictions[artifact.id].value,
                                "flagged": artifact.id in result.flagged,
                                "confidence": result.confidences.get(artifact.id),
                                "evidence": result.evidence.get(artifact.id, ""),
                            }
                        )
                    runs.append(
                        {
                            **meta,
                            "questions": result.questions,
                            "informative_answers": result.informative_answers,
                            "llm_calls": result.llm_calls,
                            "tool_calls": result.tool_calls,
                            "elapsed": result.elapsed,
                            "steps": json.dumps(result.steps),
                            "needs_history": case.needs_history(),
                            "needs_scope": case.needs_scope(),
                            "needs_clarify": case.needs_clarify(),
                            "ingest_calls": prepared.llm_calls,
                            "extraction_f1": prepared.extraction_f1,
                            "relation_f1": prepared.relation_f1,
                            "error": "",
                        }
                    )
                if log:
                    log(f"{oracle} r{repeat} {case.case_id}")
    return pd.DataFrame(rows), pd.DataFrame(runs)


def _error_run(case: EvalCase, method: str, oracle: str, repeat: int, message: str) -> dict:
    return {
        "case_id": case.case_id,
        "level": case.level,
        "source": case.source,
        "tags": ",".join(case.tags),
        "method": method,
        "oracle": oracle,
        "repeat": repeat,
        "questions": 0,
        "informative_answers": 0,
        "llm_calls": 0,
        "tool_calls": 0,
        "elapsed": 0.0,
        "steps": "[]",
        "needs_history": case.needs_history(),
        "needs_scope": case.needs_scope(),
        "needs_clarify": case.needs_clarify(),
        "ingest_calls": 0,
        "extraction_f1": None,
        "relation_f1": None,
        "error": message,
    }


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the evaluation and write a report.")
    parser.add_argument("--data-dir", default="data/eval")
    parser.add_argument("--sources", default="synthetic,assistant_written,human_written,real")
    parser.add_argument("--methods", default="all", help="comma list or 'all'")
    parser.add_argument("--provider", default="mock", choices=["mock", "anthropic"])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--oracle-stage", default="none", help="comma list of none,decisions,full")
    parser.add_argument("--n-samples", type=int, default=None, help="self-consistency samples")
    parser.add_argument("--levels", default="", help="comma list of levels to keep")
    parser.add_argument("--limit", type=int, default=0, help="keep only the first N cases")
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--out", default="out/eval_run")
    args = parser.parse_args(argv)

    methods = ALL_METHODS if args.methods == "all" else _split(args.methods)
    unknown = [m for m in methods if m not in ALL_METHODS]
    stages = _split(args.oracle_stage)
    if unknown or any(s not in ORACLE_STAGES for s in stages):
        parser.error(f"unknown method or oracle stage: {unknown or stages}")
    cases = load_data_dir(Path(args.data_dir), _split(args.sources))
    if args.levels:
        keep = {int(x) for x in _split(args.levels)}
        cases = [c for c in cases if c.level in keep]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        parser.error("no cases found; run the generator first")
    settings = replace(DEFAULT_SETTINGS, n=args.n_samples) if args.n_samples else DEFAULT_SETTINGS

    rows, runs = evaluate(
        cases,
        methods,
        args.provider,
        args.repeats,
        stages,
        settings,
        log=lambda m: print(m, file=sys.stderr),
    )
    from decision_agent.eval.report import write_report

    out = Path(args.out)
    write_report(out, rows, runs, cases, args.provider, args.repeats, args.bootstrap)
    print(f"wrote report to {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
