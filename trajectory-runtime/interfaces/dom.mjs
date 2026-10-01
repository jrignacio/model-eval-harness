export class InterfaceError extends Error {
  constructor(message) {
    super(message);
    this.name = "InterfaceError";
  }
}

export class StaleReferenceError extends InterfaceError {
  constructor(reference) {
    super(`DOM reference is stale or unknown: ${reference}`);
    this.name = "StaleReferenceError";
  }
}

const ACTIONS = Object.freeze({
  click: Object.freeze(["ref"]),
  fill: Object.freeze(["ref", "value"]),
  select: Object.freeze(["ref", "value"]),
  snapshot: Object.freeze([]),
});
const ELEMENT_FIELDS = new Set(["key", "text", "role", "label", "value", "options"]);
const DOM_TOOL_DEFINITIONS = Object.freeze([
  {
    name: "snapshot",
    description: "Read visible text and controls from the current page.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "click",
    description: "Click one control from the latest visible page snapshot.",
    inputSchema: {
      type: "object",
      properties: { ref: { type: "string" } },
      required: ["ref"],
      additionalProperties: false,
    },
  },
  {
    name: "fill",
    description: "Fill one text control from the latest visible page snapshot.",
    inputSchema: {
      type: "object",
      properties: { ref: { type: "string" }, value: { type: "string" } },
      required: ["ref", "value"],
      additionalProperties: false,
    },
  },
  {
    name: "select",
    description: "Select one visible option from the latest page snapshot.",
    inputSchema: {
      type: "object",
      properties: { ref: { type: "string" }, value: { type: "string" } },
      required: ["ref", "value"],
      additionalProperties: false,
    },
  },
]);

function assertObject(value, message) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new InterfaceError(message);
  }
}

function assertExactFields(value, fields, message) {
  assertObject(value, message);
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (
    actual.length !== expected.length
    || actual.some((field, index) => field !== expected[index])
  ) {
    throw new InterfaceError(`${message}; expected fields ${expected.join(",")}`);
  }
}

async function currentOrigin(page) {
  const origin = typeof page.getOrigin === "function" ? await page.getOrigin() : page.origin;
  if (typeof origin !== "string" || origin.length === 0) {
    throw new InterfaceError("DOM page adapter did not provide an origin");
  }
  return origin;
}

async function currentNavigationToken(page) {
  const token = typeof page.getNavigationToken === "function"
    ? await page.getNavigationToken()
    : page.navigationToken;
  if (typeof token !== "string" || token.length === 0) {
    throw new InterfaceError("DOM page adapter did not provide a navigation token");
  }
  return token;
}

function assertText(value, field) {
  if (typeof value !== "string" || value.length === 0) {
    throw new InterfaceError(`${field} must be a non-empty string`);
  }
}

function assertString(value, field) {
  if (typeof value !== "string") {
    throw new InterfaceError(`${field} must be a string`);
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

function publicElement(descriptor, reference) {
  assertObject(descriptor, "DOM snapshot element must be an object");
  const unknown = Object.keys(descriptor).filter((field) => !ELEMENT_FIELDS.has(field));
  if (unknown.length > 0) {
    throw new InterfaceError(`DOM snapshot exposes unsupported fields: ${unknown.join(",")}`);
  }
  assertText(descriptor.key, "DOM snapshot element key");
  assertString(descriptor.text, "DOM snapshot element text");
  assertText(descriptor.role, "DOM snapshot element role");
  assertText(descriptor.label, "DOM snapshot element label");
  const result = {
    ref: reference,
    text: descriptor.text,
    role: descriptor.role,
    label: descriptor.label,
  };
  if ("value" in descriptor) {
    assertString(descriptor.value, "DOM snapshot element value");
    result.value = descriptor.value;
  }
  if ("options" in descriptor) {
    if (
      !Array.isArray(descriptor.options)
      || descriptor.options.some((value) => typeof value !== "string")
    ) {
      throw new InterfaceError("DOM snapshot element options must be strings");
    }
    result.options = [...descriptor.options];
  }
  return result;
}

export function createDomInterface({ origin, page }) {
  assertText(origin, "DOM interface origin");
  if (!page || typeof page.snapshot !== "function") {
    throw new InterfaceError("DOM interface requires a page snapshot adapter");
  }

  let generation = 0;
  const references = new Map();
  let referenceToken = null;
  let closed = false;

  function ensureOpen() {
    if (closed) throw new InterfaceError("DOM interface is closed");
  }

  async function assertPageOrigin() {
    const actual = await currentOrigin(page);
    if (actual !== origin) {
      references.clear();
      referenceToken = null;
      throw new InterfaceError(`DOM page origin mismatch: expected ${origin}, got ${actual}`);
    }
  }

  async function snapshot(input = {}, { signal } = {}) {
    ensureOpen();
    const callGeneration = ++generation;
    references.clear();
    referenceToken = null;
    assertExactFields(input, ACTIONS.snapshot, "snapshot input is invalid");
    return withSignal(async () => {
      await assertPageOrigin();
      const startToken = await currentNavigationToken(page);
      const raw = await page.snapshot({ signal });
      const endToken = await currentNavigationToken(page);
      assertNotAborted(signal);
      ensureOpen();
      if (callGeneration !== generation) {
        throw new InterfaceError("DOM snapshot was superseded by a newer operation");
      }
      if (startToken !== endToken) {
        throw new InterfaceError("DOM snapshot crossed a navigation");
      }
      assertObject(raw, "DOM page snapshot must be an object");
      assertExactFields(raw, ["origin", "elements"], "DOM page snapshot is invalid");
      if (raw.origin !== origin) {
        throw new InterfaceError(
          `DOM snapshot origin mismatch: expected ${origin}, got ${raw.origin}`,
        );
      }
      if (!Array.isArray(raw.elements)) {
        throw new InterfaceError("DOM page snapshot elements must be an array");
      }

      const nextReferences = new Map();
      const elements = raw.elements.map((descriptor, index) => {
        const reference = `dom-${callGeneration}-${index + 1}`;
        const result = publicElement(descriptor, reference);
        nextReferences.set(reference, descriptor.key);
        return result;
      });
      assertNotAborted(signal);
      ensureOpen();
      if (callGeneration !== generation) {
        throw new InterfaceError("DOM snapshot was superseded by a newer operation");
      }
      for (const [reference, key] of nextReferences) references.set(reference, key);
      referenceToken = endToken;
      return { elements };
    }, signal);
  }

  async function invoke(action, input = {}, { signal } = {}) {
    ensureOpen();
    if (!Object.hasOwn(ACTIONS, action)) {
      throw new InterfaceError(`unsupported DOM action: ${action}`);
    }
    if (action === "snapshot") return snapshot(input, { signal });
    return withSignal(async () => {
      await assertPageOrigin();
      const liveToken = await currentNavigationToken(page);
      if (referenceToken !== liveToken) {
        references.clear();
        referenceToken = null;
        throw new StaleReferenceError(input.ref);
      }
      const expectedFields = ACTIONS[action];
      assertExactFields(input, expectedFields, `${action} input is invalid`);
      const key = references.get(input.ref);
      if (!key) throw new StaleReferenceError(input.ref);
      if (action === "click") {
        if (typeof page.click !== "function") {
          throw new InterfaceError("page click adapter is missing");
        }
        await page.click(key, { signal });
      } else if (action === "fill") {
        assertString(input.value, "fill value");
        if (typeof page.fill !== "function") {
          throw new InterfaceError("page fill adapter is missing");
        }
        await page.fill(key, input.value, { signal });
      } else {
        assertString(input.value, "select value");
        if (typeof page.select !== "function") {
          throw new InterfaceError("page select adapter is missing");
        }
        await page.select(key, input.value, { signal });
      }
      return { status: "ok", action, ref: input.ref };
    }, signal);
  }

  async function listTools() {
    ensureOpen();
    return DOM_TOOL_DEFINITIONS.map((tool) => ({
      name: tool.name,
      description: tool.description,
      inputSchema: JSON.parse(JSON.stringify(tool.inputSchema)),
    }));
  }

  function close() {
    if (closed) return;
    closed = true;
    generation += 1;
    references.clear();
    referenceToken = null;
    if (typeof page.close === "function") page.close();
  }

  return Object.freeze({
    kind: "dom",
    actions: Object.freeze(Object.keys(ACTIONS)),
    listTools,
    snapshot,
    invoke,
    close,
  });
}
