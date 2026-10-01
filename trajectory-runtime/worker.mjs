import { runLoop } from "./loop.mjs";

/**
 * Worker-facing wrapper. The interface is always closed, including when the
 * provider or loop fails. Model calls remain injected and offline in P4.
 */
export async function runWorker(options = {}) {
  const { interfaceAdapter } = options;
  if (!interfaceAdapter || typeof interfaceAdapter.close !== "function") {
    throw new TypeError("interfaceAdapter.close is required");
  }

  let loopError;
  try {
    return await runLoop(options);
  } catch (error) {
    loopError = error;
    throw error;
  } finally {
    try {
      interfaceAdapter.close();
    } catch (closeError) {
      if (loopError) {
        throw new AggregateError(
          [loopError, closeError],
          "model loop and interface cleanup both failed",
        );
      }
      throw closeError;
    }
  }
}
