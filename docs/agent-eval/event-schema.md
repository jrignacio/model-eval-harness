# Event schema

## Envelope

One JSON object per line:

```json
{
  "schema_version": "agent-eval.events/1",
  "run_id": "UUID",
  "event_id": "UUID",
  "seq": 1,
  "timestamp": "2026-09-19T12:00:00.000000Z",
  "elapsed_ns": 0,
  "source": "supervisor",
  "kind": "run_started",
  "span_id": null,
  "parent_span_id": null,
  "source_event_id": null,
  "evidence_seq": [],
  "data": {}
}
```

Rules:

- One supervisor owns journal ordering.
- `seq` starts at one and is contiguous.
- `elapsed_ns` comes from the supervisor’s monotonic clock.
- Wall-clock timestamps are UTC; wall-clock jumps do not reorder events.
- Preserve source timestamps inside `data` where available.
- IDs are unique; evidence references point only to earlier events.
- Unknown measurements are `null` with a reason.
- Browser-specific data is optional payload, never a required envelope field.
- Raw records remain available through relative path, SHA-256 and media type.
- No secrets or authorization headers in records.

## Event vocabulary

| Kind | Required payload |
|---|---|
| `run_started` | `manifest_ref` |
| `phase_started` | `phase` |
| `phase_finished` | `phase`, `status` |
| `model_request` | `request_id`, `model`, `request_ref`, `attempt_index` |
| `model_response` | `request_id`, `response_id`, `response_ref`, `status` |
| `tool_call` | `call_id`, `name`, `arguments_ref` |
| `tool_result` | `call_id`, `status`, `result_ref` |
| `interface_action` | `action_id`, `operation`, `status`; optional parent `call_id` |
| `observation` | `observation_id`, `channel`, `artifact_ref`, `visible_to_agent` |
| `usage_reported` | `measurement_id`, `scope`, `basis`, `values`, `coverage` |
| `retry` | `scope`, `operation_id`, `attempt_index`, `reason`, `delay_ms` |
| `heartbeat` | `process_state`, `outstanding_operation_ids` |
| `error` | `component`, `code`, `message`, `recoverable` |
| `intervention` | `intervention_id`, `actor`, `action`, `reason`, `phase`, `affected_run_id` |
| `policy_decision` | `policy_version`, `through_seq`, `assessment`, `proposed_action`, `applied` |
| `evaluator_result` | `evaluator_version`, `verdict`, `checks`, `evidence_refs` |
| `run_finished` | `termination_reason`, `outcome`, `autonomy`, `trace_complete` |

Payloads may contain additional JSON fields. Required fields have explicit type checks. Non-finite numbers, unsupported objects, missing IDs, and unknown event kinds are rejected.

## Lifecycle and durability

- `run_started` is first and appears once.
- `run_finished` appears at most once and is terminal.
- Closing a writer must not invent successful completion.
- Serialize and validate a full line before writing it.
- Flush each event; synchronize durable storage at lifecycle boundaries.
- On write failure, poison the writer and stop execution.
- Reader reports an incomplete final line as truncation; it never silently repairs the file.
- A recovered attempt remains incomplete unless recovery produces an explicit, separate terminal record with provenance.

## Usage normalization

Each usage record states:

```text
scope: request | turn | session
basis: delta | cumulative
coverage: complete | partial | unavailable
```

Preserve original provider fields. Normalize into:

```text
input_total
input_uncached
input_cache_read
input_cache_write
output_total
output_reasoning
```

Reasoning tokens may be a subset of output. Cached input may be a subset of input. Never sum both blindly.

Deduplicate by provider response or source message identity. For cumulative values, derive deltas only within the same counter epoch.

Costs require a versioned price sheet and accounting coverage. Subscription allowance is not an API invoice. Unknown model pricing remains unknown—no fallback rate.

A harness turn is not necessarily one model request. Count those separately.

## Interventions

Record requested and applied phases separately.

Actions include:

```text
clarify | correct | approve | manual_action | extend_budget
switch_interface | switch_model | interrupt | resume
```

Any applied human assistance after agent start prevents classification as autonomous success. Pre-run setup is recorded separately.

A deterministic timeout is a controller action, not human assistance. A shadow policy recommendation is not an applied intervention.

## Policy replay

Replay receives only events through `through_seq` that were observable at that time. Exclude evaluator results, future outcomes, expected answers, and later human corrections.

Return:

```text
progressing | inspection_only | waiting | blocked | inactive | indeterminate
```

Keep assessment separate from proposed action and authorization.

Historical replay can assess classification and premature-stop errors. It cannot establish what would have happened after an unobserved alternate action. That needs new controlled execution.

## Failure attribution

Record observed failure location separately from inferred cause:

```text
observed_component
suspected_causes[]
evidence_seq[]
attribution_status: observed | inferred | unresolved
```

An unsuccessful task is not automatically a model failure.
