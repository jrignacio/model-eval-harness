import assert from "node:assert/strict";
import test from "node:test";

import {
  createDomInterface,
  InterfaceError as DomInterfaceError,
  StaleReferenceError,
} from "../interfaces/dom.mjs";
import {
  ALLOWED_TOOL_NAMES,
  createWebMcpInterface,
  InterfaceError as WebMcpInterfaceError,
} from "../interfaces/webmcp.mjs";

const ORIGIN = "http://127.0.0.1:4173";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

function domPage() {
  const calls = [];
  return {
    origin: ORIGIN,
    navigationToken: "document-1",
    calls,
    async snapshot() {
      return {
        origin: ORIGIN,
        elements: [
          {
            key: "cart-product",
            text: "Blue Mug",
            role: "combobox",
            label: "Product",
            value: "mug-blue",
            options: ["mug-blue"],
          },
          {
            key: "cart-submit",
            text: "Set quantity",
            role: "button",
            label: "Set quantity",
          },
        ],
      };
    },
    async click(key) { calls.push(["click", key]); },
    async fill(key, value) { calls.push(["fill", key, value]); },
    async select(key, value) { calls.push(["select", key, value]); },
  };
}

function webmcpTools(origin = ORIGIN) {
  const schemas = {
    get_cart: { type: "object", properties: {}, additionalProperties: false },
    get_shipping: { type: "object", properties: {}, additionalProperties: false },
    list_products: { type: "object", properties: {}, additionalProperties: false },
    save_shipping: {
      type: "object",
      properties: {
        recipient: { type: "string" },
        address: { type: "string" },
        note: { type: "string" },
      },
      required: ["recipient", "address", "note"],
      additionalProperties: false,
    },
    set_cart_quantity: {
      type: "object",
      properties: {
        productId: { type: "string" },
        quantity: { type: "integer" },
      },
      required: ["productId", "quantity"],
      additionalProperties: false,
    },
  };
  return ALLOWED_TOOL_NAMES.map((name) => ({
    name,
    origin,
    description: `Description for ${name}`,
    inputSchema: schemas[name],
    annotations: {},
    window: {},
  }));
}

test("DOM interface exposes opaque references and rejects stale or arbitrary actions", async () => {
  const page = domPage();
  const iface = createDomInterface({ origin: ORIGIN, page });
  const tools = await iface.listTools();
  assert.deepEqual(tools.map((tool) => tool.name), ["snapshot", "click", "fill", "select"]);
  const snapshot = await iface.invoke("snapshot", {});
  assert.deepEqual(Object.keys(snapshot), ["elements"]);
  assert.equal(snapshot.elements[0].ref.startsWith("dom-"), true);
  assert.equal(snapshot.elements[0].text, "Blue Mug");
  assert.equal("key" in snapshot.elements[0], false);

  await iface.invoke("select", { ref: snapshot.elements[0].ref, value: "mug-blue" });
  await iface.invoke("click", { ref: snapshot.elements[1].ref });
  assert.deepEqual(page.calls, [
    ["select", "cart-product", "mug-blue"],
    ["click", "cart-submit"],
  ]);

  const oldReference = snapshot.elements[0].ref;
  await iface.invoke("snapshot", {});
  await assert.rejects(
    iface.invoke("click", { ref: oldReference }),
    StaleReferenceError,
  );
  await assert.rejects(
    iface.invoke("click", { ref: "dom-2-1", selector: "#cart-submit" }),
    DomInterfaceError,
  );
  const aborted = new AbortController();
  aborted.abort();
  await assert.rejects(
    iface.invoke("snapshot", {}, { signal: aborted.signal }),
    (error) => error.name === "AbortError",
  );
  iface.close();
  await assert.rejects(iface.listTools(), /closed/);
});

test("DOM interface fails closed on wrong origin and hidden snapshot fields", async () => {
  const page = domPage();
  const iface = createDomInterface({ origin: ORIGIN, page });
  const snapshot = await iface.invoke("snapshot", {});
  const oldReference = snapshot.elements[0].ref;

  page.snapshot = async () => ({
    origin: ORIGIN,
    elements: [{
      key: "x",
      text: "x",
      role: "button",
      label: "x",
      evaluatorAnswer: "secret",
    }],
  });
  await assert.rejects(iface.invoke("snapshot", {}), /unsupported fields/);
  await assert.rejects(iface.invoke("click", { ref: oldReference }), StaleReferenceError);

  page.origin = "http://attacker.test";
  await assert.rejects(iface.invoke("snapshot", {}), /origin mismatch/);
});

test("DOM snapshots publish atomically and expire on same-origin navigation", async () => {
  const page = domPage();
  const pending = [];
  page.snapshot = () => {
    const operation = deferred();
    pending.push(operation);
    return operation.promise;
  };
  const iface = createDomInterface({ origin: ORIGIN, page });
  const first = iface.invoke("snapshot", {});
  const second = iface.invoke("snapshot", {});
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(pending.length, 2);

  pending[1].resolve({
    origin: ORIGIN,
    elements: [{ key: "new-button", text: "New", role: "button", label: "New" }],
  });
  const current = await second;
  pending[0].resolve({
    origin: ORIGIN,
    elements: [{ key: "old-button", text: "Old", role: "button", label: "Old" }],
  });
  await assert.rejects(first, /superseded/);
  await iface.invoke("click", { ref: current.elements[0].ref });
  assert.deepEqual(page.calls, [["click", "new-button"]]);

  page.navigationToken = "document-2";
  await assert.rejects(
    iface.invoke("click", { ref: current.elements[0].ref }),
    StaleReferenceError,
  );
});

test("DOM aborted and closed snapshots cannot publish references", async () => {
  const page = domPage();
  const pending = deferred();
  page.snapshot = () => pending.promise;
  const iface = createDomInterface({ origin: ORIGIN, page });
  const controller = new AbortController();
  const request = iface.invoke("snapshot", {}, { signal: controller.signal });
  await new Promise((resolve) => setImmediate(resolve));
  controller.abort();
  await assert.rejects(request, (error) => error.name === "AbortError");
  pending.resolve({
    origin: ORIGIN,
    elements: [{ key: "aborted", text: "Aborted", role: "button", label: "Aborted" }],
  });
  await new Promise((resolve) => setImmediate(resolve));
  await assert.rejects(iface.invoke("click", { ref: "dom-1-1" }), StaleReferenceError);

  const closingPage = domPage();
  const closingPending = deferred();
  closingPage.snapshot = () => closingPending.promise;
  const closing = createDomInterface({ origin: ORIGIN, page: closingPage });
  const closingRequest = closing.invoke("snapshot", {});
  await new Promise((resolve) => setImmediate(resolve));
  closing.close();
  closingPending.resolve({ origin: ORIGIN, elements: [] });
  await assert.rejects(closingRequest, /closed/);
});

test("WebMCP interface discovers only the exact same-origin tool set", async () => {
  const calls = [];
  const context = {
    async getTools() { return webmcpTools(); },
    async executeTool(tool, input) {
      calls.push([tool.name, input]);
      return JSON.stringify({ ok: true, tool: tool.name });
    },
  };
  const iface = createWebMcpInterface({ origin: ORIGIN, context });
  const tools = await iface.listTools();
  assert.deepEqual(tools.map((tool) => tool.name), ALLOWED_TOOL_NAMES);
  assert.equal("window" in tools[0], false);
  assert.equal("annotations" in tools[0], false);
  assert.deepEqual(
    await iface.invoke("set_cart_quantity", { productId: "mug-blue", quantity: 2 }),
    {
      ok: true,
      tool: "set_cart_quantity",
    },
  );
  assert.deepEqual(calls, [["set_cart_quantity", '{"productId":"mug-blue","quantity":2}']]);
  await assert.rejects(
    iface.invoke("set_cart_quantity", { productId: "mug-blue", quantity: 2, extra: true }),
    WebMcpInterfaceError,
  );
  await assert.rejects(
    iface.invoke("set_cart_quantity", { productId: "mug-blue" }),
    WebMcpInterfaceError,
  );
  await assert.rejects(iface.invoke("get_cart", null), WebMcpInterfaceError);
  await assert.rejects(
    iface.invoke("set_cart_quantity", { productId: "mug-blue", quantity: -1 }),
    WebMcpInterfaceError,
  );
  await assert.rejects(iface.invoke("secret_tool", {}), /unsupported WebMCP tool/);
  const aborted = new AbortController();
  aborted.abort();
  await assert.rejects(
    iface.invoke("get_cart", {}, { signal: aborted.signal }),
    (error) => error.name === "AbortError",
  );
  iface.close();
  await assert.rejects(iface.listTools(), /closed/);
});

test("WebMCP interface fails closed on wrong-origin and unsupported tools", async () => {
  let mode = "valid";
  let executions = 0;
  const changingContext = {
    async getTools() {
      return mode === "valid" ? webmcpTools() : webmcpTools("http://attacker.test");
    },
    async executeTool() {
      executions += 1;
      return "{}";
    },
  };
  const changing = createWebMcpInterface({ origin: ORIGIN, context: changingContext });
  await changing.listTools();
  mode = "wrong-origin";
  await assert.rejects(changing.listTools(), /origin mismatch/);
  await assert.rejects(changing.invoke("get_cart", {}), /origin mismatch/);
  assert.equal(executions, 0);

  const wrongSchema = webmcpTools();
  wrongSchema[0] = {
    ...wrongSchema[0],
    inputSchema: { ...wrongSchema[0].inputSchema, additionalProperties: true },
  };
  const closedSchema = createWebMcpInterface({
    origin: ORIGIN,
    context: {
      async getTools() { return wrongSchema; },
      async executeTool() { return "{}"; },
    },
  });
  await assert.rejects(closedSchema.listTools(), /closed object schema/);

  const metadataTools = webmcpTools();
  const metadataTool = metadataTools.find((tool) => tool.name === "set_cart_quantity");
  metadataTool.inputSchema.benchmarkHint = "hidden answer";
  metadataTool.inputSchema.properties.quantity.default = 2;
  metadataTool.annotations = { evaluatorAnswer: "secret" };
  const metadata = createWebMcpInterface({
    origin: ORIGIN,
    context: {
      async getTools() { return metadataTools; },
      async executeTool() { return "{}"; },
    },
  });
  const publicTools = await metadata.listTools();
  const publicMetadataTool = publicTools.find((tool) => tool.name === "set_cart_quantity");
  assert.equal("benchmarkHint" in publicMetadataTool.inputSchema, false);
  assert.equal("default" in publicMetadataTool.inputSchema.properties.quantity, false);
  assert.equal("annotations" in publicMetadataTool, false);

  const unsupported = createWebMcpInterface({
    origin: ORIGIN,
    context: {
      async getTools() { return [...webmcpTools(), { name: "debug_all", origin: ORIGIN }]; },
      async executeTool() { return "{}"; },
    },
  });
  await assert.rejects(unsupported.listTools(), /unexpected tool set/);

  const duplicateTools = webmcpTools();
  duplicateTools[1] = { ...duplicateTools[1], name: duplicateTools[0].name };
  const duplicate = createWebMcpInterface({
    origin: ORIGIN,
    context: {
      async getTools() { return duplicateTools; },
      async executeTool() { return "{}"; },
    },
  });
  await assert.rejects(duplicate.listTools(), /duplicate WebMCP tool/);

  const invalidResult = createWebMcpInterface({
    origin: ORIGIN,
    context: {
      async getTools() { return webmcpTools(); },
      async executeTool() { return "not-json"; },
    },
  });
  await invalidResult.listTools();
  await assert.rejects(invalidResult.invoke("get_cart", {}), /invalid JSON/);
});

test("WebMCP aborted or closed discovery cannot repopulate the cache", async () => {
  const pending = deferred();
  let mode = "pending";
  let executions = 0;
  const context = {
    async getTools() {
      if (mode === "pending") return pending.promise;
      return webmcpTools("http://attacker.test");
    },
    async executeTool() {
      executions += 1;
      return "{}";
    },
  };
  const iface = createWebMcpInterface({ origin: ORIGIN, context });
  const controller = new AbortController();
  const request = iface.listTools({ signal: controller.signal });
  await new Promise((resolve) => setImmediate(resolve));
  controller.abort();
  await assert.rejects(request, (error) => error.name === "AbortError");
  mode = "wrong-origin";
  pending.resolve(webmcpTools());
  await new Promise((resolve) => setImmediate(resolve));
  await assert.rejects(iface.invoke("get_cart", {}), /origin mismatch/);
  assert.equal(executions, 0);

  const closingPending = deferred();
  const closing = createWebMcpInterface({
    origin: ORIGIN,
    context: {
      async getTools() { return closingPending.promise; },
      async executeTool() { return "{}"; },
    },
  });
  const closingRequest = closing.listTools();
  await new Promise((resolve) => setImmediate(resolve));
  closing.close();
  closingPending.resolve(webmcpTools());
  await assert.rejects(closingRequest, /closed/);
});
