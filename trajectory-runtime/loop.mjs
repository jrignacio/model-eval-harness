import { ProviderError } from "./provider.mjs";

export class LoopError extends Error {
  constructor(message, options = {}) {
    super(message, options);
    this.name = "LoopError";
  }
}

function clone(value) {
  return structuredClone(value);
}

function assertObject(value, message) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new LoopError(message);
  }
}

function assertText(value, field) {
  if (typeof value !== "string" || value.length === 0) {
    throw new LoopError(`${field} must be a non-empty string`);
  }
}

function assertNotAborted(signal) {
  if (signal?.aborted) {
    const error = new Error("model loop aborted");
    error.name = "AbortError";
    throw error;
  }
}

function isAbortError(error) {
  return error?.name === "AbortError";
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function serialize(value) {
  if (typeof value === "string") return value;
  try {
    const json = JSON.stringify(value);
    return json === undefined ? "undefined" : json;
  } catch (error) {
    throw new LoopError(`tool result is not JSON-serializable: ${errorMessage(error)}`);
  }
}

function truncate(value, limit) {
  if (value.length <= limit) return { value, truncated: false };
  return { value: value.slice(0, limit), truncated: true };
}

function parseArguments(value, callId) {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return clone(value);
  }
  if (typeof value !== "string") {
    throw new LoopError(`function call arguments must be JSON text: ${callId}`);
  }
  try {
    const parsed = JSON.parse(value);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("arguments must decode to an object");
    }
    return parsed;
  } catch (error) {
    throw new LoopError(`invalid function call arguments for ${callId}: ${errorMessage(error)}`);
  }
}

function registerUniqueId(seenIds, value, label) {
  if (value === undefined) return;
  assertText(value, `${label} id`);
  if (seenIds.has(value)) {
    throw new LoopError(`duplicate ${label} id: ${value}`);
  }
  seenIds.add(value);
}

const RESPONSE_STATUSES = new Set(["completed", "incomplete", "failed"]);

function modelTools(tools) {
  if (!Array.isArray(tools)) throw new LoopError("interface tools must be an array");
  return tools.map((tool) => {
    assertObject(tool, "interface tool must be an object");
    assertText(tool.name, "interface tool name");
    assertText(tool.description, `${tool.name} description`);
    assertObject(tool.inputSchema, `${tool.name} inputSchema`);
    return {
      type: "function",
      name: tool.name,
      description: tool.description,
      parameters: clone(tool.inputSchema),
      strict: true,
    };
  });
}

function validateResponse(response, seenIds) {
  assertObject(response, "provider response must be an object");
  assertText(response.id, "provider response id");
  assertText(response.status, "provider response status");
  if (!RESPONSE_STATUSES.has(response.status)) {
    throw new LoopError(`unsupported provider response status: ${response.status}`);
  }
  if (response.status === "failed") {
    const detail = response.error?.message ?? response.error ?? "unknown provider failure";
    throw new LoopError(`provider response failed: ${errorMessage(detail)}`);
  }
  registerUniqueId(seenIds, response.id, "response");
  if (!Array.isArray(response.output)) {
    throw new LoopError("provider response output must be an array");
  }

  const calls = [];
  for (const item of response.output) {
    assertObject(item, "provider output item must be an object");
    registerUniqueId(seenIds, item.id, "output item");
    if (item.type !== "function_call") continue;
    assertText(item.call_id, "function call call_id");
    registerUniqueId(seenIds, item.call_id, "function call");
    assertText(item.name, "function call name");
    calls.push({
      callId: item.call_id,
      name: item.name,
      arguments: parseArguments(item.arguments, item.call_id),
    });
  }
  return calls;
}

function extractText(output) {
  const texts = [];
  for (const item of output) {
    if (item.type === "output_text" && typeof item.text === "string") {
      texts.push(item.text);
      continue;
    }
    if (item.type !== "message" || !Array.isArray(item.content)) continue;
    for (const part of item.content) {
      if (part?.type === "output_text" && typeof part.text === "string") {
        texts.push(part.text);
      }
    }
  }
  return texts;
}

function validateLimits({ maxTurns, maxToolCalls, maxToolOutputChars, maxOutputTokens }) {
  for (const [name, value] of Object.entries({
    maxTurns,
    maxToolCalls,
    maxToolOutputChars,
    maxOutputTokens,
  })) {
    if (!Number.isInteger(value) || value < 0) {
      throw new TypeError(`${name} must be a non-negative integer`);
    }
  }
}

async function emitEvent(trace, emit, event) {
  const copy = clone(event);
  trace.push(copy);
  if (emit) await emit(clone(copy));
}

function makeRequest({ model, instructions, history, tools, maxOutputTokens }) {
  return {
    model,
    instructions,
    input: clone(history),
    tools: modelTools(tools),
    parallel_tool_calls: false,
    max_output_tokens: maxOutputTokens,
  };
}

/**
 * Run one model/interface loop. Provider failures are surfaced immediately;
 * this function never retries a model request or a tool call.
 */
export async function runLoop({
  task,
  interfaceAdapter,
  provider,
  model = "stub-model",
  instructions = "",
  maxTurns = 8,
  maxToolCalls = 32,
  maxToolOutputChars = 20_000,
  maxOutputTokens = 4_096,
  emit,
  signal,
} = {}) {
  assertText(task, "task");
  assertText(model, "model");
  if (typeof instructions !== "string") throw new TypeError("instructions must be a string");
  if (!interfaceAdapter || typeof interfaceAdapter.listTools !== "function") {
    throw new TypeError("interfaceAdapter.listTools is required");
  }
  if (typeof interfaceAdapter.invoke !== "function") {
    throw new TypeError("interfaceAdapter.invoke is required");
  }
  if (!provider || typeof provider.complete !== "function") {
    throw new TypeError("provider.complete is required");
  }
  if (emit !== undefined && typeof emit !== "function") {
    throw new TypeError("emit must be a function");
  }
  validateLimits({ maxTurns, maxToolCalls, maxToolOutputChars, maxOutputTokens });

  const history = [{ role: "user", content: task }];
  const trace = [];
  const toolResults = [];
  const usage = [];
  const seenIds = new Set();
  let finalText = "";
  let responseStatus = null;
  let incompleteDetails = null;
  let providerCalls = 0;
  let toolCalls = 0;
  let stopReason = "turn_limit";

  for (let turn = 0; turn < maxTurns; turn += 1) {
    assertNotAborted(signal);
    const tools = await interfaceAdapter.listTools({ signal });
    assertNotAborted(signal);
    const request = makeRequest({
      model,
      instructions,
      history,
      tools,
      maxOutputTokens,
    });
    await emitEvent(trace, emit, { type: "model_request", turn: turn + 1, request });

    let response;
    try {
      providerCalls += 1;
      response = await provider.complete(request, { signal });
    } catch (error) {
      if (isAbortError(error)) throw error;
      await emitEvent(trace, emit, {
        type: "provider_error",
        turn: turn + 1,
        message: errorMessage(error),
      });
      if (error instanceof ProviderError) throw error;
      throw new ProviderError(`provider request failed: ${errorMessage(error)}`, { cause: error });
    }

    assertNotAborted(signal);
    const calls = validateResponse(response, seenIds);
    responseStatus = response.status;
    if (response.usage !== undefined) usage.push(clone(response.usage));
    history.push(...clone(response.output));
    await emitEvent(trace, emit, {
      type: "model_response",
      turn: turn + 1,
      response: clone(response),
    });

    const responseText = extractText(response.output);
    if (responseText.length > 0) finalText = responseText.join("\n");
    if (response.status === "incomplete") {
      incompleteDetails = response.incomplete_details === undefined
        ? null
        : clone(response.incomplete_details);
      stopReason = "incomplete";
      break;
    }
    if (calls.length === 0) {
      stopReason = "completed";
      break;
    }

    for (const call of calls) {
      if (toolCalls >= maxToolCalls) {
        stopReason = "tool_limit";
        break;
      }
      assertNotAborted(signal);
      let rawOutput;
      let ok = true;
      try {
        toolCalls += 1;
        rawOutput = await interfaceAdapter.invoke(call.name, call.arguments, { signal });
      } catch (error) {
        if (isAbortError(error)) throw error;
        ok = false;
        rawOutput = { error: errorMessage(error) };
      }
      const serialized = serialize(rawOutput);
      const limited = truncate(serialized, maxToolOutputChars);
      toolResults.push({
        callId: call.callId,
        name: call.name,
        ok,
        truncated: limited.truncated,
        outputLength: limited.value.length,
      });
      history.push({
        type: "function_call_output",
        call_id: call.callId,
        output: limited.value,
      });
      await emitEvent(trace, emit, {
        type: "tool_result",
        turn: turn + 1,
        callId: call.callId,
        name: call.name,
        ok,
        output: limited.value,
        truncated: limited.truncated,
      });
    }
    if (stopReason === "tool_limit") break;
  }

  return {
    finalText,
    status: responseStatus,
    incompleteDetails,
    stopReason,
    turns: trace.filter((event) => event.type === "model_response").length,
    providerCalls,
    toolCalls,
    retries: 0,
    usage,
    toolResults,
    truncated: toolResults.some((result) => result.truncated),
    history: clone(history),
    trace: clone(trace),
  };
}
