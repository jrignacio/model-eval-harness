import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

import { startFixtureServer } from "./fixture/server.mjs";
import { createDomInterface } from "./interfaces/dom.mjs";
import { createWebMcpInterface } from "./interfaces/webmcp.mjs";
import { runWorker } from "./worker.mjs";
import { ProviderError } from "./provider.mjs";

const CHROME_PATH = process.env.CHROME_PATH
  ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const WEBMCP_FLAGS = [
  ["--enable-features=WebMCPTesting,DevToolsWebMCPSupport"],
  ["--enable-features=WebMCP"],
  ["--enable-experimental-web-platform-features"],
];
const INPUT_PRICE = 0.20;
const CACHED_INPUT_PRICE = 0.02;
const CACHE_WRITE_PRICE = 0.25;
const OUTPUT_PRICE = 1.20;
const MODEL = "gpt-5.6-luna";
const REASONING_EFFORT = "none";
const MAX_REQUESTS = 10;
const MAX_TOOL_CALLS = 60;
const MAX_OUTPUT_TOKENS = 4096;
const MAX_INPUT_TOKENS = 20_000;
const ATTEMPT_COST_CEILING = 0.09;

function assertObject(value, message) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(message);
  }
}

function clone(value) {
  return structuredClone(value);
}

function waitForOpen(webSocket) {
  if (webSocket.readyState === WebSocket.OPEN) return Promise.resolve();
  return new Promise((resolve, reject) => {
    webSocket.addEventListener("open", resolve, { once: true });
    webSocket.addEventListener("error", reject, { once: true });
  });
}

class CdpConnection {
  constructor(url) {
    this.webSocket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Set();
    this.webSocket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (message.id !== undefined) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message));
        else pending.resolve(message.result);
        return;
      }
      for (const listener of this.listeners) listener(message);
    });
  }

  async open() {
    await waitForOpen(this.webSocket);
  }

  send(method, params = {}, sessionId) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.webSocket.send(JSON.stringify({
        id,
        method,
        params,
        ...(sessionId ? { sessionId } : {}),
      }));
    });
  }

  async evaluate(expression, sessionId) {
    const result = await this.send(
      "Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true, userGesture: true },
      sessionId,
    );
    if (result.exceptionDetails) {
      throw new Error(
        result.exceptionDetails.exception?.description ?? "Runtime evaluation failed",
      );
    }
    return result.result?.value;
  }

  waitForEvent(method, sessionId, timeoutMs = 10_000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.listeners.delete(listener);
        reject(new Error(`Timed out waiting for ${method}`));
      }, timeoutMs);
      const listener = (message) => {
        if (message.method !== method) return;
        if (sessionId && message.sessionId !== sessionId) return;
        clearTimeout(timer);
        this.listeners.delete(listener);
        resolve(message.params ?? {});
      };
      this.listeners.add(listener);
    });
  }

  close() {
    if (this.webSocket.readyState === WebSocket.OPEN) this.webSocket.close();
  }
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const port = address.port;
  await new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
  return port;
}

async function getJson(port, endpoint) {
  const response = await fetch(`http://127.0.0.1:${port}${endpoint}`);
  if (!response.ok) throw new Error(`Chrome endpoint ${endpoint} returned ${response.status}`);
  return response.json();
}

async function waitForChrome(port, child) {
  const deadline = Date.now() + 10_000;
  let lastError;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Chrome exited before CDP was ready with code ${child.exitCode}`);
    }
    try {
      return await getJson(port, "/json/version");
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error(`Timed out waiting for Chrome CDP: ${lastError?.message}`);
}

async function closeChrome(child, profile) {
  if (child && child.exitCode === null) {
    child.kill("SIGTERM");
    await Promise.race([once(child, "exit"), new Promise((resolve) => setTimeout(resolve, 2_000))]);
    if (child.exitCode === null) child.kill("SIGKILL");
  }
  if (profile) await rm(profile, { recursive: true, force: true });
}

async function launchChrome(origin) {
  const profile = await mkdtemp(path.join(tmpdir(), "agent-eval-chrome-live-"));
  const port = await freePort();
  let lastError;
  for (const flags of WEBMCP_FLAGS) {
    const child = spawn(CHROME_PATH, [
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${profile}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-background-networking",
      "--disable-extensions",
      "--headless=new",
      ...flags,
      "about:blank",
    ], { stdio: "ignore" });
    try {
      const version = await waitForChrome(port, child);
      const browser = new CdpConnection(version.webSocketDebuggerUrl);
      await browser.open();
      const target = await browser.send("Target.createTarget", { url: "about:blank" });
      const attached = await browser.send(
        "Target.attachToTarget",
        { targetId: target.targetId, flatten: true },
      );
      await browser.send("Page.enable", {}, attached.sessionId);
      await browser.send("Runtime.enable", {}, attached.sessionId);
      const loaded = browser.waitForEvent
        ? browser.waitForEvent("Page.loadEventFired", attached.sessionId)
        : null;
      await browser.send("Page.navigate", { url: `${origin}/` }, attached.sessionId);
      if (loaded) await loaded;
      return {
        browser,
        child,
        profile,
        sessionId: attached.sessionId,
        flags,
        version,
      };
    } catch (error) {
      lastError = error;
      await closeChrome(child, profile);
    }
  }
  await rm(profile, { recursive: true, force: true });
  throw new Error(`Live browser could not start: ${lastError?.message}`);
}

function createPageAdapter(browser, sessionId, origin) {
  const snapshotExpression = `(() => {
    const elements = [];
    const add = (key, element, role, label, text = element?.textContent?.trim() ?? "") => {
      if (!element) throw new Error("missing fixture element: " + key);
      const descriptor = { key, text, role, label };
      if ("value" in element) descriptor.value = String(element.value);
      if (element instanceof HTMLSelectElement) {
        descriptor.options = [...element.options].map((option) => option.value);
      }
      elements.push(descriptor);
    };
    add("products", document.querySelector("#products"), "region", "Products");
    add("cart-product", document.querySelector("#cart-product"), "combobox", "Product");
    add("cart-quantity", document.querySelector("#cart-quantity"), "spinbutton", "Quantity");
    add(
      "cart-submit",
      document.querySelector("#cart-form button[type=submit]"),
      "button",
      "Set quantity",
    );
    add(
      "shipping-recipient",
      document.querySelector("#shipping-recipient"),
      "textbox",
      "Shipping recipient",
    );
    add(
      "shipping-address",
      document.querySelector("#shipping-address"),
      "textbox",
      "Shipping address",
    );
    add(
      "shipping-note",
      document.querySelector("#shipping-note"),
      "textbox",
      "Shipping note",
    );
    add(
      "shipping-submit",
      document.querySelector("#shipping-form button[type=submit]"),
      "button",
      "Save shipping",
    );
    add("status", document.querySelector("#status"), "status", "Status");
    return { origin: location.origin, elements };
  })()`;

  function selectorFor(key) {
    const selectors = {
      "cart-product": "#cart-product",
      "cart-quantity": "#cart-quantity",
      "cart-submit": "#cart-form button[type=submit]",
      "shipping-recipient": "#shipping-recipient",
      "shipping-address": "#shipping-address",
      "shipping-note": "#shipping-note",
      "shipping-submit": "#shipping-form button[type=submit]",
    };
    const selector = selectors[key];
    if (!selector) throw new Error(`DOM control is not actionable: ${key}`);
    return selector;
  }

  function callElement(key, operation) {
    const selector = JSON.stringify(selectorFor(key));
    return browser.evaluate(`(() => {
      const element = document.querySelector(${selector});
      if (!element) throw new Error("missing DOM control");
      ${operation}
      return { status: "ok" };
    })()`, sessionId);
  }

  return {
    async getOrigin() {
      return browser.evaluate("location.origin", sessionId);
    },
    async getNavigationToken() {
      return browser.evaluate("location.href", sessionId);
    },
    async snapshot() {
      return browser.evaluate(snapshotExpression, sessionId);
    },
    async click(key) {
      return callElement(key, "element.click();");
    },
    async fill(key, value) {
      return callElement(
        key,
        `element.value = ${JSON.stringify(value)};
         element.dispatchEvent(new Event("input", { bubbles: true }));`,
      );
    },
    async select(key, value) {
      return callElement(
        key,
        `element.value = ${JSON.stringify(value)};
         element.dispatchEvent(new Event("input", { bubbles: true }));
         element.dispatchEvent(new Event("change", { bubbles: true }));`,
      );
    },
    close() {},
  };
}

function createWebMcpContext(browser, sessionId) {
  return {
    async getTools() {
      return browser.evaluate(`(async () => {
        const tools = await document.modelContext.getTools();
        return tools.map((tool) => ({
          name: tool.name,
          description: tool.description,
          inputSchema: typeof tool.inputSchema === "string"
            ? JSON.parse(tool.inputSchema)
            : tool.inputSchema,
          origin: tool.origin,
        }));
      })()`, sessionId);
    },
    async executeTool(tool, input) {
      const name = JSON.stringify(tool.name);
      // Chrome 153 still accepts the deprecated JSON-text form. The common
      // interface already gives us that text; embed it once, not twice.
      const serialized = JSON.stringify(
        typeof input === "string" ? input : JSON.stringify(input),
      );
      return browser.evaluate(`(async () => {
        const tools = await document.modelContext.getTools();
        const tool = tools.find((candidate) => candidate.name === ${name});
        if (!tool) throw new Error("native tool is unavailable");
        return document.modelContext.executeTool(tool, ${serialized});
      })()`, sessionId);
    },
    close() {},
  };
}

function estimateInputTokens(request) {
  return Math.ceil(Buffer.byteLength(JSON.stringify(request), "utf8") / 3);
}

function usageCost(usage) {
  const input = usage.input_tokens;
  const output = usage.output_tokens;
  const details = usage.input_tokens_details ?? {};
  const cached = Number.isInteger(details.cached_tokens) ? details.cached_tokens : 0;
  const cacheWrite = Number.isInteger(details.cache_write_tokens)
    ? details.cache_write_tokens
    : 0;
  const uncached = Math.max(0, input - cached - cacheWrite);
  return (
    uncached * INPUT_PRICE
    + cached * CACHED_INPUT_PRICE
    + cacheWrite * CACHE_WRITE_PRICE
    + output * OUTPUT_PRICE
  ) / 1_000_000;
}

function assertUsage(usage) {
  assertObject(usage, "provider usage is missing");
  for (const field of ["input_tokens", "output_tokens"]) {
    if (!Number.isInteger(usage[field]) || usage[field] < 0) {
      throw new ProviderError(`provider usage.${field} is unavailable`);
    }
  }
}

class BudgetedOpenAIProvider {
  constructor({ apiKey, baseUrl = "https://api.openai.com/v1" } = {}) {
    if (typeof apiKey !== "string" || apiKey.length === 0) {
      throw new ProviderError("OPENAI_API_KEY is unavailable");
    }
    this.apiKey = apiKey;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.calls = 0;
    this.spent = 0;
    this.records = [];
    this.returnedModel = null;
  }

  async complete(request, { signal } = {}) {
    if (signal?.aborted) throw new ProviderError("provider request was aborted");
    if (this.calls >= MAX_REQUESTS) {
      throw new ProviderError("pilot model-request limit reached");
    }
    const estimatedInput = estimateInputTokens(request);
    if (estimatedInput > MAX_INPUT_TOKENS) {
      throw new ProviderError(`input guard exceeded: estimated ${estimatedInput} tokens`);
    }
    const reserved = (
      MAX_INPUT_TOKENS * INPUT_PRICE + MAX_OUTPUT_TOKENS * OUTPUT_PRICE
    ) / 1_000_000;
    if (this.spent + reserved > ATTEMPT_COST_CEILING + 1e-12) {
      throw new ProviderError("attempt cost ceiling would be exceeded");
    }

    const body = {
      ...clone(request),
      model: MODEL,
      reasoning: { effort: REASONING_EFFORT },
      store: false,
    };
    let response;
    try {
      response = await fetch(`${this.baseUrl}/responses`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${this.apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal,
      });
    } catch (error) {
      if (error?.name === "AbortError") throw error;
      throw new ProviderError(`OpenAI request failed: ${error.message}`);
    }
    const text = await response.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch (error) {
      throw new ProviderError(`OpenAI returned non-JSON status ${response.status}`);
    }
    if (!response.ok) {
      const message = payload?.error?.message ?? `HTTP ${response.status}`;
      throw new ProviderError(`OpenAI request rejected: ${message}`);
    }
    if (payload.model !== MODEL) {
      throw new ProviderError(`returned model drifted: expected ${MODEL}, got ${payload.model}`);
    }
    assertUsage(payload.usage);
    if (payload.usage.input_tokens > MAX_INPUT_TOKENS) {
      throw new ProviderError("reported input tokens exceeded the pilot guard");
    }
    const cost = usageCost(payload.usage);
    if (this.spent + cost > ATTEMPT_COST_CEILING + 1e-12) {
      throw new ProviderError("reported cost exceeded the attempt ceiling");
    }
    this.calls += 1;
    this.spent += cost;
    this.returnedModel = payload.model;
    this.records.push({
      request_index: this.calls,
      response_id: payload.id,
      model: payload.model,
      usage: clone(payload.usage),
      cost_usd: cost,
    });
    return payload;
  }
}

function decodeToolOutput(value) {
  if (typeof value !== "string") return clone(value);
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function normalizeTrace(trace) {
  const result = [];
  const requestIds = new Map();
  for (const event of trace) {
    if (event.type === "model_request") {
      const requestId = `live-request-${event.turn}`;
      requestIds.set(event.turn, requestId);
      result.push({
        type: "model_request",
        request_id: requestId,
        model: event.request.model,
        attempt_index: event.turn - 1,
        request: clone(event.request),
      });
      continue;
    }
    if (event.type === "model_response") {
      const response = event.response;
      result.push({
        type: "model_response",
        request_id: requestIds.get(event.turn) ?? `live-request-${event.turn}`,
        response_id: response.id,
        status: response.status,
        response: clone(response),
      });
      for (const item of response.output ?? []) {
        if (item.type !== "function_call") continue;
        result.push({
          type: "tool_call",
          call_id: item.call_id,
          name: item.name,
          arguments: JSON.parse(item.arguments),
        });
      }
      continue;
    }
    if (event.type === "tool_result") {
      result.push({
        type: "tool_result",
        call_id: event.callId,
        name: event.name,
        status: event.ok ? "ok" : "error",
        result: decodeToolOutput(event.output),
      });
    }
  }
  return result;
}

async function runAttempt(input) {
  assertObject(input, "live attempt input must be an object");
  assertObject(input.task, "live attempt task is required");
  if (!Object.hasOwn(input, "initial_state")) throw new Error("initial state is required");
  if (input.arm !== "dom" && input.arm !== "webmcp") {
    throw new Error(`unsupported live arm: ${input.arm}`);
  }

  const fixture = await startFixtureServer();
  let chrome;
  const started = performance.now();
  try {
    chrome = await launchChrome(fixture.origin);
    const nativeStatus = await chrome.browser.evaluate(
      "window.__fixtureAppReady",
      chrome.sessionId,
    );
    if (!nativeStatus?.available) {
      throw new Error("native WebMCP is unavailable; live pilot stopped");
    }
    const initialState = await chrome.browser.evaluate(
      "window.__fixtureApp.reset()",
      chrome.sessionId,
    );
    if (JSON.stringify(initialState) !== JSON.stringify(input.initial_state)) {
      throw new Error("fixture initial state does not match the seeded task state");
    }

    const page = createPageAdapter(chrome.browser, chrome.sessionId, fixture.origin);
    const context = createWebMcpContext(chrome.browser, chrome.sessionId);
    const interfaceAdapter = input.arm === "dom"
      ? createDomInterface({ origin: fixture.origin, page })
      : createWebMcpInterface({ origin: fixture.origin, context });
    const provider = new BudgetedOpenAIProvider({
      apiKey: process.env.OPENAI_API_KEY,
      baseUrl: process.env.OPENAI_BASE_URL,
    });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 110_000);
    let loop;
    try {
      loop = await runWorker({
        task: input.task.prompt,
        interfaceAdapter,
        provider,
        model: MODEL,
        instructions: (
          "Complete the user's shop task using only the supplied tools. "
          + "Observe before acting. Do not invent tool results. "
          + "Give a concise final answer after the task is complete."
        ),
        maxTurns: MAX_REQUESTS,
        maxToolCalls: MAX_TOOL_CALLS,
        maxOutputTokens: MAX_OUTPUT_TOKENS,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    const finalState = await chrome.browser.evaluate(
      "window.__fixtureApp.getState()",
      chrome.sessionId,
    );
    return {
      answer: loop.finalText,
      final_state: finalState,
      trace: normalizeTrace(loop.trace),
      metrics: {
        model_requests: loop.providerCalls,
        tool_invocations: loop.toolCalls,
        elapsed_ms: Math.round((performance.now() - started) * 1000) / 1000,
        provider_calls: loop.providerCalls,
      },
      usage: provider.records,
      cost_usd: provider.spent,
      returned_model: provider.returnedModel,
      provider: "openai-responses",
      configuration: {
        model: MODEL,
        reasoning_effort: REASONING_EFFORT,
        max_model_requests: MAX_REQUESTS,
        max_tool_invocations: MAX_TOOL_CALLS,
        max_input_tokens: MAX_INPUT_TOKENS,
        max_output_tokens: MAX_OUTPUT_TOKENS,
        provider_retries: 0,
        store: false,
        browser_origin: fixture.origin,
        chrome_version: chrome.version.Browser,
        webmcp_flags: chrome.flags,
      },
    };
  } finally {
    if (chrome) {
      chrome.browser.close();
      await closeChrome(chrome.child, chrome.profile);
    }
    await fixture.close();
  }
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

try {
  const input = await readStdin();
  const result = await runAttempt(input);
  process.stdout.write(JSON.stringify(result));
} catch (error) {
  process.stderr.write(`${error.stack ?? error.message ?? String(error)}\n`);
  process.exitCode = 1;
}
