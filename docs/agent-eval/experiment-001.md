# Experiment 001: restricted DOM controls versus native WebMCP

## Question and hypothesis

Can the runner measure two interface treatments without changing the model loop, application rules, evaluator, or initial state?

Secondary hypothesis: native WebMCP reduces requests and elapsed time for structured mutations while maintaining task success.

The measured treatment includes tool descriptions, observation format, and action granularity. It is not “serialization alone.”

## Arms

| Field | A | B |
|---|---|---|
| Harness | Same pinned WindTunnel-derived loop | Same |
| Model | `gpt-5.6-luna`, pending API preflight | Same |
| Interface | Restricted visible-DOM controls | Native WebMCP |
| Policy | Fixed budgets; no semantic monitor | Same |
| Environment | `shop-fixture-v1` | Same |
| Evaluator | Same deterministic predicates | Same |

DOM tools: `snapshot`, `click`, `fill`, `select`.

- `snapshot` returns visible text, roles, labels, values, and temporary element references.
- Actions accept those references, not arbitrary selectors or JavaScript.
- References expire on navigation or a new snapshot.

WebMCP tools: `list_products`, `get_cart`, `set_cart_quantity`, `get_shipping`, `save_shipping`.

- Native registration and invocation only.
- No injected replacement for `document.modelContext`.
- No hidden stock, discount, admin, or evaluator fields.
- No one-call `complete_task` helper.

Both interfaces expose the same facts and business capabilities. Additional WebMCP structure is the treatment.

## Tasks

Use four fixed, seeded tasks; repeat each exact task five times.

| Task | Public instruction | Independent checks |
|---|---|---|
| `catalog-01` | Identify the cheapest in-stock blue mug below a stated price. | Structured answer matches seeded catalog; application state unchanged. |
| `cart-01` | Set a named mug to quantity two and remove the seeded cable. | Exact cart contents; no other state changes. |
| `shipping-01` | Save a supplied fictional recipient/address/note. | Exact stored fields; cart unchanged; no order created. |
| `stock-01` | Request more units than available. | Stock error observed, final response reports failure, cart unchanged. |

Tool descriptions must describe normal product operations. They must not mention benchmark predicates, task IDs, or expected answers.

Answer checks use structured claim groups with explicit paraphrase variants and
explicit rejection phrases. They do not compare the whole final answer to one
canonical sentence. State predicates and required tool-error evidence remain
independent supervisor checks.

## Schedule

- Two setup attempts, one per arm, excluded and labelled calibration.
- Forty measured attempts: `4 tasks × 2 arms × 5 repetitions`.
- Sequential execution.
- Pair A/B within each task/repetition; choose order from a recorded randomization seed.
- Fresh conversation, fixture state, browser process, and profile every time.
- Freeze configuration after calibration. Any change starts a new cohort.

Five repetitions validate mechanics and reveal gross differences. They do not establish a general reliability ranking.

## Budgets

Proposed pilot limits:

- 120 seconds agent time.
- 30 model requests.
- 60 interface invocations.
- 4,096 maximum output tokens per request.
- No automatic provider retries.
- No model or interface fallback.
- Separate setup and evaluation deadlines.

If calibration shows these limits invalidate the task—for example repeated output truncation—STOP and revise both arms before measurement.

Paid execution requires an explicit price sheet and spend ceiling. Reserve a conservative upper bound before each request. If usage or pricing is unknown, stop further paid requests; do not report zero cost.

## Success criteria

The architecture passes only if:

1. Tasks contain no interface-specific solution.
2. Both interfaces reach the same seeded application.
3. Every attempt has a valid journal and manifest.
4. The independent evaluator catches deliberate wrong states.
5. Repetitions reset cleanly.
6. Metrics can be recomputed from retained records.
7. A fake non-browser adapter runs through the same core.

A performance tie does not invalidate the architecture.

The architecture fails if evaluator data reaches the model, cross-arm tools remain available, resets leak state, accounting duplicates requests, a bridge is silently substituted, or an attempt disappears from reporting.

## Reporting

Primary outcomes:

- Autonomous pass.
- Assisted pass.
- Task failure.
- Timeout.
- Infrastructure failure.
- Evaluator error.
- Invalid experiment attempt.

Report every scheduled attempt and every replacement. Do not erase infrastructure failures from the operational denominator.

Show per-task pass counts, paired differences, medians/ranges, and cost/time for all attempts. Show successful-only measurements separately. No composite leaderboard score.

## Metrics and decisions

| Metric | Decision it supports |
|---|---|
| Autonomous/assisted success | Can this stack run unattended? |
| Agent and total wall time | Is the interface faster, or merely cheaper to start? |
| Input/output/cache/reasoning usage | What drives model consumption? |
| Estimated cost and accounting coverage | Is a cheaper stack actually cheaper? |
| Model requests and tool invocations | Does the interface reduce deliberation or actions? |
| Transport retries and action retries | Is failure in the provider path or task execution? |
| Timeouts, exits, evaluator errors | Can the result be trusted? |
| Observation size/context growth | Does repeated observation dominate cost? |
| First useful observation/state change | Where does startup delay occur? |
| Longest observation gap and outstanding operation | Was the agent quiet, waiting, or demonstrably inactive? |

Do not label silence “stalled.” Useful read-only investigation is progress.

## Artifacts

```text
manifest.json
schedule.json
attempts/<run-id>/events.jsonl
attempts/<run-id>/raw/
attempts/<run-id>/initial-state.json
attempts/<run-id>/final-state.json
attempts/<run-id>/answer.json
attempts/<run-id>/evaluation.json
attempts/<run-id>/stderr.txt
results.jsonl
report.md
```

Evaluator artifacts are not available to the agent during execution.
