export class ProviderError extends Error {
  constructor(message, options = {}) {
    super(message, options);
    this.name = "ProviderError";
  }
}

function clone(value) {
  return structuredClone(value);
}

function assertNotAborted(signal) {
  if (signal?.aborted) {
    const error = new Error("provider operation aborted");
    error.name = "AbortError";
    throw error;
  }
}

async function wait(milliseconds, signal) {
  if (milliseconds <= 0) return;
  await new Promise((resolve, reject) => {
    let timer;
    const onAbort = () => {
      clearTimeout(timer);
      const error = new Error("provider operation aborted");
      error.name = "AbortError";
      reject(error);
    };
    timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * A deterministic provider for contract tests. It records every request and
 * returns one queued response per call. It deliberately has no retry path.
 */
export function createStubProvider({ responses = [], delayMs = 0 } = {}) {
  if (!Array.isArray(responses)) {
    throw new TypeError("stub provider responses must be an array");
  }
  if (!Number.isInteger(delayMs) || delayMs < 0) {
    throw new TypeError("stub provider delayMs must be a non-negative integer");
  }

  const queuedResponses = [...responses];
  const requests = [];
  let callCount = 0;

  async function complete(request, { signal } = {}) {
    assertNotAborted(signal);
    requests.push(clone(request));
    await wait(delayMs, signal);
    assertNotAborted(signal);

    if (callCount >= queuedResponses.length) {
      throw new ProviderError("stub provider response queue exhausted");
    }

    const queued = queuedResponses[callCount];
    callCount += 1;
    if (queued instanceof Error) throw queued;

    const response = typeof queued === "function"
      ? await queued(clone(request), { signal })
      : queued;
    assertNotAborted(signal);
    return clone(response);
  }

  return Object.freeze({
    complete,
    requests,
    get callCount() {
      return callCount;
    },
  });
}
