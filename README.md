# Model Eval Harness

A deliberately small evaluation system for comparing language models on the
same test cases. It keeps every important artifact inspectable:

- JSONL test cases with weighted rubrics
- interchangeable OpenAI, Anthropic, Gemini, and local mock runners
- local Codex, Claude Code, and Antigravity CLI runners using existing subscription auth
- deterministic checks plus model-based rubric judging
- raw JSONL results that are easy to diff or analyze
- a dependency-free HTML report

This is not an evaluation platform. It is a compact reference implementation
for the core loop: define behavior, run candidates, judge consistently, inspect
failures, and iterate.

## Quick start

The built-in mock models and heuristic judge run without dependencies, API keys,
or network access:

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/eval-harness run \
  --cases cases/support.jsonl \
  --models mock:baseline,mock:careful \
  --judge heuristic \
  --output runs/demo
```

Open `runs/demo/report.html` in a browser. The command also writes:

- `manifest.json`: run configuration and timing
- `results.jsonl`: one generation and judgment record per case/model pair
- `report.html`: aggregate comparison and case-level evidence

Run the tests with:

```sh
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## Run real models

### Use local CLIs

If `codex`, `claude`, or `agy` are already logged in, no API keys or provider
SDKs are needed:

```sh
.venv/bin/eval-harness run \
  --cases cases/support.jsonl \
  --models codex-cli:gpt-5.6-terra,claude-cli:sonnet,agy-cli:your-model \
  --judge claude-cli:opus \
  --output runs/cli-comparison \
  --max-workers 1
```

Use `--max-workers 1` initially: CLI-backed runs are full agent invocations and
may have different account limits from direct API calls. The harness starts
Codex with a read-only sandbox, no approvals, and ephemeral sessions. It starts
Claude with tools disabled and session persistence off. It starts Antigravity
with its sandbox enabled and a ten-minute print timeout.

In the current Codex CLI, `-p` means `--profile`, not print mode. The
non-interactive equivalent is `codex exec`. Claude’s `-p`/`--print` is its
non-interactive mode; Antigravity also supports `-p`/`--print`.

CLI model names are passed through unchanged. Use a model name or alias accepted
by the corresponding installed CLI.

### Use provider APIs

Install the provider SDKs you need:

```sh
.venv/bin/pip install -e '.[all]'
```

Set keys in the environment, never in this repository:

```sh
export OPENAI_API_KEY='...'
export ANTHROPIC_API_KEY='...'
export GEMINI_API_KEY='...'
```

Compare candidate models and use a separate model as judge:

```sh
.venv/bin/eval-harness run \
  --cases cases/support.jsonl \
  --models openai:gpt-5.6-terra,anthropic:claude-sonnet-5,gemini:your-model \
  --judge anthropic:claude-opus-4-8 \
  --output runs/live-comparison \
  --max-workers 4
```

Model identifiers are passed through to each provider. Confirm availability and
pricing in your account before a live run. Every candidate call and every
model-judge call can incur cost.

The direct `gemini:<model>` adapter and `agy-cli:<model>` adapter intentionally
measure different surfaces. Gemini uses Google’s Gen AI SDK with
`GEMINI_API_KEY`; Antigravity uses the locally authenticated agent CLI, whose
surrounding instructions and execution layer may affect results.

## Case format

Each line is one self-contained JSON object:

```json
{
  "id": "refund-policy",
  "input": "A customer asks ...",
  "system": "You are a support agent.",
  "reference": "Optional reference answer or facts.",
  "rubric": [
    {
      "id": "correct",
      "description": "States the policy accurately.",
      "weight": 3
    }
  ],
  "checks": {
    "must_include": ["3–5 business days"],
    "must_not_include": ["guaranteed"],
    "max_chars": 900
  },
  "metadata": {
    "category": "support",
    "difficulty": "easy"
  }
}
```

Rubric criteria are scored from 0 to 4. Their weighted mean is normalized to a
0–100 score. Deterministic checks are recorded alongside the rubric judgment;
failed checks cap the final score at 60 so a fluent answer cannot hide a hard
requirement failure.

## Architecture

```text
cases/*.jsonl
      │
      ▼
 candidate adapters ──► generated responses
      │                         │
      └─────────────────────────┤
                                ▼
                     deterministic checks
                                +
                       rubric-based judge
                                │
                                ▼
                     runs/*/results.jsonl
                                │
                                ▼
                       static HTML report
```

The model boundary is intentionally narrow: an adapter accepts a system
prompt, user prompt, and generation settings, then returns text plus usage
metadata. Add another provider by implementing `ModelAdapter.generate()` and
registering it in `models.create_adapter()`.

## Evaluation choices

- **Same inputs, explicit rubrics.** Candidate models see identical cases.
- **Judge isolation.** The judge sees the case, rubric, and candidate response,
  but not the candidate model name.
- **Structured judgments.** The judge must return JSON with criterion-level
  scores, reasons, and a concise overall note.
- **Auditable outputs.** Prompts, responses, checks, scores, latency, errors,
  and token counts remain in JSONL.
- **Failure tolerance.** One provider failure becomes an error record instead
  of aborting the full matrix.
- **Deterministic demo.** Mock models make the repository runnable in CI and in
  interviews without credentials.

## Known limits

- A single model judge can be biased or inconsistent. For consequential evals,
  calibrate against human labels and consider multiple judges.
- Token usage fields differ by provider and are normalized only to input and
  output counts.
- Codex CLI does not currently expose normalized usage through the adapter, so
  its token columns are reported as `n/a`. The same applies to Antigravity’s
  plain-text print mode.
- The heuristic judge exists for plumbing tests, not semantic evaluation.
- This harness does not implement rate-limit backoff beyond provider SDK
  defaults, prompt caching, batch APIs, or cost estimation.
