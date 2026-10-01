import assert from "node:assert/strict";
import test from "node:test";

import {
  cartView,
  createSeedState,
  listProducts,
  saveShipping,
  setCartQuantity,
  stateView,
} from "../fixture/domain.mjs";
import { startFixtureServer } from "../fixture/server.mjs";

test("seeded fixture operations are deterministic and evaluator-visible", () => {
  const first = createSeedState();
  const second = createSeedState();
  assert.deepEqual(first, second);

  assert.equal(listProducts(first).find((product) => product.color === "blue").id, "mug-blue");
  assert.deepEqual(cartView(first), {
    items: [
      { productId: "cable", name: "USB-C Cable", quantity: 1, unitPrice: 8, lineTotal: 8 },
      { productId: "mug-blue", name: "Blue Mug", quantity: 1, unitPrice: 12, lineTotal: 12 },
    ],
    subtotal: 20,
  });

  setCartQuantity(first, { productId: "mug-blue", quantity: 2 });
  saveShipping(first, {
    recipient: "Ada Example",
    address: "42 Test Street",
    note: "Leave at the fictional door",
  });
  assert.deepEqual(stateView(first).cart.items[1].quantity, 2);
  assert.equal(stateView(first).shipping.recipient, "Ada Example");
  assert.throws(() => setCartQuantity(first, { productId: "mug-blue", quantity: 5 }), /available/);
});

test("fixture server serves an origin-isolated page and rejects unknown paths", async (t) => {
  const fixture = await startFixtureServer();
  t.after(() => fixture.close());

  const page = await fetch(`${fixture.origin}/`);
  assert.equal(page.status, 200);
  assert.equal(page.headers.get("origin-agent-cluster"), "?1");
  assert.equal(page.headers.get("permissions-policy"), "tools=(self)");
  assert.match(await page.text(), /Agent Evaluation Shop Fixture/);

  const health = await fetch(`${fixture.origin}/health`);
  assert.deepEqual(await health.json(), { ok: true, fixture: "shop-fixture-v1" });

  const missing = await fetch(`${fixture.origin}/missing.js`);
  assert.equal(missing.status, 404);
});
