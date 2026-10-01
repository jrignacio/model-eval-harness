import assert from "node:assert/strict";
import test from "node:test";

import { LoopError, runLoop } from "../loop.mjs";
import { ProviderError, createStubProvider } from "../provider.mjs";
import { runWorker } from "../worker.mjs";

function message(id, text) {
  return {
    id: `${id}-message`,
    type: "message",
    role: "assistant",
    content: [{ type: "output_text", text }],
  };
}

function functionCall(id, callId, name, argumentsValue, itemSuffix = callId) {
  return {
    id: `${id}-call-${itemSuffix}`,
    type: "function_call",
    call_id: callId,
    name,
    arguments: JSON.stringify(argumentsValue),
  };
}

function response(id, output, extra = {}) {
  return { id, status: "completed", output, ...extra };
}

function makeInterface({ tools = [], invoke = async () => ({ ok: true }) } = {}) {
  const invocations = [];
  let closeCount = 0;
  return {
    adapter: {
      async listTools() {
        return structuredClone(tools);
      },
      async invoke(name, input) {
        invocations.push({ name, input: structuredClone(input) });
        return invoke(name, input);
      },
      close() {
        closeCount += 1;
      },
    },
    invocations,
    get closeCount() {
      return closeCount;
    },
  };
}

const LOOKUP_TOOLS = [
  {
    name: "lookup",
    description: "Look up one value.",
    inputSchema: {
      type: "object",
      properties: { query: { type: "string" } },
      required: ["query"],
      additionalProperties: false,
    },
  },
];

const MODEL_LOOKUP_TOOLS = [
  {
    type: "function",
    name: "lookup",
    description: "Look up one value.",
    parameters: LOOKUP_TOOLS[0].inputSchema,
    strict: true,
  },
];

test("builds exact requests and carries one tool result into the next turn", async () => {
  const harness = makeInterface({
    tools: LOOKUP_TOOLS,
    invoke: async (name, input) => ({ name, value: `result:${input.query}` }),
  });
  const provider = createStubProvider({
    responses: [
      response("response-1", [
        message("response-1", "working"),
        functionCall("response-1", "call-1", "lookup", { query: "alpha" }),
      ]),
      response("response-2", [message("response-2", "finished")]),
    ],
  });

  const result = await runLoop({
    task: "Find alpha.",
    interfaceAdapter: harness.adapter,
    provider,
    model: "offline-model",
    instructions: "Use only the supplied tools.",
    maxTurns: 3,
    maxOutputTokens: 123,
  });

  assert.deepEqual(provider.requests[0], {
    model: "offline-model",
    instructions: "Use only the supplied tools.",
    input: [{ role: "user", content: "Find alpha." }],
    tools: MODEL_LOOKUP_TOOLS,
    parallel_tool_calls: false,
    max_output_tokens: 123,
  });
  assert.equal(provider.requests.length, 2);
  assert.deepEqual(harness.invocations, [{
    name: "lookup",
    input: { query: "alpha" },
  }]);
  assert.deepEqual(provider.requests[1].input.at(-1), {
    type: "function_call_output",
    call_id: "call-1",
    output: JSON.stringify({ name: "lookup", value: "result:alpha" }),
  });
  assert.equal(result.finalText, "finished");
  assert.equal(result.stopReason, "completed");
  assert.equal(result.turns, 2);
  assert.equal(result.providerCalls, 2);
  assert.equal(result.toolCalls, 1);
  assert.equal(result.retries, 0);
});

test("rejects duplicate response and function-call IDs without retrying", async () => {
  const harness = makeInterface();
  const duplicateResponseProvider = createStubProvider({
    responses: [
      response("response-1", [functionCall("response-1", "call-1", "lookup", {})]),
      response("response-1", [message("response-2", "never accepted")]),
    ],
  });
  await assert.rejects(
    runLoop({
      task: "Duplicate response.",
      interfaceAdapter: harness.adapter,
      provider: duplicateResponseProvider,
    }),
    (error) => error instanceof LoopError && /duplicate response id/.test(error.message),
  );
  assert.equal(duplicateResponseProvider.callCount, 2);

  const duplicateCallProvider = createStubProvider({
    responses: [response("response-3", [
        functionCall("response-3", "call-2", "lookup", {}),
        functionCall("response-3", "call-2", "lookup", {}, "second-item"),
    ])],
  });
  await assert.rejects(
    runLoop({
      task: "Duplicate call.",
      interfaceAdapter: harness.adapter,
      provider: duplicateCallProvider,
    }),
    (error) => error instanceof LoopError && /duplicate function call id/.test(error.message),
  );
  assert.equal(duplicateCallProvider.callCount, 1);
});

test("preserves incomplete status and rejects failed responses", async () => {
  const incompleteProvider = createStubProvider({
    responses: [response(
      "response-incomplete",
      [message("response-incomplete", "partial")],
      { status: "incomplete", incomplete_details: { reason: "max_output_tokens" } },
    )],
  });
  const incomplete = await runLoop({
    task: "Stop at an incomplete response.",
    interfaceAdapter: makeInterface().adapter,
    provider: incompleteProvider,
  });
  assert.equal(incomplete.status, "incomplete");
  assert.equal(incomplete.stopReason, "incomplete");
  assert.deepEqual(incomplete.incompleteDetails, { reason: "max_output_tokens" });
  assert.equal(incomplete.finalText, "partial");

  const failedProvider = createStubProvider({
    responses: [response(
      "response-failed",
      [],
      { status: "failed", error: { message: "provider rejected request" } },
    )],
  });
  await assert.rejects(
    runLoop({
      task: "Reject a failed response.",
      interfaceAdapter: makeInterface().adapter,
      provider: failedProvider,
    }),
    (error) => error instanceof LoopError && /provider response failed/.test(error.message),
  );
  assert.equal(failedProvider.callCount, 1);

  const missingIdProvider = createStubProvider({
    responses: [{ status: "completed", output: [] }],
  });
  await assert.rejects(
    runLoop({
      task: "Reject a response without an id.",
      interfaceAdapter: makeInterface().adapter,
      provider: missingIdProvider,
    }),
    (error) => error instanceof LoopError && /response id/.test(error.message),
  );
});

test("stops at turn and tool-call budgets", async () => {
  const turnHarness = makeInterface();
  const turnProvider = createStubProvider({
    responses: [response(
      "response-turn",
      [functionCall("response-turn", "call-turn", "lookup", {})],
    )],
  });
  const turnLimited = await runLoop({
    task: "One turn only.",
    interfaceAdapter: turnHarness.adapter,
    provider: turnProvider,
    maxTurns: 1,
  });
  assert.equal(turnLimited.stopReason, "turn_limit");
  assert.equal(turnLimited.providerCalls, 1);

  const toolHarness = makeInterface();
  const toolProvider = createStubProvider({
    responses: [response("response-tools", [
      functionCall("response-tools", "call-tool-1", "lookup", {}),
      functionCall("response-tools", "call-tool-2", "lookup", {}, "second-tool-item"),
    ])],
  });
  const toolLimited = await runLoop({
    task: "One tool call only.",
    interfaceAdapter: toolHarness.adapter,
    provider: toolProvider,
    maxToolCalls: 1,
  });
  assert.equal(toolLimited.stopReason, "tool_limit");
  assert.equal(toolLimited.toolCalls, 1);
  assert.equal(toolHarness.invocations.length, 1);
  assert.equal(toolProvider.callCount, 1);
});

test("propagates an abort during a provider call without retrying", async () => {
  const controller = new AbortController();
  const provider = createStubProvider({
    delayMs: 25,
    responses: [response("response-abort", [message("response-abort", "never returned")])],
  });
  const pending = runLoop({
    task: "Abort the provider call.",
    interfaceAdapter: makeInterface().adapter,
    provider,
    signal: controller.signal,
  });
  setTimeout(() => controller.abort(), 5);
  await assert.rejects(pending, (error) => error?.name === "AbortError");
  assert.equal(provider.requests.length, 1);
});

test(
  "records tool errors and truncates oversized tool output before the next request",
  async () => {
    const harness = makeInterface({
      tools: LOOKUP_TOOLS,
      invoke: async (name) => {
        if (name === "fail") throw new Error("controlled tool failure");
        return "abcdefghijklmnopqrstuvwxyz";
      },
    });
    const provider = createStubProvider({
      responses: [
        response("response-4", [
            functionCall("response-4", "call-4a", "fail", {}),
            functionCall("response-4", "call-4b", "lookup", {}),
        ]),
        response("response-5", [message("response-5", "continued")]),
      ],
    });

    const result = await runLoop({
      task: "Exercise tool failures.",
      interfaceAdapter: harness.adapter,
      provider,
      maxToolOutputChars: 5,
    });

    assert.deepEqual(result.toolResults, [
      { callId: "call-4a", name: "fail", ok: false, truncated: true, outputLength: 5 },
      { callId: "call-4b", name: "lookup", ok: true, truncated: true, outputLength: 5 },
    ]);
    assert.equal(result.truncated, true);
    assert.equal(provider.requests[1].input.at(-2).output, "{\"err");
    assert.equal(provider.requests[1].input.at(-1).output, "abcde");
    assert.equal(result.finalText, "continued");
  },
);

test("surfaces provider failures after one request and performs no hidden retry", async () => {
  const harness = makeInterface();
  const provider = createStubProvider({
    responses: [new ProviderError("provider is unavailable")],
  });

  await assert.rejects(
    runLoop({ task: "No retry.", interfaceAdapter: harness.adapter, provider }),
    (error) => error instanceof ProviderError && error.message === "provider is unavailable",
  );
  assert.equal(provider.callCount, 1);
  assert.equal(provider.requests.length, 1);
});

test("worker closes the interface on success and failure", async () => {
  const successHarness = makeInterface();
  const successProvider = createStubProvider({
    responses: [response("response-6", [message("response-6", "done")])],
  });
  await runWorker({
    task: "Close after success.",
    interfaceAdapter: successHarness.adapter,
    provider: successProvider,
  });
  assert.equal(successHarness.closeCount, 1);

  const failureHarness = makeInterface();
  const failureProvider = createStubProvider({ responses: [new ProviderError("failure")] });
  await assert.rejects(runWorker({
    task: "Close after failure.",
    interfaceAdapter: failureHarness.adapter,
    provider: failureProvider,
  }));
  assert.equal(failureHarness.closeCount, 1);

  const doubleFailureHarness = makeInterface();
  doubleFailureHarness.adapter.close = () => {
    throw new Error("cleanup failure");
  };
  const doubleFailureProvider = createStubProvider({
    responses: [new ProviderError("loop failure")],
  });
  await assert.rejects(runWorker({
    task: "Preserve both failures.",
    interfaceAdapter: doubleFailureHarness.adapter,
    provider: doubleFailureProvider,
  }), (error) => (
    error instanceof AggregateError
    && error.errors.some((item) => item.message === "loop failure")
    && error.errors.some((item) => item.message === "cleanup failure")
  ));
});
