# Agent evaluation architecture plan

## Decision record

Extend the existing `pugad/model-eval-harness` repository with a small trajectory runner. Use a single, inspectable model loop for the first experiment, with **DOM controls versus native WebMCP** on the same local fixture. Reuse a bounded portion of WindTunnel rather than fork its whole benchmark. Add Codex after the experiment proves isolation, traces, and independent scoring.

The planning artifacts below are proposed content, not implementation.

### Why this and not the alternative

- **Codex first would mix architecture validation with harness isolation.** Its installed JSON event surface is useful, but the current local adapter does not consume it, and ordinary runs can inherit instructions, hooks, tools, and configuration.
- **Full Browser Harness versus WebMCP changes too many things.** Browser Harness includes code execution, persistent connections, editable helpers, optional domain skills, and broad browser access. That is a useful later stack comparison.
- **WindTunnel is worth reusing, but its current run contract is insufficient here.** Its task predicates, lifecycle, and native loop are useful. Its aggregated rows cannot replace timestamped intervention and policy-replay records.
- **A small custom fixture is justified.** It lets us prove equal business rules, isolated state, and evaluator access before taking on larger applications.

### Assumptions carried forward

- The first experiment tests the evaluation machinery and one interface hypothesis. It will not rank everyday coding harnesses.
- Synthetic local data is sufficient.
- Direct API access to the selected model must be checked before paid execution. The LunaGort configuration does not prove API access.
- “Same information” means equivalent task-relevant facts and permissions—not identical observation bytes.
- The existing text-evaluation workflow remains intact.

### What would change this

Reconsider before implementation expands if:

- Native WebMCP cannot pass the browser capability check without a compatibility shim.
- The chosen model cannot use both interfaces through the same request and history logic.
- One interface exposes facts or mutations unavailable through the other.
- Codex can already provide demonstrably isolated tools and complete accounting with less work than the small native driver.
- The trajectory addition starts duplicating WindTunnel’s capsule catalog or Harbor’s general execution platform.

## Problem

Measure how a configured agent system performs, while recording which differences can and cannot be attributed to a single variable.

Separate two experiment classes:

1. **Controlled comparison:** change one declared interface contract inside a fixed loop.
2. **Whole-stack comparison:** compare Codex, Claude Code, Pi, or Antigravity as configured systems.

A whole-stack result is still useful. It must not be described as a pure model or interface effect.

## Architecture

```text
Experiment manifest + task
             |
     Python supervisor
      /      |       \
environment  |     independent evaluator
 lifecycle   |        reads final state
             v
       Harness adapter <----> Model endpoint
             |
       Interface adapter
             |
     disposable environment

All components --> timestamped event journal --> report / later replay
                                      |
                           shadow policy assessor
                           (no execution authority)
```

The model and harness form a loop. A decision policy is a separate observer/controller, not necessarily a layer after the model.

## Component contracts

| Component | Owns | Must not own |
|---|---|---|
| Experiment | Matrix, order, repetitions, budgets, frozen configuration | Runtime decisions |
| Task | Public instruction and environment requirements | Interface-specific solution |
| Supervisor | IDs, lifecycle, deadlines, process ownership, journal | Agent reasoning or scoring |
| Harness adapter | Context/history and model interaction | Evaluator access |
| Interface adapter | Tool catalog, observations, allowed actions | Hidden task solution |
| Environment | Seeded state, application rules, reset, snapshot | Pass/fail |
| Evaluator | Predicates against sealed evidence | Agent guidance |
| Policy assessor | Assessment from an observable trace prefix | Direct termination or mutation |
| Report | Reproducible aggregation | Reclassifying failures silently |

Minimal Python interfaces:

```python
Environment.prepare(task, seed) -> Lease
Environment.snapshot(lease) -> SnapshotRef
Environment.close(lease) -> CleanupResult

Harness.run(public_task, config, lease, emit, deadline) -> AgentOutcome

Evaluator.evaluate(task, initial_snapshot, final_snapshot, outcome) -> Verdict
```

`Lease` exposes only the handles needed by the selected adapter. Evaluator handles are never included in the agent-facing lease.

The JavaScript interface boundary is:

```javascript
listTools() -> ToolDefinition[]
invoke(name, arguments, { signal }) -> ToolResult
close() -> void
```

Both arms use the same model request builder, history handling, parser, budgets, and finish logic.

## Technology and reuse

- Python supervisor and reporting inside `src/eval_harness/trajectory/`.
- JavaScript browser worker under `trajectory-runtime/`.
- JSONL journal and JSON manifests; no database initially.
- Playwright for trusted browser lifecycle and fixed actions.
- One attributed adaptation of WindTunnel’s native OpenAI loop.
- No modifications to existing `ModelAdapter`, text results, or judges.

The JavaScript worker is justified by browser APIs and the reusable upstream code. Python remains the CLI and orchestration layer.

## Scope

**v0: offline contract proof**

- Journal writer/reader.
- Fake harness and environment.
- Deadline, exit, evaluator-error, intervention, and malformed-event tests.
- One non-browser fake task to prove the core has no browser dependency.

**v1: experiment 001**

- One native model loop.
- One model configuration.
- Two interfaces.
- Four local tasks.
- Five repetitions per task/interface.
- Deterministic evaluator and CLI report.

**Excluded:** Jev control, real checkout, personal profiles, screenshots as an agent input, unrestricted code execution, dashboards, cloud deployment, parallel runs, and adaptive helpers.

## Isolation rules

- Fresh browser process and temporary profile for every attempt.
- Fixture state belongs to that attempt only.
- Browser requests are restricted to the fixture origin; model requests originate from the worker, not the page.
- No personal cookies, extensions, session restore, or default-profile discovery.
- Fixture reset and evaluation use supervisor-only IPC, not a browser-accessible admin endpoint.
- The model receives no filesystem, shell, raw HTTP, arbitrary JavaScript, or raw CDP tool.
- The worker receives public task data only. Expected answers and predicates remain with the supervisor.
- Both UI and WebMCP call the same application validation and mutation functions.

These restrictions are enforceable in the small native loop. Future general-purpose CLI adapters must prove equivalent boundaries separately.

## Manifest

Record:

- Experiment, task, arm, block and attempt IDs.
- Repo revision and dirty-diff hash.
- Adapter and dependency versions.
- Requested model, returned model identity, provider, reasoning configuration.
- Prompt, tool-schema and configuration hashes.
- Browser executable hash/version, flags, viewport, locale and timezone.
- Fixture revision, seed and initial-state hash.
- Retry policy, deadlines, call limits and output limits.
- Usage visibility and pricing source/date.
- Evaluation revision and allowed intervention policy.
- Unknown settings explicitly marked unknown.

Do not silently substitute a model, browser build, interface, or provider.

## Later expansion

1. Codex JSONL adapter with isolated configuration and a restricted common tool server.
2. One second CLI harness using that same tool server.
3. Browser Harness as a separate workflow treatment, with fresh helper directories and explicit code permissions.
4. WindTunnel capsule adapter for larger applications.
5. Filesystem/coding tasks with separate verifiers.
6. Offline Jev/Luna/rule-policy comparison.
7. Live policy intervention only after offline validation.

No core type should require a URL, browser page, DOM node, or screenshot.
