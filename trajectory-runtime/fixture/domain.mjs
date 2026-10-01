export const CATALOG = Object.freeze([
  Object.freeze({
    id: "mug-blue",
    name: "Blue Mug",
    color: "blue",
    price: 12,
    stock: 4,
  }),
  Object.freeze({
    id: "mug-red",
    name: "Red Mug",
    color: "red",
    price: 11,
    stock: 2,
  }),
  Object.freeze({
    id: "cable",
    name: "USB-C Cable",
    color: "black",
    price: 8,
    stock: 5,
  }),
]);

export const SEED_VERSION = "shop-fixture-v1";

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function createSeedState() {
  return {
    seedVersion: SEED_VERSION,
    products: clone(CATALOG),
    cart: [
      { productId: "cable", quantity: 1 },
      { productId: "mug-blue", quantity: 1 },
    ],
    shipping: {
      recipient: "Seed Recipient",
      address: "100 Example Avenue",
      note: "Initial fixture state",
    },
  };
}

function productFor(state, productId) {
  const product = state.products.find((candidate) => candidate.id === productId);
  if (!product) {
    throw new Error(`Unknown product: ${productId}`);
  }
  return product;
}

export function listProducts(state) {
  return state.products.map((product) => ({ ...product }));
}

export function cartView(state) {
  const items = state.cart.map(({ productId, quantity }) => {
    const product = productFor(state, productId);
    return {
      productId,
      name: product.name,
      quantity,
      unitPrice: product.price,
      lineTotal: product.price * quantity,
    };
  });
  return {
    items,
    subtotal: items.reduce((total, item) => total + item.lineTotal, 0),
  };
}

export function setCartQuantity(state, { productId, quantity }) {
  if (!Number.isInteger(quantity) || quantity < 0) {
    throw new Error("quantity must be a non-negative integer");
  }
  const product = productFor(state, productId);
  if (quantity > product.stock) {
    throw new Error(`Only ${product.stock} units are available`);
  }
  const index = state.cart.findIndex((item) => item.productId === productId);
  if (quantity === 0) {
    if (index >= 0) state.cart.splice(index, 1);
  } else if (index >= 0) {
    state.cart[index] = { productId, quantity };
  } else {
    state.cart.push({ productId, quantity });
  }
  return cartView(state);
}

export function shippingView(state) {
  return { ...state.shipping };
}

export function saveShipping(state, { recipient, address, note }) {
  for (const [name, value] of Object.entries({ recipient, address, note })) {
    if (typeof value !== "string" || value.trim() === "") {
      throw new Error(`${name} is required`);
    }
  }
  state.shipping = { recipient, address, note };
  return shippingView(state);
}

export function stateView(state) {
  return {
    seedVersion: state.seedVersion,
    products: listProducts(state),
    cart: cartView(state),
    shipping: shippingView(state),
  };
}
