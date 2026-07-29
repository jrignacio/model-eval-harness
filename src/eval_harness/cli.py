from __future__ import annotations

import argparse
from pathlib import Path

from .cases import CaseValidationError, load_cases
from .models import create_adapter
from .runner import run_evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eval-harness",
        description="Compare language models on JSONL cases with rubric-based judging.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run an evaluation matrix and build a report")
    run.add_argument("--cases", required=True, help="path to JSONL test cases")
    run.add_argument(
        "--models",
        required=True,
        help="comma-separated candidates using provider:model syntax",
    )
    run.add_argument(
        "--judge",
        default="heuristic",
        help="provider:model judge, or heuristic for a zero-cost plumbing check",
    )
    run.add_argument("--output", required=True, help="output directory for run artifacts")
    run.add_argument("--max-workers", type=int, default=4)
    run.add_argument("--max-tokens", type=int, default=1200)
    run.add_argument("--temperature", type=float, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_workers < 1:
        raise SystemExit("--max-workers must be at least 1")
    if args.max_tokens < 1:
        raise SystemExit("--max-tokens must be at least 1")
    if not 0 <= args.temperature <= 2:
        raise SystemExit("--temperature must be between 0 and 2")
    try:
        cases = load_cases(args.cases)
        model_specs = [item.strip() for item in args.models.split(",") if item.strip()]
        if not model_specs:
            raise ValueError("--models must contain at least one model")
        candidates = [create_adapter(item) for item in model_specs]
        if len({candidate.name for candidate in candidates}) != len(candidates):
            raise ValueError("--models contains a duplicate model")
        judge = None if args.judge == "heuristic" else create_adapter(args.judge)
    except (CaseValidationError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    results = run_evaluation(
        cases=cases,
        candidates=candidates,
        judge=judge,
        output=Path(args.output),
        source_path=str(Path(args.cases)),
        max_workers=args.max_workers,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )
    errors = sum(bool(result.error) for result in results)
    print(f"\nWrote {len(results)} results to {args.output}")
    return 1 if errors else 0

