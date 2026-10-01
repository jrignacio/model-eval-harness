"""Process-isolated offline trajectory supervisor."""

from __future__ import annotations

import copy
import math
import json
import multiprocessing as mp
import random
import sys
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .environment import run_mock_attempt, state_view
from .events import EventWriter, read_events
from .evaluator import EVALUATOR_VERSION, EvaluatorError, evaluate_task
from .live import run_live_attempt
from .report import write_report


class TrajectoryRunError(RuntimeError):
    """The experiment or supervisor contract is invalid."""


class ProcessTimeoutError(TimeoutError):
    """An attempt exceeded its hard process deadline."""


class ProcessExecutionError(RuntimeError):
    """An isolated attempt process failed before returning a result."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrajectoryRunError(f"could not read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TrajectoryRunError(f"JSON file {path} must contain an object")
    return value


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrajectoryRunError(f"{field} must be a non-empty string")
    return value


def _validate_task(task: dict[str, Any], path: Path) -> dict[str, Any]:
    _require_text(task.get("id"), f"{path}: id")
    _require_text(task.get("prompt"), f"{path}: prompt")
    kind = _require_text(task.get("kind"), f"{path}: kind")
    if kind not in {"catalog", "cart", "shipping", "stock"}:
        raise TrajectoryRunError(f"{path}: unsupported task kind {kind!r}")
    if not isinstance(task.get("inputs", {}), dict):
        raise TrajectoryRunError(f"{path}: inputs must be an object")
    if not isinstance(task.get("initial_state"), dict):
        raise TrajectoryRunError(f"{path}: initial_state must be an object")
    if not isinstance(task.get("evaluation"), dict):
        raise TrajectoryRunError(f"{path}: evaluation must be an object")
    return task


def load_experiment(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load an experiment and its supervisor-only task specifications."""

    experiment_path = Path(path).resolve()
    experiment = _read_json(experiment_path)
    _require_text(experiment.get("id"), "experiment id")
    _require_text(experiment.get("model"), "experiment model")
    interfaces = experiment.get("interfaces")
    if interfaces != ["dom", "webmcp"]:
        raise TrajectoryRunError("experiment interfaces must be exactly ['dom', 'webmcp']")
    repetitions = experiment.get("repetitions")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise TrajectoryRunError("experiment repetitions must be a positive integer")
    seed = experiment.get("randomization_seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TrajectoryRunError("experiment randomization_seed must be an integer")
    task_paths = experiment.get("tasks")
    if not isinstance(task_paths, list) or not task_paths:
        raise TrajectoryRunError("experiment tasks must be a non-empty list")
    timeout_seconds = experiment.get("budgets", {}).get("attempt_timeout_seconds")
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise TrajectoryRunError("experiment budgets.attempt_timeout_seconds must be positive")

    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for task_path in task_paths:
        if not isinstance(task_path, str) or not task_path:
            raise TrajectoryRunError("experiment task paths must be non-empty strings")
        resolved = (experiment_path.parent / task_path).resolve()
        task = _validate_task(_read_json(resolved), resolved)
        if task["id"] in seen_ids:
            raise TrajectoryRunError(f"duplicate task id: {task['id']}")
        seen_ids.add(task["id"])
        tasks.append(task)
    return experiment, tasks


def build_schedule(experiment: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rng = random.Random(experiment["randomization_seed"])
    schedule: list[dict[str, Any]] = []
    counter = 0
    for repetition in range(1, experiment["repetitions"] + 1):
        for task in tasks:
            arms = ["dom", "webmcp"]
            rng.shuffle(arms)
            for order, arm in enumerate(arms, start=1):
                counter += 1
                schedule.append(
                    {
                        "attempt_index": counter,
                        "attempt_id": f"attempt-{counter:03d}",
                        "task_id": task["id"],
                        "arm": arm,
                        "repetition": repetition,
                        "pair_order": order,
                    }
                )
    return schedule


def _process_entry(
    connection: Any,
    function: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    try:
        connection.send(("ok", function(*args, **kwargs)))
    except BaseException as exc:  # noqa: BLE001 - return child failures to the supervisor
        connection.send(
            (
                "error",
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        )
    finally:
        connection.close()


def run_in_process(
    function: Callable[..., Any],
    *args: Any,
    timeout_seconds: float,
    **kwargs: Any,
) -> Any:
    """Run a picklable callable with a hard child-process timeout."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    context = mp.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_process_entry,
        args=(child_connection, function, args, kwargs),
    )
    process.start()
    child_connection.close()
    deadline = time.monotonic() + timeout_seconds
    status = payload = None
    received = False
    try:
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            if parent_connection.poll(min(0.05, remaining)):
                try:
                    status, payload = parent_connection.recv()
                except EOFError as exc:
                    raise ProcessExecutionError("child closed its result channel") from exc
                received = True
                break
            if not process.is_alive():
                break
        if not received:
            if process.is_alive():
                process.terminate()
                process.join(1)
                if process.is_alive():
                    process.kill()
                    process.join()
                raise ProcessTimeoutError(f"process exceeded {timeout_seconds:g}s deadline")
            if parent_connection.poll(0.5):
                try:
                    status, payload = parent_connection.recv()
                except EOFError as exc:
                    raise ProcessExecutionError("child closed its result channel") from exc
                received = True
            else:
                raise ProcessExecutionError(
                    f"child exited without a result (exit code {process.exitcode})"
                )
        process.join()
    finally:
        parent_connection.close()
    if status == "error":
        raise ProcessExecutionError(
            f"{payload['type']}: {payload['message']}\n{payload['traceback']}"
        )
    if status != "ok":
        raise ProcessExecutionError(f"unknown child result status: {status}")
    return payload


def _validate_worker_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ProcessExecutionError("worker payload must be an object")
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ProcessExecutionError("worker payload is not stable JSON") from exc
    for field in ("answer", "final_state", "trace", "metrics"):
        if field not in payload:
            raise ProcessExecutionError(f"worker payload is missing {field}")
    if not isinstance(payload["answer"], str):
        raise ProcessExecutionError("worker answer must be a string")
    if not isinstance(payload["final_state"], dict):
        raise ProcessExecutionError("worker final_state must be an object")
    if not isinstance(payload["trace"], list) or any(
        not isinstance(item, dict) for item in payload["trace"]
    ):
        raise ProcessExecutionError("worker trace must be a list of objects")
    trace_fields = {
        "model_request": ("request_id", "model"),
        "model_response": ("request_id", "response_id", "status"),
        "tool_call": ("call_id", "name"),
        "tool_result": ("call_id", "name", "status"),
    }
    for index, item in enumerate(payload["trace"]):
        item_type = item.get("type")
        if item_type not in trace_fields:
            raise ProcessExecutionError(f"worker trace item {index} has unsupported type")
        for field in trace_fields[item_type]:
            value = item.get(field)
            if not isinstance(value, str) or not value:
                raise ProcessExecutionError(
                    f"worker trace item {index} is missing non-empty {field}"
                )
            if field.endswith("id") and Path(value).name != value:
                raise ProcessExecutionError(f"worker trace item {index} has unsafe {field}")
        if item_type == "model_request":
            attempt_index = item.get("attempt_index", 0)
            if (
                isinstance(attempt_index, bool)
                or not isinstance(attempt_index, int)
                or attempt_index < 0
            ):
                raise ProcessExecutionError(f"worker trace item {index} has invalid attempt_index")
    metrics = payload["metrics"]
    if not isinstance(metrics, dict):
        raise ProcessExecutionError("worker metrics must be an object")
    for field in ("model_requests", "tool_invocations"):
        value = metrics.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProcessExecutionError(f"worker metrics.{field} must be non-negative integer")
    elapsed = metrics.get("elapsed_ms")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(elapsed)
        or elapsed < 0
    ):
        raise ProcessExecutionError(
            "worker metrics.elapsed_ms must be a finite non-negative number"
        )


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task["id"],
        "kind": task["kind"],
        "prompt": task["prompt"],
        "inputs": copy.deepcopy(task.get("inputs", {})),
    }


def _append_trace_events(
    journal: EventWriter,
    trace: list[dict[str, Any]],
    raw_dir: Path,
) -> list[int]:
    sequence_refs: list[int] = []
    for item in trace:
        item_type = item.get("type")
        if item_type == "model_request":
            request_id = _require_text(item.get("request_id"), "trace request_id")
            ref = f"raw/requests/{request_id}.json"
            _write_json(raw_dir.parent / ref, item)
            event = journal.append(
                "model_request",
                {
                    "request_id": request_id,
                    "model": _require_text(item.get("model"), "trace model"),
                    "request_ref": ref,
                    "attempt_index": item.get("attempt_index", 0),
                },
                source="worker",
            )
        elif item_type == "model_response":
            response_id = _require_text(item.get("response_id"), "trace response_id")
            ref = f"raw/responses/{response_id}.json"
            _write_json(raw_dir.parent / ref, item)
            event = journal.append(
                "model_response",
                {
                    "request_id": _require_text(item.get("request_id"), "trace request_id"),
                    "response_id": response_id,
                    "response_ref": ref,
                    "status": _require_text(item.get("status"), "trace response status"),
                },
                source="worker",
            )
        elif item_type == "tool_call":
            call_id = _require_text(item.get("call_id"), "trace call_id")
            ref = f"raw/arguments/{call_id}.json"
            _write_json(raw_dir.parent / ref, item.get("arguments", {}))
            event = journal.append(
                "tool_call",
                {
                    "call_id": call_id,
                    "name": _require_text(item.get("name"), "trace tool name"),
                    "arguments_ref": ref,
                },
                source="worker",
            )
        elif item_type == "tool_result":
            call_id = _require_text(item.get("call_id"), "trace result call_id")
            ref = f"raw/results/{call_id}.json"
            _write_json(raw_dir.parent / ref, item.get("result", {}))
            status = _require_text(item.get("status"), "trace result status")
            event = journal.append(
                "tool_result",
                {"call_id": call_id, "status": status, "result_ref": ref},
                source="worker",
            )
            journal.append(
                "interface_action",
                {
                    "action_id": call_id,
                    "operation": _require_text(item.get("name", "tool"), "trace operation"),
                    "status": status,
                    "call_id": call_id,
                },
                source="worker",
            )
            if item.get("name") == "snapshot":
                journal.append(
                    "observation",
                    {
                        "observation_id": f"observation-{call_id}",
                        "channel": "dom",
                        "artifact_ref": ref,
                        "visible_to_agent": True,
                    },
                    source="worker",
                )
        else:
            raise TrajectoryRunError(f"unknown mock trace item: {item_type!r}")
        sequence_refs.append(event["seq"])
    return sequence_refs


def _base_result(
    schedule_item: dict[str, Any],
    autonomy: str,
    *,
    requested_model: str = "gpt-5.6-luna",
    provider: str = "stub",
    returned_model: str = "mock-luna",
) -> dict[str, Any]:
    return {
        **schedule_item,
        "model": returned_model,
        "outcome": "infrastructure_failure",
        "verdict": None,
        "autonomy": autonomy,
        "model_requests": 0,
        "tool_invocations": 0,
        "elapsed_ms": None,
        "provider_calls": 0,
        "requested_model": requested_model,
        "provider": provider,
        "returned_model": returned_model,
        "cost_usd": 0.0,
        "usage": [],
        "configuration": {},
        "error": None,
    }


def run_attempt(
    *,
    task: dict[str, Any],
    schedule_item: dict[str, Any],
    output_dir: str | Path,
    timeout_seconds: float,
    intervention: dict[str, Any] | None = None,
    attempt_function: Callable[..., Any] = run_mock_attempt,
    requested_model: str = "gpt-5.6-luna",
    provider: str = "stub",
    returned_model: str = "mock-luna",
) -> dict[str, Any]:
    """Run one isolated attempt and retain every scheduled attempt outcome."""

    attempt_dir = Path(output_dir)
    raw_dir = attempt_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    (raw_dir / "requests").mkdir()
    (raw_dir / "responses").mkdir()
    (raw_dir / "arguments").mkdir()
    (raw_dir / "results").mkdir()
    attempt_manifest = {
        "attempt_id": schedule_item["attempt_id"],
        "task_id": task["id"],
        "arm": schedule_item["arm"],
        "repetition": schedule_item["repetition"],
        "phase": schedule_item.get("phase", "measured"),
        "requested_model": requested_model,
        "provider": provider,
        "returned_model": returned_model,
        "public_task": _public_task(task),
        "timeout_seconds": timeout_seconds,
    }
    _write_json(attempt_dir / "manifest.json", attempt_manifest)
    _write_json(attempt_dir / "initial-state.json", state_view(task["initial_state"]))
    (attempt_dir / "stderr.txt").write_text("", encoding="utf-8")
    run_id = str(uuid.uuid4())
    autonomy = "assisted" if intervention else "autonomous"
    result = _base_result(
        schedule_item,
        autonomy,
        requested_model=requested_model,
        provider=provider,
        returned_model=returned_model,
    )
    journal_path = attempt_dir / "events.jsonl"

    with EventWriter(journal_path, run_id) as journal:
        journal.append("run_started", {"manifest_ref": "manifest.json"})
        journal.append("phase_started", {"phase": "setup"})
        initial_event = journal.append(
            "observation",
            {
                "observation_id": "initial-state",
                "channel": "environment",
                "artifact_ref": "initial-state.json",
                "visible_to_agent": False,
            },
        )
        journal.append("phase_finished", {"phase": "setup", "status": "completed"})
        journal.append("phase_started", {"phase": "agent"})
        if intervention:
            journal.append(
                "intervention",
                {
                    "intervention_id": intervention.get(
                        "id", f"intervention-{schedule_item['attempt_id']}"
                    ),
                    "actor": intervention.get("actor", "human"),
                    "action": intervention["action"],
                    "reason": intervention.get("reason", "recorded test intervention"),
                    "phase": "agent",
                    "affected_run_id": run_id,
                },
                source="supervisor",
            )
        try:
            payload = run_in_process(
                attempt_function,
                _public_task(task),
                schedule_item["arm"],
                copy.deepcopy(task["initial_state"]),
                timeout_seconds=timeout_seconds,
            )
            _validate_worker_payload(payload)
        except ProcessTimeoutError as exc:
            message = str(exc)
            (attempt_dir / "stderr.txt").write_text(message + "\n", encoding="utf-8")
            journal.append(
                "error",
                {
                    "component": "attempt_process",
                    "code": "timeout",
                    "message": message,
                    "recoverable": False,
                },
            )
            _write_json(
                attempt_dir / "answer.json",
                {"available": False, "reason": "timeout"},
            )
            _write_json(
                attempt_dir / "evaluation.json",
                {
                    "task_id": task["id"],
                    "evaluator_version": EVALUATOR_VERSION,
                    "specification": copy.deepcopy(task["evaluation"]),
                    "status": "not_run",
                    "reason": "timeout",
                },
            )
            journal.append("phase_finished", {"phase": "agent", "status": "timeout"})
            journal.append(
                "run_finished",
                {
                    "termination_reason": "timeout",
                    "outcome": "timeout",
                    "autonomy": autonomy,
                    "trace_complete": False,
                },
            )
            result.update({"outcome": "timeout", "error": message})
            _write_json(attempt_dir / "final-state.json", {"available": False, "reason": "timeout"})
            read_events(journal_path)
            return result
        except ProcessExecutionError as exc:
            message = str(exc)
            (attempt_dir / "stderr.txt").write_text(message + "\n", encoding="utf-8")
            journal.append(
                "error",
                {
                    "component": "attempt_process",
                    "code": "process_error",
                    "message": message,
                    "recoverable": False,
                },
            )
            _write_json(
                attempt_dir / "answer.json",
                {"available": False, "reason": "process_error"},
            )
            _write_json(
                attempt_dir / "evaluation.json",
                {
                    "task_id": task["id"],
                    "evaluator_version": EVALUATOR_VERSION,
                    "specification": copy.deepcopy(task["evaluation"]),
                    "status": "not_run",
                    "reason": "process_error",
                },
            )
            journal.append("phase_finished", {"phase": "agent", "status": "error"})
            journal.append(
                "run_finished",
                {
                    "termination_reason": "process_error",
                    "outcome": "infrastructure_failure",
                    "autonomy": autonomy,
                    "trace_complete": False,
                },
            )
            result["error"] = message
            _write_json(
                attempt_dir / "final-state.json", {"available": False, "reason": "process_error"}
            )
            read_events(journal_path)
            return result

        _write_json(raw_dir / "trace.json", payload["trace"])
        if "usage" in payload:
            usage = payload["usage"]
            _write_json(raw_dir / "usage.json", usage)
            if isinstance(usage, list):
                input_total = sum(
                    item.get("usage", {}).get("input_tokens", 0)
                    for item in usage
                    if isinstance(item, dict) and isinstance(item.get("usage"), dict)
                )
                output_total = sum(
                    item.get("usage", {}).get("output_tokens", 0)
                    for item in usage
                    if isinstance(item, dict) and isinstance(item.get("usage"), dict)
                )
            else:
                input_total = output_total = 0
            journal.append(
                "usage_reported",
                {
                    "measurement_id": f"{schedule_item['attempt_id']}-usage",
                    "scope": "session",
                    "basis": "delta",
                    "values": {
                        "input_tokens": input_total,
                        "output_tokens": output_total,
                        "cost_usd": payload.get("cost_usd", 0.0),
                    },
                    "coverage": "complete",
                },
                source="worker",
            )
        _write_json(
            attempt_dir / "answer.json",
            {"available": True, "answer": payload["answer"]},
        )
        _write_json(attempt_dir / "final-state.json", payload["final_state"])
        sequence_refs = _append_trace_events(journal, payload["trace"], raw_dir)
        result.update(
            {
                "model_requests": payload["metrics"]["model_requests"],
                "tool_invocations": payload["metrics"]["tool_invocations"],
                "elapsed_ms": payload["metrics"]["elapsed_ms"],
                "provider_calls": payload["metrics"].get(
                    "provider_calls", payload["metrics"]["model_requests"]
                )
                if provider != "stub"
                else 0,
                "requested_model": requested_model,
                "provider": payload.get("provider", provider),
                "returned_model": payload.get("returned_model", returned_model),
                "cost_usd": payload.get("cost_usd", 0.0),
                "usage": payload.get("usage", []),
                "configuration": payload.get("configuration", {}),
            }
        )
        attempt_manifest.update(
            {
                "provider": result["provider"],
                "returned_model": result["returned_model"],
                "usage": result["usage"],
                "cost_usd": result["cost_usd"],
                "configuration": result["configuration"],
            }
        )
        _write_json(attempt_dir / "manifest.json", attempt_manifest)
        journal.append("phase_finished", {"phase": "agent", "status": "completed"})
        journal.append("phase_started", {"phase": "evaluation"})
        initial_state = state_view(task["initial_state"])
        try:
            evaluation = evaluate_task(
                task,
                initial_state,
                payload["final_state"],
                payload["answer"],
                payload["trace"],
            )
            evaluation_result = evaluation.to_dict()
            evaluation_data = {
                "task_id": task["id"],
                "evaluator_version": EVALUATOR_VERSION,
                "specification": copy.deepcopy(task["evaluation"]),
                "result": evaluation_result,
            }
            _write_json(attempt_dir / "evaluation.json", evaluation_data)
            evaluator_event = journal.append(
                "evaluator_result",
                {
                    "evaluator_version": EVALUATOR_VERSION,
                    "verdict": evaluation.verdict,
                    "checks": evaluation_result["checks"],
                    "evidence_refs": [
                        initial_event["seq"],
                        *(sequence_refs or [initial_event["seq"]]),
                    ],
                },
                source="evaluator",
            )
            del evaluator_event
            journal.append("phase_finished", {"phase": "evaluation", "status": "completed"})
            outcome = "pass" if evaluation.passed else "task_failure"
            result.update(
                {
                    "outcome": outcome,
                    "verdict": evaluation.verdict,
                }
            )
        except (EvaluatorError, KeyError, TypeError, ValueError) as exc:
            message = f"{type(exc).__name__}: {exc}"
            (attempt_dir / "stderr.txt").write_text(message + "\n", encoding="utf-8")
            _write_json(
                attempt_dir / "evaluation.json",
                {
                    "task_id": task["id"],
                    "evaluator_version": EVALUATOR_VERSION,
                    "specification": copy.deepcopy(task["evaluation"]),
                    "status": "error",
                    "error": message,
                },
            )
            journal.append(
                "error",
                {
                    "component": "evaluator",
                    "code": "evaluator_error",
                    "message": message,
                    "recoverable": False,
                },
                source="evaluator",
            )
            journal.append("phase_finished", {"phase": "evaluation", "status": "error"})
            result["error"] = message
            result["outcome"] = "evaluator_error"
        journal.append(
            "run_finished",
            {
                "termination_reason": (
                    "completed"
                    if result["outcome"] in {"pass", "task_failure", "evaluator_error"}
                    else result["outcome"]
                ),
                "outcome": result["outcome"],
                "autonomy": autonomy,
                "trace_complete": True,
            },
        )

    read_events(journal_path)
    return result


def _write_results(output_path: Path, results: list[dict[str, Any]]) -> None:
    with (output_path / "results.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "outcomes": {
            outcome: sum(result["outcome"] == outcome for result in results)
            for outcome in sorted({result["outcome"] for result in results})
        },
        "passed": sum(result["verdict"] == "pass" for result in results),
        "autonomous_pass": sum(
            result["verdict"] == "pass" and result["autonomy"] == "autonomous"
            for result in results
        ),
        "assisted_pass": sum(
            result["verdict"] == "pass" and result["autonomy"] == "assisted"
            for result in results
        ),
        "calibration": {
            "attempts": sum(result.get("phase") == "calibration" for result in results),
            "passed": sum(
                result.get("phase") == "calibration" and result["verdict"] == "pass"
                for result in results
            ),
        },
        "measured": {
            "attempts": sum(result.get("phase", "measured") == "measured" for result in results),
            "passed": sum(
                result.get("phase", "measured") == "measured" and result["verdict"] == "pass"
                for result in results
            ),
        },
    }


def _live_schedule(
    experiment: dict[str, Any], tasks: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    calibration_count = experiment.get("calibration_attempts", 2)
    if (
        isinstance(calibration_count, bool)
        or not isinstance(calibration_count, int)
        or calibration_count not in {0, 2}
    ):
        raise TrajectoryRunError("live calibration_attempts must be 0 or 2")
    calibration_task_id = experiment.get("calibration_task_id")
    task_by_id = {task["id"]: task for task in tasks}
    if calibration_count:
        if not isinstance(calibration_task_id, str) or not calibration_task_id:
            raise TrajectoryRunError("live experiment calibration_task_id is required")
        if calibration_task_id not in task_by_id:
            raise TrajectoryRunError(
                f"calibration task is not in the experiment: {calibration_task_id}"
            )

    calibration = []
    if calibration_count:
        calibration = [
            {
                "attempt_index": 1,
                "attempt_id": "calibration-001",
                "task_id": calibration_task_id,
                "arm": "dom",
                "repetition": 0,
                "pair_order": 1,
                "phase": "calibration",
            },
            {
                "attempt_index": 2,
                "attempt_id": "calibration-002",
                "task_id": calibration_task_id,
                "arm": "webmcp",
                "repetition": 0,
                "pair_order": 2,
                "phase": "calibration",
            },
        ]
    measured = []
    for item in build_schedule(experiment, tasks):
        measured.append(
            {
                **item,
                "attempt_index": item["attempt_index"] + len(calibration),
                "attempt_id": f"attempt-{item['attempt_index'] + len(calibration):03d}",
                "phase": "measured",
            }
        )
    return calibration, measured


def _update_live_manifest(
    manifest: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    stopped_reason: str | None = None,
) -> None:
    known_cost = sum(float(result.get("cost_usd", 0.0) or 0.0) for result in results)
    known_calls = sum(int(result.get("provider_calls", 0) or 0) for result in results)
    accounting_complete = not any(
        result["outcome"] in {"infrastructure_failure", "timeout"} for result in results
    )
    manifest["completed_attempts"] = len(results)
    manifest["provider_calls"] = known_calls
    manifest["cost_usd"] = round(known_cost, 9)
    returned_models = {
        result["returned_model"]
        for result in results
        if isinstance(result.get("returned_model"), str)
        and result["returned_model"] not in {"", "pending", "mock-luna"}
    }
    if len(returned_models) == 1:
        manifest["returned_model"] = next(iter(returned_models))
    manifest["cost_accounting"] = "complete" if accounting_complete else "unknown_after_failure"
    manifest["usage"] = {
        "visibility": "response.usage",
        "coverage": "complete" if accounting_complete else "partial",
    }
    manifest["summary"] = _summary(results)
    if stopped_reason:
        manifest["stopped_reason"] = stopped_reason


def run_live_experiment(
    experiment_path: str | Path,
    output: str | Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the explicitly capped live pilot through the OpenAI Responses API."""

    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(f"output directory already exists: {output_path}")
    experiment, tasks = load_experiment(experiment_path)
    if experiment["model"] != "gpt-5.6-luna":
        raise TrajectoryRunError("the capped live pilot is pinned to gpt-5.6-luna")
    budgets = experiment.get("budgets", {})
    expected_budgets = {
        "max_model_requests": 10,
        "max_tool_invocations": 60,
        "max_input_tokens": 20_000,
        "max_output_tokens": 4_096,
        "provider_retries": 0,
        "reasoning_effort": "none",
        "cost_ceiling_usd": 1.0,
        "max_attempt_cost_usd": 0.09,
    }
    for field, expected in expected_budgets.items():
        if budgets.get(field) != expected:
            raise TrajectoryRunError(
                f"capped live pilot requires budgets.{field}={expected!r}"
            )
    calibration, measured = _live_schedule(experiment, tasks)
    schedule = calibration + measured
    if len(schedule) > 10:
        raise TrajectoryRunError("capped live pilot exceeds ten total attempts")

    output_path.mkdir(parents=True, exist_ok=False)
    started_at = _now()
    manifest: dict[str, Any] = {
        "schema_version": "agent-eval.manifest/1",
        "run_id": str(uuid.uuid4()),
        "experiment_id": experiment["id"],
        "mode": "live-capped-pilot",
        "model": experiment["model"],
        "requested_model": experiment["model"],
        "provider": "openai-responses",
        "returned_model": "pending",
        "environment": experiment.get("environment", "shop-fixture-v1"),
        "interfaces": experiment["interfaces"],
        "repetitions": experiment["repetitions"],
        "randomization_seed": experiment["randomization_seed"],
        "budgets": experiment["budgets"],
        "started_at": started_at,
        "tasks": [{"id": task["id"], "prompt": task["prompt"]} for task in tasks],
        "scheduled_attempts": len(schedule),
        "calibration_attempts": len(calibration),
        "measured_attempts": len(measured),
        "provider_calls": 0,
        "cost_usd": 0.0,
        "cost_accounting": "pending",
        "repo_revision": "unknown",
        "dirty_diff_hash": "unknown",
        "reasoning_configuration": {"effort": "none"},
        "usage": {"visibility": "response.usage", "coverage": "pending"},
        "pricing": {
            "status": "frozen_for_run",
            "source": "OpenAI gpt-5.6-luna model documentation",
            "retrieved_at": "2026-09-20",
            "input_per_million_usd": budgets["input_price_per_million_usd"],
            "cached_input_per_million_usd": budgets["cached_input_price_per_million_usd"],
            "cache_write_per_million_usd": budgets["cache_write_price_per_million_usd"],
            "output_per_million_usd": budgets["output_price_per_million_usd"],
        },
        "retry_policy": {"provider_retries": 0, "hidden_retries": False},
    }
    _write_json(output_path / "manifest.json", manifest)
    _write_json(
        output_path / "schedule.json",
        {
            "randomization_seed": experiment["randomization_seed"],
            "calibration": calibration,
            "measured": measured,
            "attempts": schedule,
        },
    )

    task_by_id = {task["id"]: task for task in tasks}
    results: list[dict[str, Any]] = []
    timeout_seconds = float(experiment["budgets"]["attempt_timeout_seconds"])
    stopped_reason = None
    calibration_results: list[dict[str, Any]] = []
    for item in schedule:
        if sum(float(result.get("cost_usd", 0.0) or 0.0) for result in results) >= 1.0:
            stopped_reason = "total_cost_ceiling_reached"
            break
        attempt_dir = output_path / "attempts" / item["attempt_id"]
        attempt_dir.parent.mkdir(parents=True, exist_ok=True)
        result = run_attempt(
            task=task_by_id[item["task_id"]],
            schedule_item=item,
            output_dir=attempt_dir,
            timeout_seconds=timeout_seconds,
            attempt_function=run_live_attempt,
            requested_model=experiment["model"],
            provider="openai-responses",
            returned_model="pending",
        )
        results.append(result)
        _write_results(output_path, results)
        _update_live_manifest(manifest, results)
        _write_json(output_path / "manifest.json", manifest)
        print(
            f"{item['attempt_id']:<16} {item['task_id']:<14} "
            f"{item['arm']:<6} {result['outcome']} "
            f"cost={result.get('cost_usd', 0.0):.6f}"
        )

        if item["phase"] == "calibration":
            calibration_results.append(result)
            if result["outcome"] in {"infrastructure_failure", "timeout", "evaluator_error"}:
                stopped_reason = f"calibration_{result['outcome']}"
                break
            if len(calibration_results) == len(calibration):
                if any(item_result["verdict"] != "pass" for item_result in calibration_results):
                    stopped_reason = "calibration_failed"
                    break
        elif result["outcome"] in {"infrastructure_failure", "timeout", "evaluator_error"}:
            stopped_reason = f"measured_{result['outcome']}"
            break

    _update_live_manifest(manifest, results, stopped_reason=stopped_reason)
    manifest["finished_at"] = _now()
    _write_results(output_path, results)
    _write_json(output_path / "manifest.json", manifest)
    write_report(output_path / "report.md", manifest, results)
    return manifest, results


def run_experiment(
    experiment_path: str | Path,
    output: str | Path,
    *,
    mock: bool = False,
    live: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the offline matrix or the explicitly capped live pilot."""

    if mock and live:
        raise TrajectoryRunError("choose exactly one of --mock or --live")
    if live:
        return run_live_experiment(experiment_path, output)
    if not mock:
        raise TrajectoryRunError("choose --mock or --live")

    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(f"output directory already exists: {output_path}")
    experiment, tasks = load_experiment(experiment_path)
    output_path.mkdir(parents=True, exist_ok=False)
    schedule = build_schedule(experiment, tasks)
    started_at = _now()
    manifest: dict[str, Any] = {
        "schema_version": "agent-eval.manifest/1",
        "run_id": str(uuid.uuid4()),
        "experiment_id": experiment["id"],
        "mode": "mock",
        "model": experiment["model"],
        "requested_model": experiment["model"],
        "provider": "stub",
        "returned_model": "mock-luna",
        "environment": experiment.get("environment", "shop-fixture-v1"),
        "interfaces": experiment["interfaces"],
        "repetitions": experiment["repetitions"],
        "randomization_seed": experiment["randomization_seed"],
        "budgets": experiment["budgets"],
        "started_at": started_at,
        "tasks": [{"id": task["id"], "prompt": task["prompt"]} for task in tasks],
        "scheduled_attempts": len(schedule),
        "provider_calls": 0,
        "repo_revision": "unknown",
        "dirty_diff_hash": "unknown",
        "reasoning_configuration": "unknown",
        "usage": {"visibility": "unavailable", "coverage": "unavailable"},
        "pricing": {"status": "unknown", "reason": "mock mode has no invoice"},
        "retry_policy": {"provider_retries": 0, "hidden_retries": False},
    }
    _write_json(output_path / "manifest.json", manifest)
    _write_json(
        output_path / "schedule.json",
        {"randomization_seed": experiment["randomization_seed"], "attempts": schedule},
    )
    task_by_id = {task["id"]: task for task in tasks}
    results: list[dict[str, Any]] = []
    timeout_seconds = float(experiment["budgets"]["attempt_timeout_seconds"])
    for item in schedule:
        attempt_dir = output_path / "attempts" / item["attempt_id"]
        attempt_dir.parent.mkdir(parents=True, exist_ok=True)
        result = run_attempt(
            task=task_by_id[item["task_id"]],
            schedule_item=item,
            output_dir=attempt_dir,
            timeout_seconds=timeout_seconds,
        )
        results.append(result)
        print(
            f"{item['attempt_id']:<16} {item['task_id']:<14} "
            f"{item['arm']:<6} {result['outcome']}"
        )

    _write_results(output_path, results)
    manifest["finished_at"] = _now()
    manifest["summary"] = _summary(results)
    _write_json(output_path / "manifest.json", manifest)
    write_report(output_path / "report.md", manifest, results)
    return manifest, results


__all__ = [
    "ProcessExecutionError",
    "ProcessTimeoutError",
    "TrajectoryRunError",
    "build_schedule",
    "load_experiment",
    "run_attempt",
    "run_experiment",
    "run_live_experiment",
    "run_in_process",
]
