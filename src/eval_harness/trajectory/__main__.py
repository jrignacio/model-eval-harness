from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .runner import TrajectoryRunError, run_experiment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval_harness.trajectory",
        description="Run the process-isolated agent evaluation trajectory supervisor.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one experiment and build its report")
    run.add_argument("--experiment", required=True, help="experiment JSON file")
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="run the offline provider-free matrix")
    mode.add_argument("--live", action="store_true", help="run the explicitly capped live pilot")
    run.add_argument("--output", required=True, help="new output directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run":
        raise SystemExit(f"unsupported command: {args.command}")
    try:
        _manifest, results = run_experiment(
            Path(args.experiment),
            Path(args.output),
            mock=args.mock,
            live=args.live,
        )
    except (FileExistsError, TrajectoryRunError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"\nWrote {len(results)} attempts to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
