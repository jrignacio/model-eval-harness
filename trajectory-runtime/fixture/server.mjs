import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const FIXTURE_ROOT = path.dirname(fileURLToPath(import.meta.url));
const ASSETS = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/index.html", ["index.html", "text/html; charset=utf-8"]],
  ["/app.mjs", ["app.mjs", "text/javascript; charset=utf-8"]],
  ["/domain.mjs", ["domain.mjs", "text/javascript; charset=utf-8"]],
  ["/webmcp.mjs", ["webmcp.mjs", "text/javascript; charset=utf-8"]],
]);

function headers(contentType) {
  return {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'",
    "Content-Type": contentType,
    "Origin-Agent-Cluster": "?1",
    "Permissions-Policy": "tools=(self)",
  };
}

export async function startFixtureServer({ host = "127.0.0.1", port = 0 } = {}) {
  const server = createServer(async (request, response) => {
    const requestUrl = new URL(request.url ?? "/", `http://${host}`);
    if (requestUrl.pathname === "/health") {
      response.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      response.end(JSON.stringify({ ok: true, fixture: "shop-fixture-v1" }));
      return;
    }
    const asset = ASSETS.get(requestUrl.pathname);
    if (!asset) {
      response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("not found");
      return;
    }
    try {
      const body = await readFile(path.join(FIXTURE_ROOT, asset[0]));
      response.writeHead(200, headers(asset[1]));
      response.end(body);
    } catch (error) {
      response.writeHead(500, { "Content-Type": "text/plain; charset=utf-8" });
      response.end(`fixture error: ${error.message}`);
    }
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("fixture server did not expose a TCP address");
  }
  return {
    server,
    origin: `http://${host}:${address.port}`,
    close: () => new Promise((resolve, reject) => {
      server.close((error) => error ? reject(error) : resolve());
    }),
  };
}
