"""Evaluate plans, returned values, refusals, retrieval, latency, and token usage."""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys

from .analytics import ROOT
from .api import from_settings
from .assistant import Plan, retrieve
from .budget import BudgetError
from .llm import OpenAIPlanner


def matches(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and matches(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(matches(a, b) for a, b in zip(expected, actual))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-9)
    return expected == actual


def evaluate(assistant, cases):
    if not cases:
        raise ValueError("An empty evaluation does not validate the assistant")
    outcomes = []
    latency = []
    tokens = {"input_tokens": 0, "output_tokens": 0}
    recalled = positives = 0
    for index, case in enumerate(cases):
        expected = Plan.model_validate(case["expected_plan"])
        if expected.metric != "unsupported":
            positives += 1
            recalled += expected.metric in {item["id"] for item in retrieve(case["question"])}
        try:
            actual = assistant.ask(case["question"])
            plan_ok = actual["plan"] == expected.model_dump()
            rows_ok = "expected_rows" not in case or matches(case["expected_rows"], actual["rows"])
            evidence_ok = (actual["query"] is None and not actual["citations"]) if expected.metric == "unsupported" else bool(actual["query"] and actual["citations"])
            latency.append(actual["timing_ms"]["total"])
            for key in tokens:
                tokens[key] += actual["usage"].get(key, 0)
            outcomes.append({"id": case["id"], "passed": plan_ok and rows_ok and evidence_ok,
                             "plan_correct": plan_ok, "rows_correct": rows_ok, "evidence_correct": evidence_ok,
                             "actual_plan": actual["plan"]})
        except BudgetError as error:
            outcomes.append({"id":case["id"],"passed":False,"error_type":type(error).__name__})
            outcomes.extend({"id":remaining["id"],"passed":False,"not_run":True,"reason":"usage guard stopped evaluation"}
                            for remaining in cases[index+1:])
            break
        except Exception as error:
            outcomes.append({"id": case["id"], "passed": False, "error_type": type(error).__name__})
    ordered = sorted(latency)
    return {
        "dataset": "careflow_extended_demo", "cases": len(cases),
        "passed": sum(row["passed"] for row in outcomes),
        "failed": sum(not row["passed"] and not row.get("not_run",False) for row in outcomes),
        "unrun": sum(row.get("not_run",False) for row in outcomes),
        "retrieval_recall_at_3": recalled / positives if positives else None,
        "median_latency_ms": statistics.median(latency) if latency else None,
        "p95_latency_ms": ordered[max(0, math.ceil(.95 * len(ordered)) - 1)] if ordered else None,
        "usage": tokens, "estimated_cost_usd": None, "outcomes": outcomes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["baseline", "llm"], default="baseline")
    parser.add_argument("--suite", choices=["core", "paraphrase", "all"], default="core")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cases",type=int,help="Limit the batch; start with 1 for live calls")
    parser.add_argument("--offset",type=int,default=0,help="Skip this many selected-suite cases for later batches")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a fresh output path before making model calls")
    if args.offset < 0 or (args.max_cases is not None and args.max_cases < 1):
        parser.error("offset must be nonnegative and max-cases must be positive")
    cases = json.loads((ROOT / "evaluation" / "assistant_cases.json").read_text())
    cases = [case for case in cases if args.suite == "all" or case["suite"] == args.suite]
    cases = cases[args.offset:]
    if args.max_cases is not None:
        cases=cases[:args.max_cases]
    if not cases:
        parser.error("No cases selected")
    assistant = from_settings(mode=args.mode)
    try:
        if isinstance(assistant.planner,OpenAIPlanner):
            usage=assistant.planner.ledger.snapshot()
            remaining=min(usage[scope]["remaining_requests"] for scope in ("daily","lifetime"))
            if usage["halted"] or len(cases)>remaining:
                parser.error("Live batch exceeds remaining request allowance; use --max-cases 1 and inspect careflow usage")
        result = {"mode": args.mode, "suite": args.suite, **evaluate(assistant, cases)}
    finally:
        assistant.engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in result.items() if key != "outcomes"}, indent=2))
    sys.exit(1 if result["failed"] or result["unrun"] else 0)


if __name__ == "__main__":
    main()
