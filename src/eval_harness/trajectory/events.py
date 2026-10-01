"""Validated, append-only JSONL events for agent evaluation runs."""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "agent-eval.events/1"

EVENT_KINDS = frozenset(
    {
        "run_started",
        "phase_started",
        "phase_finished",
        "model_request",
        "model_response",
        "tool_call",
        "tool_result",
        "interface_action",
        "observation",
        "usage_reported",
        "retry",
        "heartbeat",
        "error",
        "intervention",
        "policy_decision",
        "evaluator_result",
        "run_finished",
    }
)

_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "event_id",
        "seq",
        "timestamp",
        "elapsed_ns",
        "source",
        "kind",
        "span_id",
        "parent_span_id",
        "source_event_id",
        "evidence_seq",
        "data",
    }
)
_DURABILITY_KINDS = frozenset(
    {"run_started", "phase_started", "phase_finished", "run_finished"}
)
_USAGE_SCOPES = frozenset({"request", "turn", "session"})
_USAGE_BASES = frozenset({"delta", "cumulative"})
_USAGE_COVERAGE = frozenset({"complete", "partial", "unavailable"})
_POLICY_ASSESSMENTS = frozenset(
    {"progressing", "inspection_only", "waiting", "blocked", "inactive", "indeterminate"}
)
_INTERVENTION_ACTIONS = frozenset(
    {
        "clarify",
        "correct",
        "approve",
        "manual_action",
        "extend_budget",
        "switch_interface",
        "switch_model",
        "interrupt",
        "resume",
    }
)


class EventValidationError(ValueError):
    """Raised when an event or journal lifecycle violates the schema."""


class EventReadError(EventValidationError):
    """Raised when a JSONL journal cannot be read as a valid event stream."""


class TruncatedEventError(EventReadError):
    """Raised when the final JSONL record has no terminating newline."""


class EventWriteError(RuntimeError):
    """Raised after an event cannot be durably written."""


class EventWriterClosed(RuntimeError):
    """Raised when an append is attempted after the writer is closed."""


class EventWriterPoisoned(RuntimeError):
    """Raised when an append is attempted after a write failure."""


def _canonical_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise EventValidationError(f"{field} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise EventValidationError(f"{field} is not a valid UUID: {value!r}") from exc
    canonical = str(parsed)
    if value.lower() != canonical:
        raise EventValidationError(f"{field} must use canonical UUID form")
    return canonical


def _normalize_uuid(value: str | uuid.UUID, field: str) -> str:
    if isinstance(value, uuid.UUID):
        value = str(value)
    return _canonical_uuid(value, field)


def _require_string(data: Mapping[str, Any], name: str, kind: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise EventValidationError(f"{kind}.data.{name} must be a non-empty string")
    return value


def _require_ref(data: Mapping[str, Any], name: str, kind: str) -> Any:
    if name not in data or data[name] is None:
        raise EventValidationError(f"{kind}.data.{name} is required")
    value = data[name]
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping) and value:
        return value
    raise EventValidationError(f"{kind}.data.{name} must be a non-empty reference")


def _require_non_negative_int(data: Mapping[str, Any], name: str, kind: str) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EventValidationError(f"{kind}.data.{name} must be a non-negative integer")
    return value


def _require_positive_int(data: Mapping[str, Any], name: str, kind: str) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EventValidationError(f"{kind}.data.{name} must be a positive integer")
    return value


def _require_non_negative_number(data: Mapping[str, Any], name: str, kind: str) -> float:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EventValidationError(f"{kind}.data.{name} must be a non-negative number")
    if isinstance(value, float) and not math.isfinite(value):
        raise EventValidationError(f"{kind}.data.{name} must be finite")
    if value < 0:
        raise EventValidationError(f"{kind}.data.{name} must be a non-negative number")
    return value


def _require_list_of_positive_ints(
    data: Mapping[str, Any], name: str, kind: str
) -> list[int]:
    value = data.get(name)
    if not isinstance(value, list):
        raise EventValidationError(f"{kind}.data.{name} must be a list")
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in value):
        raise EventValidationError(f"{kind}.data.{name} must contain positive integers")
    if len(value) != len(set(value)):
        raise EventValidationError(f"{kind}.data.{name} must not contain duplicates")
    return value


def _validate_utf8_string(value: str, path: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EventValidationError(
            f"{path} contains a string that cannot be UTF-8 encoded"
        ) from exc


def _validate_json_value(
    value: Any, path: str = "data", ancestors: set[int] | None = None
) -> None:
    """Reject Python values that are not stable JSON values."""

    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        _validate_utf8_string(value, path)
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EventValidationError(f"{path} contains a non-finite number")
        return
    if isinstance(value, (list, dict)):
        ancestors = ancestors or set()
        identity = id(value)
        if identity in ancestors:
            raise EventValidationError(f"{path} contains a recursive container")
        ancestors.add(identity)
        try:
            if isinstance(value, list):
                for index, item in enumerate(value):
                    _validate_json_value(item, f"{path}[{index}]", ancestors)
            else:
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise EventValidationError(f"{path} contains a non-string object key")
                    _validate_utf8_string(key, f"{path}.{key}")
                    _validate_json_value(item, f"{path}.{key}", ancestors)
        finally:
            ancestors.remove(identity)
        return
    raise EventValidationError(f"{path} contains unsupported value {type(value).__name__}")


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise EventValidationError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventValidationError(f"timestamp is invalid: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EventValidationError("timestamp must include UTC timezone information")


def _validate_payload(kind: str, data: Mapping[str, Any]) -> None:
    if kind == "run_started":
        _require_ref(data, "manifest_ref", kind)
    elif kind == "phase_started":
        _require_string(data, "phase", kind)
    elif kind == "phase_finished":
        _require_string(data, "phase", kind)
        _require_string(data, "status", kind)
    elif kind == "model_request":
        _require_string(data, "request_id", kind)
        _require_string(data, "model", kind)
        _require_ref(data, "request_ref", kind)
        _require_non_negative_int(data, "attempt_index", kind)
    elif kind == "model_response":
        _require_string(data, "request_id", kind)
        _require_string(data, "response_id", kind)
        _require_ref(data, "response_ref", kind)
        _require_string(data, "status", kind)
    elif kind == "tool_call":
        _require_string(data, "call_id", kind)
        _require_string(data, "name", kind)
        _require_ref(data, "arguments_ref", kind)
    elif kind == "tool_result":
        _require_string(data, "call_id", kind)
        _require_string(data, "status", kind)
        _require_ref(data, "result_ref", kind)
    elif kind == "interface_action":
        _require_string(data, "action_id", kind)
        _require_string(data, "operation", kind)
        _require_string(data, "status", kind)
        if "call_id" in data:
            _require_string(data, "call_id", kind)
    elif kind == "observation":
        _require_string(data, "observation_id", kind)
        _require_string(data, "channel", kind)
        _require_ref(data, "artifact_ref", kind)
        if not isinstance(data.get("visible_to_agent"), bool):
            raise EventValidationError(f"{kind}.data.visible_to_agent must be a boolean")
    elif kind == "usage_reported":
        _require_string(data, "measurement_id", kind)
        scope = _require_string(data, "scope", kind)
        basis = _require_string(data, "basis", kind)
        coverage = _require_string(data, "coverage", kind)
        if scope not in _USAGE_SCOPES:
            raise EventValidationError(f"usage_reported.data.scope is invalid: {scope!r}")
        if basis not in _USAGE_BASES:
            raise EventValidationError(f"usage_reported.data.basis is invalid: {basis!r}")
        if coverage not in _USAGE_COVERAGE:
            raise EventValidationError(f"usage_reported.data.coverage is invalid: {coverage!r}")
        if not isinstance(data.get("values"), dict):
            raise EventValidationError("usage_reported.data.values must be an object")
    elif kind == "retry":
        _require_string(data, "scope", kind)
        _require_string(data, "operation_id", kind)
        _require_non_negative_int(data, "attempt_index", kind)
        _require_string(data, "reason", kind)
        _require_non_negative_number(data, "delay_ms", kind)
    elif kind == "heartbeat":
        _require_string(data, "process_state", kind)
        outstanding = data.get("outstanding_operation_ids")
        if not isinstance(outstanding, list) or any(
            not isinstance(item, str) or not item for item in outstanding
        ):
            raise EventValidationError(
                "heartbeat.data.outstanding_operation_ids must be a list of strings"
            )
    elif kind == "error":
        _require_string(data, "component", kind)
        _require_string(data, "code", kind)
        _require_string(data, "message", kind)
        if not isinstance(data.get("recoverable"), bool):
            raise EventValidationError(f"{kind}.data.recoverable must be a boolean")
    elif kind == "intervention":
        _require_string(data, "intervention_id", kind)
        _require_string(data, "actor", kind)
        action = _require_string(data, "action", kind)
        _require_string(data, "reason", kind)
        _require_string(data, "phase", kind)
        _require_string(data, "affected_run_id", kind)
        if action not in _INTERVENTION_ACTIONS:
            raise EventValidationError(f"intervention.data.action is invalid: {action!r}")
    elif kind == "policy_decision":
        _require_string(data, "policy_version", kind)
        _require_positive_int(data, "through_seq", kind)
        assessment = _require_string(data, "assessment", kind)
        _require_string(data, "proposed_action", kind)
        if assessment not in _POLICY_ASSESSMENTS:
            raise EventValidationError(
                f"policy_decision.data.assessment is invalid: {assessment!r}"
            )
        if not isinstance(data.get("applied"), bool):
            raise EventValidationError(f"{kind}.data.applied must be a boolean")
    elif kind == "evaluator_result":
        _require_string(data, "evaluator_version", kind)
        _require_string(data, "verdict", kind)
        if not isinstance(data.get("checks"), (dict, list)):
            raise EventValidationError(f"{kind}.data.checks must be an object or list")
        _require_list_of_positive_ints(data, "evidence_refs", kind)
    elif kind == "run_finished":
        _require_string(data, "termination_reason", kind)
        _require_string(data, "outcome", kind)
        _require_string(data, "autonomy", kind)
        if not isinstance(data.get("trace_complete"), bool):
            raise EventValidationError(f"{kind}.data.trace_complete must be a boolean")


def validate_event(event: Mapping[str, Any]) -> None:
    """Validate one event without applying journal ordering or lifecycle state."""

    if not isinstance(event, Mapping):
        raise EventValidationError("event must be an object")
    missing = _ENVELOPE_FIELDS - event.keys()
    extra = event.keys() - _ENVELOPE_FIELDS
    if missing:
        raise EventValidationError(f"event is missing fields: {sorted(missing)}")
    if extra:
        raise EventValidationError(f"event has unknown fields: {sorted(extra)}")
    if event["schema_version"] != SCHEMA_VERSION:
        raise EventValidationError("schema_version is not supported")
    _canonical_uuid(event["run_id"], "run_id")
    _canonical_uuid(event["event_id"], "event_id")
    if isinstance(event["seq"], bool) or not isinstance(event["seq"], int) or event["seq"] < 1:
        raise EventValidationError("seq must be a positive integer")
    _validate_timestamp(event["timestamp"])
    if (
        isinstance(event["elapsed_ns"], bool)
        or not isinstance(event["elapsed_ns"], int)
        or event["elapsed_ns"] < 0
    ):
        raise EventValidationError("elapsed_ns must be a non-negative integer")
    if not isinstance(event["source"], str) or not event["source"]:
        raise EventValidationError("source must be a non-empty string")
    _validate_utf8_string(event["source"], "source")
    kind = event["kind"]
    if not isinstance(kind, str) or kind not in EVENT_KINDS:
        raise EventValidationError(f"unknown event kind: {kind!r}")
    for field in ("span_id", "parent_span_id"):
        if event[field] is not None and (
            not isinstance(event[field], str) or not event[field]
        ):
            raise EventValidationError(f"{field} must be null or a non-empty string")
        if event[field] is not None:
            _validate_utf8_string(event[field], field)
    if event["source_event_id"] is not None:
        _canonical_uuid(event["source_event_id"], "source_event_id")
    evidence_seq = event["evidence_seq"]
    if not isinstance(evidence_seq, list):
        raise EventValidationError("evidence_seq must be a list")
    if any(
        isinstance(item, bool)
        or not isinstance(item, int)
        or item < 1
        or item >= event["seq"]
        for item in evidence_seq
    ):
        raise EventValidationError("evidence_seq must reference earlier positive sequence numbers")
    if len(evidence_seq) != len(set(evidence_seq)):
        raise EventValidationError("evidence_seq must not contain duplicates")
    data = event["data"]
    if not isinstance(data, dict):
        raise EventValidationError("data must be an object")
    try:
        _validate_json_value(data)
    except RecursionError as exc:
        raise EventValidationError("data is too deeply nested") from exc
    _validate_payload(kind, data)


class _JournalState:
    """Mutable lifecycle state shared by the writer and reader."""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id
        self.seq = 0
        self.event_ids: set[str] = set()
        self.started = False
        self.finished = False
        self.active_phases: set[str] = set()

    def validate(self, event: Mapping[str, Any]) -> None:
        validate_event(event)
        event_run_id = event["run_id"]
        if self.run_id is not None and event_run_id != self.run_id:
            raise EventValidationError("all events in a journal must use the same run_id")
        if event["seq"] != self.seq + 1:
            raise EventValidationError("event sequence must be contiguous")
        if any(reference > self.seq for reference in event["evidence_seq"]):
            raise EventValidationError("evidence_seq must reference events already in the journal")
        if event["event_id"] in self.event_ids:
            raise EventValidationError("event_id must be unique within a journal")
        kind = event["kind"]
        if not self.started and kind != "run_started":
            raise EventValidationError("run_started must be the first event")
        if self.started and kind == "run_started":
            raise EventValidationError("run_started may appear only once")
        if self.finished:
            raise EventValidationError("run_finished must be the terminal event")
        if kind == "phase_started":
            phase = event["data"]["phase"]
            if phase in self.active_phases:
                raise EventValidationError(f"phase is already active: {phase!r}")
        elif kind == "phase_finished":
            phase = event["data"]["phase"]
            if phase not in self.active_phases:
                raise EventValidationError(f"phase was not started: {phase!r}")
        elif kind == "policy_decision":
            through_seq = event["data"]["through_seq"]
            if through_seq > self.seq:
                raise EventValidationError(
                    "policy_decision.data.through_seq must reference committed events"
                )
        elif kind == "evaluator_result":
            if any(reference > self.seq for reference in event["data"]["evidence_refs"]):
                raise EventValidationError(
                    "evaluator_result.data.evidence_refs must reference committed events"
                )

    def commit(self, event: Mapping[str, Any]) -> None:
        self.run_id = self.run_id or event["run_id"]
        self.seq = event["seq"]
        self.event_ids.add(event["event_id"])
        kind = event["kind"]
        if kind == "run_started":
            self.started = True
        elif kind == "phase_started":
            self.active_phases.add(event["data"]["phase"])
        elif kind == "phase_finished":
            self.active_phases.remove(event["data"]["phase"])
        elif kind == "run_finished":
            self.finished = True


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise EventValidationError("utc_now must return a datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _clock_ns(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EventValidationError("monotonic_ns must return an integer")
    return value


def _sync_parent_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


class EventWriter:
    """Write a validated event stream to an exclusive JSONL file."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        run_id: str | uuid.UUID,
        *,
        utc_now: Callable[[], datetime] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
    ) -> None:
        self.path = Path(path)
        self.run_id = _normalize_uuid(run_id, "run_id")
        self._utc_now = utc_now or _utc_now
        self._monotonic_ns = monotonic_ns or time.monotonic_ns
        self._started_ns = _clock_ns(self._monotonic_ns())
        self._last_elapsed_ns = 0
        self._state = _JournalState(self.run_id)
        self._poisoned = False
        self._closed = False
        self._handle = self.path.open("x", encoding="utf-8", newline="\n")
        try:
            _sync_parent_directory(self.path)
        except Exception as exc:  # noqa: BLE001 - creation durability is required
            try:
                self._handle.close()
            finally:
                raise EventWriteError("could not synchronize event journal directory") from exc

    def _ensure_writable(self) -> None:
        if self._closed:
            raise EventWriterClosed("event writer is closed")
        if self._poisoned:
            raise EventWriterPoisoned("event writer is poisoned after an I/O failure")

    def append(
        self,
        kind: str,
        data: Mapping[str, Any],
        *,
        source: str = "supervisor",
        span_id: str | None = None,
        parent_span_id: str | None = None,
        source_event_id: str | uuid.UUID | None = None,
        evidence_seq: Iterable[int] = (),
    ) -> dict[str, Any]:
        self._ensure_writable()
        if not isinstance(data, Mapping):
            raise EventValidationError("data must be an object")
        if isinstance(source_event_id, uuid.UUID):
            source_event_id = str(source_event_id)
        try:
            evidence = list(evidence_seq)
        except TypeError as exc:
            raise EventValidationError("evidence_seq must be iterable") from exc
        elapsed_ns = max(0, _clock_ns(self._monotonic_ns()) - self._started_ns)
        elapsed_ns = max(self._last_elapsed_ns, elapsed_ns)
        event = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "event_id": str(uuid.uuid4()),
            "seq": self._state.seq + 1,
            "timestamp": _format_timestamp(self._utc_now()),
            "elapsed_ns": elapsed_ns,
            "source": source,
            "kind": kind,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "source_event_id": source_event_id,
            "evidence_seq": evidence,
            "data": dict(data),
        }
        self._state.validate(event)
        try:
            line = json.dumps(
                event,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ) + "\n"
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise EventValidationError("event is not JSON serializable") from exc
        try:
            self._handle.write(line)
            self._handle.flush()
            if kind in _DURABILITY_KINDS:
                os.fsync(self._handle.fileno())
        except Exception as exc:  # noqa: BLE001 - poison on every write-path failure
            self._poisoned = True
            raise EventWriteError(f"could not write event {event['event_id']}") from exc
        self._state.commit(event)
        self._last_elapsed_ns = elapsed_ns
        return event

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()
        except Exception as exc:  # noqa: BLE001 - report close failures consistently
            self._poisoned = True
            self._closed = True
            try:
                if not self._handle.closed:
                    self._handle.close()
            except Exception:
                pass
            raise EventWriteError("could not close event journal") from exc
        self._closed = True

    def __enter__(self) -> EventWriter:
        self._ensure_writable()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def read_events(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read and validate every event in a JSONL journal."""

    events: list[dict[str, Any]] = []
    state = _JournalState()
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.endswith("\n"):
                    raise TruncatedEventError(
                        f"event journal line {line_number} has no terminating newline"
                    )
                content = line[:-1]
                if content.endswith("\r"):
                    content = content[:-1]
                if not content.strip():
                    raise EventReadError(f"event journal line {line_number} is empty")
                try:
                    event = json.loads(
                        content,
                        object_pairs_hook=_reject_duplicate_json_keys,
                        parse_constant=_reject_json_constant,
                    )
                except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
                    raise EventReadError(
                        f"event journal line {line_number} is invalid JSON"
                    ) from exc
                if not isinstance(event, dict):
                    raise EventReadError(f"event journal line {line_number} is not an object")
                try:
                    state.validate(event)
                except EventValidationError as exc:
                    raise EventReadError(
                        f"event journal line {line_number} is invalid: {exc}"
                    ) from exc
                state.commit(event)
                events.append(event)
    except UnicodeDecodeError as exc:
        raise EventReadError("event journal contains invalid UTF-8") from exc
    if not events:
        raise EventReadError("event journal is empty; run_started is required")
    return events
