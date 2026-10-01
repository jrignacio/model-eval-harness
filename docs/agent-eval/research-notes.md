# Research notes

## Local inventory: verified during the planning pass

| Surface | Finding | Architectural consequence |
|---|---|---|
| Existing eval repo | Located at [`pugad/model-eval-harness`](/Users/jr/workspaces/pugad/model-eval-harness). Clean worktree when checked; HEAD `2a41f94`. The historical `personal` path is absent. | Extend this repo. |
| Current abstraction | [`types.py`](/Users/jr/workspaces/pugad/model-eval-harness/src/eval_harness/types.py) represents text generations and rubric results. [`runner.py`](/Users/jr/workspaces/pugad/model-eval-harness/src/eval_harness/runner.py) writes results after concurrent evaluations complete. | Add a separate trajectory path; preserve the text path. |
| Provider and CLI adapters | [`models.py`](/Users/jr/workspaces/pugad/model-eval-harness/src/eval_harness/models.py) supports direct APIs plus Codex, Claude, and `agy`. CLI methods ignore the requested token and temperature settings. Codex and `agy` return final text and elapsed time without usage. | Do not treat their nominal settings or absent usage as comparable measurements. |
| Codex | Installed CLI `0.155.1`; supports `--json`, `--ephemeral`, `--ignore-user-config`, and explicit working directory. Existing adapter neither requests JSON nor changes subprocess cwd. | Build a new adapter later; do not copy the current adapter unchanged. |
| LunaGort | [`lunagort.toml`](/Users/jr/.codex/agents/lunagort.toml) currently specifies Luna, maximum reasoning, and checkpointed execution. | Profile exists; callable agent discovery was not tested because delegation was forbidden. |
| Claude Code | Executable and settings present. Inspected settings contain lifecycle configuration; Playwright and Chrome DevTools plugins are disabled there. | Installed/configured does not establish a working browser path. |
| Pi | Executable, usage extension, Herdr/Orca status extensions, and Safari extension exist. Safari configuration says enabled. | Useful later adapter candidates; no live Safari connection was tested. |
| Antigravity | `agy` executable exists; text adapter exists. Gemini configuration also has tool/agent hooks. | No claim that Gemini configuration governs `agy`; runtime provenance still needs checking. |
| Browser runtime | Chrome `153.0.8010.50`; cached Playwright Chromium assets present. `browser-harness` and `docker` were not found on this shell’s PATH. | Existing browser assets help, but capability remains untested. |
| Telemetry | Codex hooks reference Orca and Herdr; Pi has token/cache/cost normalization. | These are lifecycle/accounting sources, not independent progress judges. |
| Earlier browser pilot | [`run-log.md`](/Users/jr/workspaces/tmp/browser-mcp-token-test/run-log.md) records 11 pilot trials, one per condition, full-toolset overhead, and a fixed duplicate-message accounting bug. `report.md` is absent. | Reuse the deduplication lesson, not the pilot as a reliable ranking. |
| Jev experiments | [`jev-starter`](/Users/jr/workspaces/pugad/jev-starter/README.md), pilot outputs, and [`jev_judgment()`](/Users/jr/workspaces/pugad/model-eval-harness/src/eval_harness/judge.py:113) exist. | Jev experimentation is real. A production progress monitor was not established. |

No benchmark, paid model request, or browser session was launched. Existing tests were inspected, not rerun.

Official Codex documentation confirms JSONL lifecycle/item events and reported usage. That does **not** imply every internal model request or retry is visible. [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive/)

## Browser Harness

Inspected README, installation instructions, skill, AGENTS, MCP documentation, and execution/daemon/recording source at commit:

```text
afbcc381b963040c19627d788e40c7e7663171ee
```

**Verified source facts:** it executes agent-written Python against browser helpers and a persistent CDP connection. The host agent owns reasoning and model calls. Helpers and domain skills can persist. Local daemon naming does not create a separate browser profile. MCP exposes the helper layer, including broad JavaScript and CDP tools. Local recording captures browser actions; it cannot provide host-model token accounting. [Workflow](https://github.com/browser-use/browser-harness/blob/afbcc381b963040c19627d788e40c7e7663171ee/SKILL.md), [execution source](https://github.com/browser-use/browser-harness/blob/afbcc381b963040c19627d788e40c7e7663171ee/src/browser_harness/run.py), [MCP surface](https://github.com/browser-use/browser-harness/blob/afbcc381b963040c19627d788e40c7e7663171ee/docs/MCP.md)

**Inference:** Browser Harness is an interaction runtime plus workflow guidance. It is not the same component as Browser Use’s higher-level agent SDK. Editable helpers are a treatment variable, not harmless setup.

## WebMCP

**Verified documentation:** the current imperative surface uses `document.modelContext.registerTool`, discovery, and invocation. The specification is a Draft Community Group Report, not a W3C Standard. Chrome documentation describes a flag and origin trial; Chrome Status contains a trial stage covering milestones 149–156, while its summary fields remain “Proposed.” Do not infer universal availability. [Imperative API](https://developer.chrome.com/docs/ai/webmcp/imperative-api), [specification](https://webmachinelearning.github.io/webmcp/), [Chrome Status](https://chromestatus.com/feature/5117755740913664)

**Verified documentation:** tool annotations include read-only, untrusted-content, and consequential hints. They are not a substitute for application authorization or an executor’s permission policy. [Tool security](https://developer.chrome.com/docs/ai/webmcp/secure-tools)

**Inference:** WebMCP changes the application’s action and observation surface. MCP can carry those tools to a harness, but transport and application semantics are separate dimensions.

## WindTunnel: reuse decision

Inspected source at:

```text
5ca8644e23826ebb30108e7bad240b61043bfe67
```

**Verified source facts:** tasks declare prompts and predicates; capsules manage setup/reset/observation; arms run the agent; scoring reads answers or application state. Repetitions feed majority verdicts. Reports distinguish setup and agent time. Budgets, public development tasks, infrastructure exclusions, and interface-dependent turn limits affect interpretation. [Design specification](https://github.com/nekuda-ai/WindTunnel/blob/5ca8644e23826ebb30108e7bad240b61043bfe67/docs/SPEC.md)

Important source-level limitations:

- `actions_or_tool_calls` uses transcript length, which is not a uniform action count.
- Missing usage can become zero.
- Unknown pricing falls back to another model’s rates.
- The native OpenAI arm retries requests; timeout checks occur between loop iterations rather than constituting an external process deadline.
- Its WebMCP bridge replaces page surfaces and invokes registered handlers; this is not proof of native Chrome behavior.

Sources: [run lifecycle](https://github.com/nekuda-ai/WindTunnel/blob/5ca8644e23826ebb30108e7bad240b61043bfe67/harness/run.mjs), [accounting](https://github.com/nekuda-ai/WindTunnel/blob/5ca8644e23826ebb30108e7bad240b61043bfe67/harness/lib.mjs), [OpenAI arm](https://github.com/nekuda-ai/WindTunnel/blob/5ca8644e23826ebb30108e7bad240b61043bfe67/arms/wm-gpt.mjs), [WebMCP bridge](https://github.com/nekuda-ai/WindTunnel/blob/5ca8644e23826ebb30108e7bad240b61043bfe67/arms/wm-claude.mjs)

**Recommendation:** reuse an attributed, pinned adaptation of the native loop and later consume WindTunnel capsules/results through an adapter. Do not fork its entire benchmark or reproduce its site catalog.

## Jev result: what it establishes

**Published result:** WindTunnel reports 49/49 majority-solved tasks for Jev + Mercury with WebMCP and 25/49 with DOM controls. Its specification explicitly identifies these as different harnesses. Mercury generates arguments, values, and final answers. [Benchmark](https://webmcp.com/benchmark), [frozen experiment](https://github.com/nekuda-ai/WindTunnel/blob/5ca8644e23826ebb30108e7bad240b61043bfe67/experiments/jev/README.md)

**Hypothesis, not conclusion:** structured interfaces may make action selection easier. The published comparison does not isolate interface effects from policy, observations, prompting, or implementation.

## TypeSafe

**Documented contract:** text/structured state plus Choice, Noul, or Score questions; no free-text generation. Current model documentation lists `jev-1.13.0`, a 64k total request limit, and a 32k state-plus-longest-question limit. SDK retries are enabled by default. [API](https://docs.typesafe.ai/api), [models](https://docs.typesafe.ai/models), [retry policy](https://docs.typesafe.ai/sdk/python/api/retries)

**Vendor claims:** $0.042 per million input tokens, free output tokens, and 70–500 ms latency. These are not measurements from this machine. Its workflow evaluation compares fixed compute graphs against reference probabilities from larger models, not independently established task truth. [Announcement and methodology](https://typesafe.ai/blog/introducing-system-one-models-and-jev)

**Important distinction:** `confidence` summarizes the probability distribution; it is not automatically a locally calibrated probability of correctness. Thresholds need local validation. [Confidence](https://docs.typesafe.ai/confidence)

**Correction to local notes:** TypeSafe now documents enterprise ZDR. Whether your account has it remains unverified. [Current legal documentation](https://docs.typesafe.ai/legal)

## Other prior art worth retaining

- **WebMCP Kit:** useful later for auditing routes, selecting tools, wiring existing client logic, and verifying registration and visible effects. Its workflow and SDK add dependencies and defaults; do not install it merely to build the tiny fixture. [Implementation workflow](https://github.com/nekuda-ai/webmcp-kit/blob/f0298ec9f26af13e477b9141ab4d8f2a6c23426d/plugin/skills/implement/SKILL.md), [verification workflow](https://github.com/nekuda-ai/webmcp-kit/blob/f0298ec9f26af13e477b9141ab4d8f2a6c23426d/plugin/skills/verify/SKILL.md)
- **Harbor/ATIF:** preserve exportable messages, tool-call IDs, observations, and metrics. Keep evaluation outside agent access. Do not adopt its entire platform for this pilot. [ATIF](https://docs.harborframework.com/core-concepts/agents/atif), [separate verifier](https://docs.harborframework.com/core-concepts/tasks/separate-verifier)
- **OpenTelemetry:** retain span relationships, provider/model identity, and error types. Its agent conventions remain under development; use a later exporter rather than making them the internal storage contract. [Agent span specification](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)
