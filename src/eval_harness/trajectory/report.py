"""Markdown reporting for trajectory attempts."""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _range(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"{min(values):.1f}–{max(values):.1f}"


def _median(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"{statistics.median(values):.1f}"


def _metric(group: list[dict[str, Any]], name: str, *, successful_only: bool = False) -> str:
    values = [
        float(item[name])
        for item in group
        if item.get(name) is not None
        and (not successful_only or item.get("verdict") == "pass")
    ]
    return f"{_median(values)} ({_range(values)})"


def write_report(path: str | Path, manifest: dict[str, Any], results: list[dict[str, Any]]) -> None:
    calibration_results = [item for item in results if item.get("phase") == "calibration"]
    measured_results = [item for item in results if item.get("phase", "measured") == "measured"]
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_pair: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for result in measured_results:
        by_group[(result["task_id"], result["arm"])].append(result)
        by_pair[(result["task_id"], result["repetition"])][result["arm"]] = result

    lines = [
        f"# {manifest['experiment_id']}",
        "",
        f"- Mode: `{manifest['mode']}`",
        f"- Model label: `{manifest['model']}`",
        f"- Environment: `{manifest['environment']}`",
        f"- Scheduled attempts: {manifest['scheduled_attempts']}",
        f"- Executed attempts: {manifest.get('completed_attempts', len(results))}",
        f"- Randomization seed: `{manifest['randomization_seed']}`",
        f"- Known provider calls: {manifest.get('provider_calls', 0)}",
        f"- Known cost (USD): `{manifest.get('cost_usd', 0.0):.6f}`",
        f"- Cost accounting: `{manifest.get('cost_accounting', 'not reported')}`",
        "",
        (
            "This report shows operational outcomes and paired measurements. "
            "It does not create a composite leaderboard score."
        ),
        "",
        "## Outcomes by task and interface",
        "",
        (
            "| Task | Interface | Autonomous pass | Assisted pass | Other outcomes | "
            "Attempts | Agent time median (range, ms) | Requests median (range) | "
            "Tools median (range) |"
        ),
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for (task_id, arm), group in sorted(by_group.items()):
        autonomous_pass = sum(
            item.get("verdict") == "pass" and item.get("autonomy") == "autonomous"
            for item in group
        )
        assisted_pass = sum(
            item.get("verdict") == "pass" and item.get("autonomy") == "assisted"
            for item in group
        )
        other = len(group) - autonomous_pass - assisted_pass
        lines.append(
            f"| `{task_id}` | `{arm}` | {autonomous_pass} | {assisted_pass} | {other} | "
            f"{len(group)} | "
            f"{_metric(group, 'elapsed_ms')} | {_metric(group, 'model_requests')} | "
            f"{_metric(group, 'tool_invocations')} |"
        )

    if calibration_results:
        lines.extend(
            [
                "",
                "## Calibration",
                "",
                "Calibration attempts use the selected task and are excluded from paired measurements.",
                "",
                "| Attempt | Interface | Outcome | Verdict | Requests | Cost (USD) |",
                "| --- | --- | --- | --- | ---: | ---: |",
            ]
        )
        for result in calibration_results:
            lines.append(
                f"| `{result['attempt_id']}` | `{result['arm']}` | `{result['outcome']}` | "
                f"`{result.get('verdict') or 'n/a'}` | {result.get('model_requests', 0)} | "
                f"{float(result.get('cost_usd', 0.0) or 0.0):.6f} |"
            )

    lines.extend(
        [
            "",
            "## Paired differences",
            "",
            "Each row compares DOM minus WebMCP within the same task and repetition.",
            "",
            (
                "| Task | Complete pairs / scheduled pairs | "
                "Agent-time difference median (range, ms) | "
                "Request difference median (range) |"
            ),
            "| --- | ---: | --- | --- |",
        ]
    )
    paired_by_task: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for (task_id, _repetition), pair in by_pair.items():
        if "dom" not in pair or "webmcp" not in pair:
            continue
        dom = pair["dom"]
        webmcp = pair["webmcp"]
        if dom.get("elapsed_ms") is not None and webmcp.get("elapsed_ms") is not None:
            paired_by_task[task_id].append(
                (
                    float(dom["elapsed_ms"]) - float(webmcp["elapsed_ms"]),
                    float(dom["model_requests"]) - float(webmcp["model_requests"]),
                )
            )
    scheduled_pairs_by_task = {
        task["id"]: int(manifest["repetitions"])
        for task in manifest.get("tasks", [])
    }
    for task_id in sorted({task for task, _ in by_group}):
        pairs = paired_by_task.get(task_id, [])
        time_values = [item[0] for item in pairs]
        request_values = [item[1] for item in pairs]
        lines.append(
            f"| `{task_id}` | {len(pairs)} / {scheduled_pairs_by_task.get(task_id, 0)} | "
            f"{_median(time_values)} ({_range(time_values)}) | "
            f"{_median(request_values)} ({_range(request_values)}) |"
        )

    lines.extend(
        [
            "",
            "## Successful-only measurements",
            "",
            (
                "These values exclude attempts whose verdict was not `pass`; "
                "operational outcomes above retain every scheduled attempt."
            ),
            "",
            (
                "| Task | Interface | Autonomy | Successful attempts | "
                "Agent time median (range, ms) | "
                "Requests median (range) | Tools median (range) |"
            ),
            "| --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for (task_id, arm), group in sorted(by_group.items()):
        for autonomy in ("autonomous", "assisted"):
            successful = [
                item
                for item in group
                if item.get("verdict") == "pass" and item.get("autonomy") == autonomy
            ]
            lines.append(
                f"| `{task_id}` | `{arm}` | `{autonomy}` | {len(successful)} | "
                f"{_metric(successful, 'elapsed_ms')} | "
                f"{_metric(successful, 'model_requests')} | "
                f"{_metric(successful, 'tool_invocations')} |"
            )

    outcome_counts = manifest.get("summary", {}).get("outcomes", {})
    lines.extend(
        [
            "",
            "## Operational outcome counts",
            "",
            "| Outcome | Attempts |",
            "| --- | ---: |",
        ]
    )
    for outcome, count in sorted(outcome_counts.items()):
        lines.append(f"| `{outcome}` | {count} |")
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


__all__ = ["write_report"]
