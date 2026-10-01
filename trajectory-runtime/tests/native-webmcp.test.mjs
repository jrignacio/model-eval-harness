import assert from "node:assert/strict";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";

import { startFixtureServer } from "../fixture/server.mjs";

const CHROME_PATH = process.env.CHROME_PATH
  ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const WEBMCP_FLAGS = [
  ["--enable-features=WebMCPTesting,DevToolsWebMCPSupport"],
  ["--enable-features=WebMCP"],
  ["--enable-experimental-web-platform-features"],
];

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

  waitForEvent(method, sessionId, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.listeners.delete(listener);
        reject(new Error(`Timed out waiting for ${method}`));
      }, timeoutMs);
      const listener = (message) => {
        if (message.method !== method || (sessionId && message.sessionId !== sessionId)) return;
        clearTimeout(timer);
        this.listeners.delete(listener);
        resolve(message.params);
      };
      this.listeners.add(listener);
    });
  }

  async evaluate(expression, sessionId) {
    const response = await this.send(
      "Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true, userGesture: true },
      sessionId,
    );
    if (response.exceptionDetails) {
      throw new Error(
        response.exceptionDetails.exception?.description ?? "Runtime evaluation failed",
      );
    }
    return response.result?.value;
  }

  close() {
    if (this.webSocket.readyState === WebSocket.OPEN) this.webSocket.close();
  }
}

async function getJson(port, endpoint) {
  const response = await fetch(`http://127.0.0.1:${port}${endpoint}`);
  if (!response.ok) throw new Error(`Chrome endpoint ${endpoint} returned ${response.status}`);
  return response.json();
}

async function waitForChrome(port, child) {
  const deadline = Date.now() + 10000;
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
  if (child.exitCode === null) {
    child.kill("SIGTERM");
    await Promise.race([once(child, "exit"), new Promise((resolve) => setTimeout(resolve, 2000))]);
    if (child.exitCode === null) child.kill("SIGKILL");
  }
  await rm(profile, { recursive: true, force: true });
}

async function launchChrome(origin) {
  const profile = await mkdtemp(path.join(tmpdir(), "agent-eval-chrome-"));
  const port = await freePort();
  let lastError;
  for (const flags of WEBMCP_FLAGS) {
    const child = spawn(CHROME_PATH, [
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${profile}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-background-networking",
      "--headless=new",
      ...flags,
      "about:blank",
    ], { stdio: ["ignore", "ignore", "pipe"] });
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
      const loaded = browser.waitForEvent("Page.loadEventFired", attached.sessionId);
      await browser.send("Page.navigate", { url: `${origin}/` }, attached.sessionId);
      await loaded;
      return {
        browser,
        child,
        profile,
        sessionId: attached.sessionId,
        targetId: target.targetId,
        flags,
      };
    } catch (error) {
      lastError = error;
      await closeChrome(child, profile);
    }
  }
  await rm(profile, { recursive: true, force: true });
  throw new Error(`Native WebMCP browser proof could not start: ${lastError?.message}`);
}

test("native WebMCP and visible UI reach identical seeded states", async (t) => {
  const fixture = await startFixtureServer();
  t.after(() => fixture.close());
  const chrome = await launchChrome(fixture.origin);
  t.after(async () => {
    chrome.browser.close();
    await closeChrome(chrome.child, chrome.profile);
  });

  const nativeStatus = await chrome.browser.evaluate("window.__fixtureAppReady", chrome.sessionId);
  assert.equal(
    nativeStatus.available,
    true,
    "native document.modelContext is unavailable; stop P2",
  );
  assert.deepEqual(nativeStatus.toolNames, [
    "get_cart",
    "get_shipping",
    "list_products",
    "save_shipping",
    "set_cart_quantity",
  ]);

  const initial = await chrome.browser.evaluate("window.__fixtureApp.getState()", chrome.sessionId);
  const uiRun = await chrome.browser.evaluate(
    `window.__fixtureApp.reset();
     document.querySelector("#cart-product").value = "mug-blue";
     document.querySelector("#cart-quantity").value = "2";
     document.querySelector("#cart-form").requestSubmit();
     document.querySelector("#shipping-recipient").value = "Ada Example";
     document.querySelector("#shipping-address").value = "42 Test Street";
     document.querySelector("#shipping-note").value = "Leave at the fictional door";
     document.querySelector("#shipping-form").requestSubmit();
     ({
       state: window.__fixtureApp.getState(),
       renderedCart: document.querySelector("#cart-output").textContent,
       renderedShipping: document.querySelector("#shipping-output").textContent,
     })`,
    chrome.sessionId,
  );
  const uiState = uiRun.state;
  assert.deepEqual(JSON.parse(uiRun.renderedCart), uiState.cart);
  assert.deepEqual(JSON.parse(uiRun.renderedShipping), uiState.shipping);
  assert.equal(initial.cart.subtotal, 20);

  const nativeRun = await chrome.browser.evaluate(
    `(async () => {
       window.__fixtureApp.reset();
       const tools = await document.modelContext.getTools();
       const byName = Object.fromEntries(tools.map((tool) => [tool.name, tool]));
       await document.modelContext.executeTool(
         byName.set_cart_quantity,
         JSON.stringify({ productId: "mug-blue", quantity: 2 }),
       );
       await document.modelContext.executeTool(
         byName.save_shipping,
         JSON.stringify({
           recipient: "Ada Example",
           address: "42 Test Street",
           note: "Leave at the fictional door",
         }),
       );
       return {
         state: window.__fixtureApp.getState(),
         renderedCart: document.querySelector("#cart-output").textContent,
         renderedShipping: document.querySelector("#shipping-output").textContent,
       };
     })()`,
    chrome.sessionId,
  );
  const nativeState = nativeRun.state;
  assert.deepEqual(JSON.parse(nativeRun.renderedCart), nativeState.cart);
  assert.deepEqual(JSON.parse(nativeRun.renderedShipping), nativeState.shipping);
  assert.deepEqual(nativeState, uiState);
  assert.deepEqual(nativeState.cart.items, [
    { productId: "cable", name: "USB-C Cable", quantity: 1, unitPrice: 8, lineTotal: 8 },
    { productId: "mug-blue", name: "Blue Mug", quantity: 2, unitPrice: 12, lineTotal: 24 },
  ]);
  assert.equal(nativeState.cart.subtotal, 32);
  assert.deepEqual(nativeState.shipping, {
    recipient: "Ada Example",
    address: "42 Test Street",
    note: "Leave at the fictional door",
  });
  assert.equal(chrome.flags.length > 0, true);
});
