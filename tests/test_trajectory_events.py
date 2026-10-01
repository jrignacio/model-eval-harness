import json
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from eval_harness.trajectory import (
    EventReadError,
    EventValidationError,
    EventWriter,
    EventWriterClosed,
    EventWriterPoisoned,
    EventWriteError,
    TruncatedEventError,
    read_events,
    validate_event,
)


class Sequence:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


def _finish_data():
    return {
        "termination_reason": "completed",
        "outcome": "success",
        "autonomy": "autonomous",
        "trace_complete": True,
    }


def test_valid_round_trip_has_ordered_unique_events_and_utc_timestamps(tmp_path):
    path = tmp_path / "events.jsonl"
    run_id = str(uuid.uuid4())
    writer = EventWriter(
        path,
        run_id,
        utc_now=Sequence(
            datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 19, 12, 0, 1, tzinfo=UTC),
            datetime(2026, 9, 19, 12, 0, 2, tzinfo=UTC),
            datetime(2026, 9, 19, 12, 0, 3, tzinfo=UTC),
        ),
        monotonic_ns=Sequence(100, 200, 500, 900, 1200),
    )
    writer.append("run_started", {"manifest_ref": "manifest.json"})
    writer.append("phase_started", {"phase": "agent"})
    writer.append("phase_finished", {"phase": "agent", "status": "ok"})
    writer.append("run_finished", _finish_data())
    writer.close()

    events = read_events(path)
    assert [event["seq"] for event in events] == [1, 2, 3, 4]
    assert len({event["event_id"] for event in events}) == 4
    assert all(event["run_id"] == run_id for event in events)
    assert all(event["timestamp"].endswith("Z") for event in events)
    assert [event["data"] for event in events][0] == {"manifest_ref": "manifest.json"}


def test_elapsed_time_uses_monotonic_clock_when_wall_clock_moves_back(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(
        path,
        str(uuid.uuid4()),
        utc_now=Sequence(
            datetime(2026, 9, 19, 12, 0, 2, tzinfo=UTC),
            datetime(2026, 9, 19, 11, 59, 59, tzinfo=UTC),
        ),
        monotonic_ns=Sequence(1_000, 2_500, 2_000),
    )
    writer.append("run_started", {"manifest_ref": "manifest.json"})
    writer.append("run_finished", _finish_data())
    writer.close()

    events = read_events(path)
    assert [event["elapsed_ns"] for event in events] == [1_500, 1_500]
    assert events[1]["timestamp"] < events[0]["timestamp"]


def test_validation_failure_does_not_write_or_advance_sequence(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, str(uuid.uuid4()))
    writer.append("run_started", {"manifest_ref": "manifest.json"})
    before = path.read_bytes()

    with pytest.raises(EventValidationError):
        writer.append("model_response", {"request_id": "req-1"})

    assert path.read_bytes() == before
    event = writer.append("phase_started", {"phase": "agent"})
    assert event["seq"] == 2
    writer.close()


def test_lifecycle_rejects_duplicate_start_and_append_after_finish(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, str(uuid.uuid4()))
    writer.append("run_started", {"manifest_ref": "manifest.json"})

    with pytest.raises(EventValidationError):
        writer.append("run_started", {"manifest_ref": "manifest.json"})

    writer.append("run_finished", _finish_data())
    with pytest.raises(EventValidationError):
        writer.append(
            "error",
            {"component": "agent", "code": "late", "message": "late", "recoverable": False},
        )
    writer.close()


def test_writer_requires_exclusive_new_file(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        EventWriter(path, str(uuid.uuid4()))


def test_writer_syncs_parent_directory_when_creating_journal(tmp_path):
    path = tmp_path / "events.jsonl"
    with patch("eval_harness.trajectory.events.os.fsync") as fsync:
        writer = EventWriter(path, uuid.uuid4())

    fsync.assert_called_once()
    writer.close()


def test_write_failure_poisons_writer(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, str(uuid.uuid4()))
    writer._handle.close()

    with pytest.raises(EventWriteError):
        writer.append("run_started", {"manifest_ref": "manifest.json"})
    with pytest.raises(EventWriterPoisoned):
        writer.append("run_started", {"manifest_ref": "manifest.json"})


def test_reader_reports_truncated_final_jsonl_record(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, str(uuid.uuid4()))
    writer.append("run_started", {"manifest_ref": "manifest.json"})
    writer.close()
    path.write_bytes(path.read_bytes().rstrip(b"\n"))

    with pytest.raises(TruncatedEventError):
        read_events(path)


def test_reader_rejects_invalid_lifecycle_and_json(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("{\"not\": \"an event\"}\n", encoding="utf-8")

    with pytest.raises(EventReadError):
        read_events(path)


def test_reader_rejects_empty_journal_duplicate_keys_and_invalid_utf8(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    with pytest.raises(EventReadError):
        read_events(empty)

    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(
        '{"schema_version":"agent-eval.events/1",'
        '"schema_version":"agent-eval.events/1"}\n',
        encoding="utf-8",
    )
    with pytest.raises(EventReadError):
        read_events(duplicate)

    invalid_utf8 = tmp_path / "invalid-utf8.jsonl"
    invalid_utf8.write_bytes(b"\xff\n")
    with pytest.raises(EventReadError):
        read_events(invalid_utf8)

    escaped_surrogate = tmp_path / "escaped-surrogate.jsonl"
    event = {
        "schema_version": "agent-eval.events/1",
        "run_id": str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "seq": 1,
        "timestamp": "2026-09-19T12:00:00.000000Z",
        "elapsed_ns": 0,
        "source": "supervisor",
        "kind": "run_started",
        "span_id": None,
        "parent_span_id": None,
        "source_event_id": None,
        "evidence_seq": [],
        "data": {"manifest_ref": "\ud800"},
    }
    escaped_surrogate.write_text(json.dumps(event, ensure_ascii=True) + "\n", encoding="utf-8")
    with pytest.raises(EventReadError):
        read_events(escaped_surrogate)

    deeply_nested = tmp_path / "deeply-nested.jsonl"
    deeply_nested_prefix = (
        '{"schema_version":"agent-eval.events/1",'
        f'"run_id":"{uuid.uuid4()}",'
        f'"event_id":"{uuid.uuid4()}",'
        '"seq":1,"timestamp":"2026-09-19T12:00:00.000000Z",'
        '"elapsed_ns":0,"source":"supervisor","kind":"run_started",'
        '"span_id":null,"parent_span_id":null,"source_event_id":null,'
        '"evidence_seq":[],"data":{"manifest_ref":"manifest.json","nested":'
    )
    deeply_nested.write_text(
        deeply_nested_prefix + "[" * 1100 + "0" + "]" * 1100 + "}}\n",
        encoding="utf-8",
    )
    with pytest.raises(EventReadError):
        read_events(deeply_nested)


def test_close_failure_poison_writes_and_rejects_later_append(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, str(uuid.uuid4()))
    with patch("eval_harness.trajectory.events.os.fsync", side_effect=OSError("sync failed")):
        with pytest.raises(EventWriteError):
            writer.close()

    with pytest.raises(EventWriterClosed):
        writer.append("run_started", {"manifest_ref": "manifest.json"})


def test_validation_rejects_surrogates_and_cycles_without_poisoning(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, str(uuid.uuid4()))
    writer.append("run_started", {"manifest_ref": "manifest.json"})

    with pytest.raises(EventValidationError):
        writer.append("phase_started", {"phase": "\ud800"})

    cycle = {}
    cycle["self"] = cycle
    with pytest.raises(EventValidationError):
        writer.append(
            "observation",
            {
                "observation_id": "obs-1",
                "channel": "dom",
                "artifact_ref": "artifact.json",
                "visible_to_agent": True,
                "cycle": cycle,
            },
        )

    event = writer.append("phase_started", {"phase": "agent"})
    assert event["seq"] == 2
    writer.close()


def test_serialization_failure_is_validation_error_without_poisoning(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, str(uuid.uuid4()))
    writer.append("run_started", {"manifest_ref": "manifest.json"})

    with pytest.raises(EventValidationError):
        writer.append(
            "observation",
            {
                "observation_id": "obs-1",
                "channel": "dom",
                "artifact_ref": "artifact.json",
                "visible_to_agent": True,
                "oversized": 10**5000,
            },
        )

    event = writer.append("phase_started", {"phase": "agent"})
    assert event["seq"] == 2
    writer.close()


def test_deep_nesting_is_validation_error_without_poisoning(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, str(uuid.uuid4()))
    writer.append("run_started", {"manifest_ref": "manifest.json"})

    nested = 0
    for _ in range(1100):
        nested = [nested]
    with pytest.raises(EventValidationError):
        writer.append(
            "observation",
            {
                "observation_id": "obs-1",
                "channel": "dom",
                "artifact_ref": "artifact.json",
                "visible_to_agent": True,
                "nested": nested,
            },
        )

    event = writer.append("phase_started", {"phase": "agent"})
    assert event["seq"] == 2
    writer.close()


def test_validate_event_rejects_non_finite_values_and_malformed_ids():
    event = {
        "schema_version": "agent-eval.events/1",
        "run_id": str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "seq": 1,
        "timestamp": "2026-09-19T12:00:00.000000Z",
        "elapsed_ns": 0,
        "source": "supervisor",
        "kind": "run_started",
        "span_id": None,
        "parent_span_id": None,
        "source_event_id": None,
        "evidence_seq": [],
        "data": {"manifest_ref": "manifest.json", "bad": float("nan")},
    }
    with pytest.raises(EventValidationError):
        validate_event(event)

    event["data"].pop("bad")
    event["event_id"] = uuid.uuid4()
    with pytest.raises(EventValidationError):
        validate_event(event)

    event["event_id"] = "not-a-uuid"
    with pytest.raises(EventValidationError):
        validate_event(event)


def test_event_evidence_must_point_to_earlier_sequences(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, str(uuid.uuid4()))
    writer.append("run_started", {"manifest_ref": "manifest.json"})

    with pytest.raises(EventValidationError):
        writer.append(
            "error",
            {"component": "agent", "code": "x", "message": "x", "recoverable": False},
            evidence_seq=[2],
        )

    with pytest.raises(EventValidationError):
        writer.append(
            "policy_decision",
            {
                "policy_version": "rules-1",
                "through_seq": 999,
                "assessment": "progressing",
                "proposed_action": "continue",
                "applied": False,
            },
        )

    with pytest.raises(EventValidationError):
        writer.append(
            "evaluator_result",
            {
                "evaluator_version": "eval-1",
                "verdict": "pass",
                "checks": {},
                "evidence_refs": [0, 1, 1],
            },
        )

    with pytest.raises(EventValidationError):
        writer.append(
            "evaluator_result",
            {
                "evaluator_version": "eval-1",
                "verdict": "pass",
                "checks": {},
                "evidence_refs": [2],
            },
        )

    writer.append(
        "evaluator_result",
        {
            "evaluator_version": "eval-1",
            "verdict": "pass",
            "checks": {},
            "evidence_refs": [1],
        },
    )

    writer.close()
