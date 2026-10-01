export const WEBMCP_TOOL_NAMES = Object.freeze([
  "get_cart",
  "get_shipping",
  "list_products",
  "save_shipping",
  "set_cart_quantity",
]);

const TOOL_SPECS = [
  {
    name: "list_products",
    description: "List the products currently available in this shop.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    readOnlyHint: true,
    execute: (operations) => () => operations.listProducts(),
  },
  {
    name: "get_cart",
    description: "Read the current shopping cart and subtotal.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    readOnlyHint: true,
    execute: (operations) => () => operations.getCart(),
  },
  {
    name: "set_cart_quantity",
    description: "Set the quantity of one product in the shopping cart.",
    inputSchema: {
      type: "object",
      properties: {
        productId: { type: "string", description: "The product identifier." },
        quantity: { type: "integer", minimum: 0, description: "The desired quantity." },
      },
      required: ["productId", "quantity"],
      additionalProperties: false,
    },
    readOnlyHint: false,
    execute: (operations) => ({ productId, quantity }) =>
      operations.setCartQuantity({ productId, quantity }),
  },
  {
    name: "get_shipping",
    description: "Read the fictional shipping details saved for this shop session.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    readOnlyHint: true,
    execute: (operations) => () => operations.getShipping(),
  },
  {
    name: "save_shipping",
    description: "Save fictional recipient, address, and delivery note details.",
    inputSchema: {
      type: "object",
      properties: {
        recipient: { type: "string", description: "The fictional recipient name." },
        address: { type: "string", description: "The fictional delivery address." },
        note: { type: "string", description: "A fictional delivery note." },
      },
      required: ["recipient", "address", "note"],
      additionalProperties: false,
    },
    readOnlyHint: false,
    execute: (operations) => ({ recipient, address, note }) =>
      operations.saveShipping({ recipient, address, note }),
  },
];

function serializedResult(value) {
  return JSON.stringify(value);
}

export function webMcpAvailable() {
  return typeof document !== "undefined" && Boolean(document.modelContext);
}

export async function registerNativeWebMCP(operations) {
  if (!webMcpAvailable()) {
    return { available: false, toolNames: [] };
  }

  for (const spec of TOOL_SPECS) {
    await document.modelContext.registerTool({
      name: spec.name,
      description: spec.description,
      inputSchema: spec.inputSchema,
      annotations: {
        readOnlyHint: spec.readOnlyHint,
        untrustedContentHint: false,
        consequentialHint: false,
      },
      execute: async (input) => serializedResult(await spec.execute(operations)(input ?? {})),
    });
  }
  return { available: true, toolNames: [...WEBMCP_TOOL_NAMES] };
}
