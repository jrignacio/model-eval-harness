export class InterfaceError extends Error {
  constructor(message) {
    super(message);
    this.name = "InterfaceError";
  }
}

export const ALLOWED_TOOL_NAMES = Object.freeze([
  "get_cart",
  "get_shipping",
  "list_products",
  "save_shipping",
  "set_cart_quantity",
]);

const INPUT_SPECS = Object.freeze({
  get_cart: Object.freeze({}),
  get_shipping: Object.freeze({}),
  list_products: Object.freeze({}),
  save_shipping: Object.freeze({
    recipient: "string",
    address: "string",
    note: "string",
  }),
  set_cart_quantity: Object.freeze({ productId: "string", quantity: "integer" }),
});

const ALLOWED_SET = new Set(ALLOWED_TOOL_NAMES);

function assertObject(value, message) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new InterfaceError(message);
  }
}

function assertNotAborted(signal) {
  if (signal?.aborted) {
    const error = new Error("interface operation aborted");
    error.name = "AbortError";
    throw error;
  }
}

async function withSignal(operation, signal) {
  assertNotAborted(signal);
  if (!signal) return operation();
  let onAbort;
  const aborted = new Promise((_, reject) => {
    onAbort = () => {
      const error = new Error("interface operation aborted");
      error.name = "AbortError";
      reject(error);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    return await Promise.race([operation(), aborted]);
  } finally {
    signal.removeEventListener("abort", onAbort);
  }
}

function assertText(value, field) {
  if (typeof value !== "string" || value.length === 0) {
    throw new InterfaceError(`${field} must be a non-empty string`);
  }
}

function sortedEqual(left, right) {
  const a = [...left].sort();
  const b = [...right].sort();
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

function assertInput(name, input) {
  const value = input === undefined ? {} : input;
  assertObject(value, `${name} input must be an object`);
  const spec = INPUT_SPECS[name];
  if (!sortedEqual(Object.keys(value), Object.keys(spec))) {
    throw new InterfaceError(`${name} input has unsupported or missing fields`);
  }
  for (const [field, type] of Object.entries(spec)) {
    if (type === "string") assertText(value[field], `${name}.${field}`);
    if (type === "integer" && (!Number.isInteger(value[field]) || value[field] < 0)) {
      throw new InterfaceError(`${name}.${field} must be a non-negative integer`);
    }
  }
  return value;
}

function assertToolSchema(name, schema) {
  assertObject(schema, `${name} inputSchema must be an object`);
  if (schema.type !== "object" || schema.additionalProperties !== false) {
    throw new InterfaceError(`${name} inputSchema must be a closed object schema`);
  }
  assertObject(schema.properties, `${name} inputSchema properties must be an object`);
  const expected = INPUT_SPECS[name];
  if (!sortedEqual(Object.keys(schema.properties), Object.keys(expected))) {
    throw new InterfaceError(`${name} inputSchema exposes unsupported or missing fields`);
  }
  for (const [field, type] of Object.entries(expected)) {
    const property = schema.properties[field];
    assertObject(property, `${name}.${field} schema must be an object`);
    if (property.type !== type) throw new InterfaceError(`${name}.${field} schema type mismatch`);
  }
  const required = schema.required ?? [];
  if (!Array.isArray(required) || !sortedEqual(required, Object.keys(expected))) {
    throw new InterfaceError(`${name} inputSchema required fields are invalid`);
  }
}

function publicSchema(name) {
  const properties = {};
  for (const [field, type] of Object.entries(INPUT_SPECS[name])) {
    properties[field] = { type };
    if (field === "quantity") properties[field].minimum = 0;
  }
  const schema = {
    type: "object",
    properties,
    additionalProperties: false,
  };
  const required = Object.keys(properties);
  if (required.length > 0) schema.required = required;
  return schema;
}

function publicTool(tool) {
  return {
    name: tool.name,
    description: tool.description,
    inputSchema: publicSchema(tool.name),
  };
}

export function createWebMcpInterface({ origin, context }) {
  assertText(origin, "WebMCP interface origin");
  if (
    !context
    || typeof context.getTools !== "function"
    || typeof context.executeTool !== "function"
  ) {
    throw new InterfaceError("WebMCP interface requires a model context adapter");
  }

  let discovered = null;
  let closed = false;
  let operationEpoch = 0;

  function ensureOpen() {
    if (closed) throw new InterfaceError("WebMCP interface is closed");
  }

  async function discover({ signal } = {}) {
    ensureOpen();
    const callEpoch = ++operationEpoch;
    discovered = null;
    return withSignal(async () => {
      const tools = await context.getTools({ signal });
      assertNotAborted(signal);
      ensureOpen();
      if (callEpoch !== operationEpoch) {
        throw new InterfaceError("WebMCP discovery was superseded by a newer operation");
      }
      if (!Array.isArray(tools)) throw new InterfaceError("model context tools must be an array");
      if (tools.length !== ALLOWED_TOOL_NAMES.length) {
        throw new InterfaceError("model context exposed an unexpected tool set");
      }
      const byName = new Map();
      for (const tool of tools) {
        assertObject(tool, "model context tool must be an object");
        assertText(tool.name, "model context tool name");
        if (!ALLOWED_SET.has(tool.name)) {
          throw new InterfaceError(`unsupported WebMCP tool: ${tool.name}`);
        }
        if (byName.has(tool.name)) {
          throw new InterfaceError(`duplicate WebMCP tool: ${tool.name}`);
        }
        if (tool.origin !== origin) {
          throw new InterfaceError(`WebMCP tool origin mismatch: ${tool.name}`);
        }
        assertText(tool.description, `${tool.name} description`);
        assertToolSchema(tool.name, tool.inputSchema);
        byName.set(tool.name, tool);
      }
      if (!sortedEqual([...byName.keys()], ALLOWED_TOOL_NAMES)) {
        throw new InterfaceError("model context is missing an allowed WebMCP tool");
      }
      const publicTools = ALLOWED_TOOL_NAMES.map((name) => publicTool(byName.get(name)));
      assertNotAborted(signal);
      ensureOpen();
      if (callEpoch !== operationEpoch) {
        throw new InterfaceError("WebMCP discovery was superseded by a newer operation");
      }
      discovered = byName;
      return publicTools;
    }, signal);
  }

  async function invoke(name, input = {}, { signal } = {}) {
    ensureOpen();
    if (!ALLOWED_SET.has(name)) throw new InterfaceError(`unsupported WebMCP tool: ${name}`);
    await discover({ signal });
    ensureOpen();
    assertNotAborted(signal);
    const tool = discovered?.get(name);
    if (!tool) throw new InterfaceError(`WebMCP tool was not discovered: ${name}`);
    const validInput = assertInput(name, input);
    const raw = await withSignal(
      () => context.executeTool(tool, JSON.stringify(validInput), { signal }),
      signal,
    );
    if (typeof raw !== "string") throw new InterfaceError(`${name} returned non-JSON text`);
    try {
      return JSON.parse(raw);
    } catch {
      throw new InterfaceError(`${name} returned invalid JSON`);
    }
  }

  async function listTools(options = {}) {
    return discover(options);
  }

  function close() {
    if (closed) return;
    closed = true;
    operationEpoch += 1;
    discovered = null;
    if (typeof context.close === "function") context.close();
  }

  return Object.freeze({
    kind: "webmcp",
    toolNames: ALLOWED_TOOL_NAMES,
    listTools,
    discover,
    invoke,
    close,
  });
}
