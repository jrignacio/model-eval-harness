# P4 upstream note

The P4 loop is an independent, dependency-free adaptation of the loop shape
used by WindTunnel's native OpenAI arm. The inspected source is pinned to
[`5ca8644e23826ebb30108e7bad240b61043bfe67`](https://github.com/nekuda-ai/WindTunnel/tree/5ca8644e23826ebb30108e7bad240b61043bfe67), especially
[`arms/wm-gpt.mjs`](https://github.com/nekuda-ai/WindTunnel/blob/5ca8644e23826ebb30108e7bad240b61043bfe67/arms/wm-gpt.mjs).

The adaptation keeps the useful per-turn sequence: discover the current public
tools, build a model request, parse response items, execute function calls, and
feed bounded tool outputs back into history. It does not copy WindTunnel code,
pull in its dependency tree, call a paid provider, or reuse its automatic
network/status retry behavior. P4 injects a deterministic stub provider so
request counts and failures are visible to tests.

WindTunnel is licensed under Apache-2.0. This note preserves attribution for
the referenced design inspiration; no WindTunnel source is vendored here.
