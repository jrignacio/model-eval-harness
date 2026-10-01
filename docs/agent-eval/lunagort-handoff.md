# LunaGort handoff

All paths below are relative to:

```text
/Users/jr/workspaces/pugad/model-eval-harness
```

Root must materialize and approve the five planning artifacts before execution. Each packet ends at a STOP checkpoint. No packet authorizes commits, pushes, deployment, other agents, or edits to global harness configuration.

The named-agent-routing skill requires Smith review for implementation of a Sage plan. LunaGort recommends that review; it does not invoke Smith. [Routing contract](/Users/jr/.codex/skills/named-agent-routing/SKILL.md)

| Packet | Exact scope | Files | Verification / STOP |
|---|---|---|---|
| P1 | Journal writer, validator and reader only | `trajectory/__init__.py`, `trajectory/events.py`, `tests/test_trajectory_events.py` | Offline tests; STOP before runner work. |
| P2 | Seeded fixture and native browser capability proof | `trajectory-runtime/package.json`, lockfile, `fixture/{server,app,domain,webmcp}.mjs`, `fixture/index.html`, `tests/{fixture,native-webmcp}.test.mjs` | No model calls. Both UI and native WebMCP produce identical expected states. STOP if native API unavailable. |
| P3 | Two restricted interfaces | `trajectory-runtime/interfaces/{dom,webmcp}.mjs`, `tests/interfaces.test.mjs` | Scripted actions only; stale references, wrong origin, unsupported tools and extra fields fail closed. |
| P4 | Single model loop and instrumentation | `trajectory-runtime/{worker,loop,provider}.mjs`, `tests/loop.test.mjs`, `UPSTREAM.md` | Stubbed provider; exact requests, duplicate IDs, errors, truncation and zero hidden retries tested. No paid calls. |
| P5 | Supervisor, tasks, evaluator and report | `trajectory/{runner,environment,evaluator,report,__main__}.py`, `experiments/001.json`, `tasks/agent-eval/*.json`, matching Python tests | Full mock matrix, process timeout, evaluator isolation, intervention and non-browser contract tests. STOP for review. |
| P6 | Capability preflight, then approved live pilot | New run artifacts only | Freeze manifests; approve exact model/settings/prices/spend ceiling; run calibration then forty attempts. STOP on changed configuration or unknown accounting. |

Here `trajectory/` means `src/eval_harness/trajectory/`.

## Packet dependencies and constraints

P1 and P2 can be implemented independently, but execution remains sequential initially. P3 depends on P2. P4 depends on the interface contract. P5 integrates all prior packets. P6 depends on reviewed offline results.

Before P2–P5, read the relevant inspected upstream source identified in `research-notes.md`. Pin copied source and preserve its license/attribution. Do not install an entire upstream dependency tree just to reuse one loop.

Proposed verification commands:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_trajectory_events.py -q
node --test trajectory-runtime/tests/fixture.test.mjs
node --test trajectory-runtime/tests/native-webmcp.test.mjs
node --test trajectory-runtime/tests/interfaces.test.mjs
node --test trajectory-runtime/tests/loop.test.mjs
PYTHONPATH=src .venv/bin/python -m pytest tests/test_trajectory_*.py -q
git diff --check
```

These are implementation acceptance commands, not checks already run.

P5 must add:

```bash
PYTHONPATH=src .venv/bin/python -m eval_harness.trajectory \
  run --experiment experiments/001.json --mock --output <new-directory>
```

The command refuses an existing output directory and makes no provider calls in mock mode.

## Execution plan

1. **REVERSIBLE:** Root materializes these planning artifacts.
2. **REVERSIBLE:** LunaGort implements P1, then returns its validation report.
3. **STOP:** Review the journal contract before adding runtime behavior.
4. **REVERSIBLE:** Execute P2–P5 at their stated checkpoints; retain all work locally.
5. **STOP:** Review isolation, native WebMCP proof, model access, exact configuration and spend ceiling.
6. **IRREVERSIBLE:** Approved live API calls spend money and transmit synthetic inputs. Execute P6 only after j approves that concrete run.
7. **REVERSIBLE:** Produce the report and decide whether to add Codex or refine the fixture.

## Risks after execution

- Four small tasks can validate machinery while overstating usefulness on real websites.
- Native WebMCP and model aliases can change; manifests detect drift but cannot freeze a provider’s internals.
- Interface design remains an author choice. A specially helpful WebMCP tool can bias the result.
- Five repetitions provide weak reliability estimates.
- Future general-purpose harnesses can regain evaluator access unless their isolation is tested.
- Offline monitor replay cannot prove the benefit of intervention.

## Final handoff

**1. Architecture:** a non-agentic Python supervisor records and evaluates attempts; interchangeable harness and interface adapters run against isolated environments. Start with one WindTunnel-derived native loop and keep the evaluator outside it.

**2. First experiment:** Luna through restricted DOM controls versus native WebMCP, four deterministic local tasks, five repetitions each: forty measured attempts.

**3. Main changes from your assumptions:** Browser Harness is a workflow bundle, WebMCP is an application interface, Jev is an optional assessor, and the published Jev comparison is not an isolated interface test. Begin outside Codex so hidden harness behavior does not dominate the architecture proof.

**4. Exact first LunaGort work packet:**

> Implement P1 only: the event journal described in `docs/agent-eval/event-schema.md`.
>
> Read first: all five planning artifacts, `pyproject.toml`, `src/eval_harness/types.py`, `src/eval_harness/runner.py`, and `tests/test_runner.py`.
>
> Allowed changes:
>
> - `src/eval_harness/trajectory/__init__.py`
> - `src/eval_harness/trajectory/events.py`
> - `tests/test_trajectory_events.py`
>
> Use Python’s standard library and existing pytest. Do not change dependencies.
>
> Provide:
>
> ```python
> class EventWriter:
>     def __init__(self, path, run_id, *, utc_now=None, monotonic_ns=None): ...
>     def append(self, kind, data, *, source="supervisor",
>                span_id=None, parent_span_id=None,
>                source_event_id=None, evidence_seq=()): ...
>     def close(self): ...
>
> def validate_event(event): ...
> def read_events(path): ...
> ```
>
> Create the output file exclusively; never overwrite it. Generate event UUIDs and contiguous sequence numbers. Use injectable clocks. Enforce the envelope, required payload fields, lifecycle, and earlier-event references. Validate before writing. Reject NaN, infinity, malformed IDs and non-JSON values. Flush each event and fsync lifecycle boundaries. After an I/O failure, reject further appends. Closing without `run_finished` preserves an incomplete run.
>
> Tests must cover valid round-trip, UTC timestamps, monotonic elapsed time despite a backward wall clock, ordering, unique IDs, required payload rejection, unchanged file/sequence after validation failure, duplicate start, append after finish, existing-file refusal, write failure, and truncated JSONL.
>
> Run:
>
> ```bash
> PYTHONPATH=src .venv/bin/python -m pytest tests/test_trajectory_events.py -q
> git diff --check
> ```
>
> Do not implement adapters, fixtures, metrics aggregation, browser operations, provider calls, hooks or CLI changes. Do not commit or invoke another agent.
>
> **STOP:** return changed files, test results, deviations and unresolved points. Recommend Smith review. Do not begin P2.
