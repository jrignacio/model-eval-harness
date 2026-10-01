import {
  cartView,
  createSeedState,
  listProducts,
  saveShipping,
  setCartQuantity,
  shippingView,
  stateView,
} from "./domain.mjs";
import { registerNativeWebMCP } from "./webmcp.mjs";

let state = createSeedState();

function mutateCart(input) {
  const result = setCartQuantity(state, input);
  render();
  return result;
}

function mutateShipping(input) {
  const result = saveShipping(state, input);
  render();
  return result;
}

const operations = {
  listProducts: () => listProducts(state),
  getCart: () => cartView(state),
  setCartQuantity: mutateCart,
  getShipping: () => shippingView(state),
  saveShipping: mutateShipping,
};

function element(selector) {
  const found = document.querySelector(selector);
  if (!found) throw new Error(`Missing fixture element: ${selector}`);
  return found;
}

function showStatus(message, isError = false) {
  const status = element("#status");
  status.textContent = message;
  status.dataset.error = isError ? "true" : "false";
}

function render() {
  const products = element("#products");
  products.replaceChildren();
  for (const product of listProducts(state)) {
    const item = document.createElement("li");
    item.textContent = [
      product.name,
      product.color,
      `$${product.price}`,
      `${product.stock} in stock`,
    ].join(" — ");
    item.dataset.productId = product.id;
    products.append(item);
  }

  const productSelect = element("#cart-product");
  productSelect.replaceChildren();
  for (const product of listProducts(state)) {
    const option = document.createElement("option");
    option.value = product.id;
    option.textContent = product.name;
    productSelect.append(option);
  }

  const cart = cartView(state);
  element("#cart-output").textContent = JSON.stringify(cart, null, 2);
  const shipping = shippingView(state);
  element("#shipping-recipient").value = shipping.recipient;
  element("#shipping-address").value = shipping.address;
  element("#shipping-note").value = shipping.note;
  element("#shipping-output").textContent = JSON.stringify(shipping, null, 2);
}

function reset() {
  state = createSeedState();
  showStatus("reset");
  render();
  return stateView(state);
}

function bindUi() {
  element("#cart-form").addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      operations.setCartQuantity({
        productId: element("#cart-product").value,
        quantity: Number(element("#cart-quantity").value),
      });
      showStatus("cart updated");
    } catch (error) {
      showStatus(error.message, true);
    }
  });

  element("#shipping-form").addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      operations.saveShipping({
        recipient: element("#shipping-recipient").value,
        address: element("#shipping-address").value,
        note: element("#shipping-note").value,
      });
      showStatus("shipping saved");
    } catch (error) {
      showStatus(error.message, true);
    }
  });
}

bindUi();
render();

window.__fixtureApp = {
  getState: () => stateView(state),
  reset,
};

window.__fixtureAppReady = registerNativeWebMCP(operations).then((native) => {
  window.__fixtureNativeWebMCP = native;
  return native;
});
