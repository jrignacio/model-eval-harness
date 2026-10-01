"""Offline environments and interfaces used by the trajectory supervisor."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any, Callable


class EnvironmentError(RuntimeError):
    """Base error for an environment or interface operation."""


class ToolInvocationError(EnvironmentError):
    """A public tool rejected an otherwise valid invocation."""


CATALOG = (
    {"id": "mug-blue", "name": "Blue Mug", "color": "blue", "price": 12, "stock": 4},
    {"id": "mug-red", "name": "Red Mug", "color": "red", "price": 11, "stock": 2},
    {"id": "cable", "name": "USB-C Cable", "color": "black", "price": 8, "stock": 5},
)
SEED_VERSION = "shop-fixture-v1"

WEBMCP_TOOLS = (
    {
        "name": "get_cart",
        "description": "Read the current shopping cart and subtotal.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_shipping",
        "description": "Read the fictional shipping details saved for this shop session.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_products",
        "description": "List the products currently available in this shop.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "save_shipping",
        "description": "Save fictional recipient, address, and delivery note details.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string"},
                "address": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["recipient", "address", "note"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_cart_quantity",
        "description": "Set the quantity of one product in the shopping cart.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "productId": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 0},
            },
            "required": ["productId", "quantity"],
            "additionalProperties": False,
        },
    },
)

DOM_TOOLS = (
    {
        "name": "snapshot",
        "description": "Read visible text and controls from the current page.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "click",
        "description": "Click one control from the latest visible page snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fill",
        "description": "Fill one text control from the latest visible page snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}, "value": {"type": "string"}},
            "required": ["ref", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "select",
        "description": "Select one visible option from the latest page snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}, "value": {"type": "string"}},
            "required": ["ref", "value"],
            "additionalProperties": False,
        },
    },
)


def create_seed_state() -> dict[str, Any]:
    return {
        "seedVersion": SEED_VERSION,
        "products": copy.deepcopy(list(CATALOG)),
        "cart": [
            {"productId": "cable", "quantity": 1},
            {"productId": "mug-blue", "quantity": 1},
        ],
        "shipping": {
            "recipient": "Seed Recipient",
            "address": "100 Example Avenue",
            "note": "Initial fixture state",
        },
    }


def _product_for(state: dict[str, Any], product_id: str) -> dict[str, Any]:
    for product in state["products"]:
        if product["id"] == product_id:
            return product
    raise ToolInvocationError(f"Unknown product: {product_id}")


def _cart_view(state: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in state["cart"]:
        product = _product_for(state, item["productId"])
        items.append(
            {
                "productId": item["productId"],
                "name": product["name"],
                "quantity": item["quantity"],
                "unitPrice": product["price"],
                "lineTotal": product["price"] * item["quantity"],
            }
        )
    return {"items": items, "subtotal": sum(item["lineTotal"] for item in items)}


def state_view(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "seedVersion": state["seedVersion"],
        "products": copy.deepcopy(state["products"]),
        "cart": _cart_view(state),
        "shipping": copy.deepcopy(state["shipping"]),
    }


@dataclass
class MockShopEnvironment:
    """A browser-free copy of the seeded fixture's application rules."""

    _state: dict[str, Any]

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "MockShopEnvironment":
        return cls(copy.deepcopy(state))

    def list_products(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._state["products"])

    def get_cart(self) -> dict[str, Any]:
        return _cart_view(self._state)

    def get_shipping(self) -> dict[str, Any]:
        return copy.deepcopy(self._state["shipping"])

    def set_cart_quantity(self, product_id: str, quantity: int) -> dict[str, Any]:
        if not isinstance(product_id, str) or not product_id:
            raise ToolInvocationError("productId is required")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise ToolInvocationError("quantity must be a non-negative integer")
        product = _product_for(self._state, product_id)
        if quantity > product["stock"]:
            raise ToolInvocationError(f"Only {product['stock']} units are available")
        index = next(
            (
                index
                for index, item in enumerate(self._state["cart"])
                if item["productId"] == product_id
            ),
            None,
        )
        if quantity == 0:
            if index is not None:
                self._state["cart"].pop(index)
        elif index is None:
            self._state["cart"].append({"productId": product_id, "quantity": quantity})
        else:
            self._state["cart"][index] = {"productId": product_id, "quantity": quantity}
        return self.get_cart()

    def save_shipping(self, recipient: str, address: str, note: str) -> dict[str, Any]:
        values = {"recipient": recipient, "address": address, "note": note}
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise ToolInvocationError("recipient, address, and note are required")
        self._state["shipping"] = values
        return self.get_shipping()

    def final_state(self) -> dict[str, Any]:
        return state_view(self._state)


class MockInterface:
    """A small common-interface adapter for offline supervisor tests."""

    def __init__(self, environment: MockShopEnvironment, arm: str):
        if arm not in {"dom", "webmcp"}:
            raise ValueError(f"unsupported mock arm: {arm}")
        self.environment = environment
        self.arm = arm
        self._generation = 0
        self._refs: dict[str, str] = {}
        self._draft: dict[str, Any] = {"quantities": {}, "shipping": {}}

    def list_tools(self) -> list[dict[str, Any]]:
        return copy.deepcopy(list(DOM_TOOLS if self.arm == "dom" else WEBMCP_TOOLS))

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        if self.arm == "webmcp":
            return self._invoke_webmcp(name, arguments)
        return self._invoke_dom(name, arguments)

    def _invoke_webmcp(self, name: str, arguments: dict[str, Any]) -> Any:
        if not isinstance(arguments, dict):
            raise ToolInvocationError("tool arguments must be an object")
        if name == "list_products" and not arguments:
            return self.environment.list_products()
        if name == "get_cart" and not arguments:
            return self.environment.get_cart()
        if name == "get_shipping" and not arguments:
            return self.environment.get_shipping()
        if name == "set_cart_quantity" and set(arguments) == {"productId", "quantity"}:
            return self.environment.set_cart_quantity(arguments["productId"], arguments["quantity"])
        if name == "save_shipping" and set(arguments) == {"recipient", "address", "note"}:
            return self.environment.save_shipping(
                arguments["recipient"], arguments["address"], arguments["note"]
            )
        raise ToolInvocationError(f"invalid WebMCP invocation: {name}")

    def _add_ref(self, elements: list[dict[str, Any]], key: str, **descriptor: Any) -> None:
        reference = f"dom-{self._generation}-{len(elements) + 1}"
        self._refs[reference] = key
        elements.append({"ref": reference, **descriptor})

    def _dom_snapshot(self) -> dict[str, Any]:
        self._generation += 1
        self._refs.clear()
        elements: list[dict[str, Any]] = []
        product_text = "; ".join(
            f"{item['name']} (${item['price']}, stock {item['stock']})"
            for item in self.environment.list_products()
        )
        self._add_ref(
            elements,
            "products",
            text=product_text,
            role="region",
            label="Products",
        )
        cart = self.environment.get_cart()
        quantities = {item["productId"]: item["quantity"] for item in cart["items"]}
        for product in self.environment.list_products():
            self._add_ref(
                elements,
                f"quantity:{product['id']}",
                text=f"{product['name']} quantity",
                role="spinbutton",
                label=f"Quantity for {product['name']}",
                value=str(quantities.get(product["id"], 0)),
            )
        self._add_ref(
            elements,
            "apply-cart",
            text="Apply cart quantities",
            role="button",
            label="Apply cart quantities",
        )
        shipping = self.environment.get_shipping()
        for field in ("recipient", "address", "note"):
            self._add_ref(
                elements,
                f"shipping:{field}",
                text=f"Shipping {field}",
                role="textbox",
                label=f"Shipping {field}",
                value=shipping[field],
            )
        self._add_ref(
            elements,
            "save-shipping",
            text="Save shipping",
            role="button",
            label="Save shipping",
        )
        return {"elements": elements}

    def _resolve_ref(self, reference: str) -> str:
        key = self._refs.get(reference)
        if key is None:
            raise ToolInvocationError(f"stale DOM reference: {reference}")
        return key

    def _invoke_dom(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "snapshot" and not arguments:
            return self._dom_snapshot()
        if name == "fill" and set(arguments) == {"ref", "value"}:
            key = self._resolve_ref(arguments["ref"])
            value = arguments["value"]
            if not isinstance(value, str):
                raise ToolInvocationError("fill value must be a string")
            if key.startswith("quantity:"):
                try:
                    quantity = int(value)
                except ValueError as exc:
                    raise ToolInvocationError("quantity must be an integer") from exc
                self._draft["quantities"][key.split(":", 1)[1]] = quantity
            elif key.startswith("shipping:"):
                self._draft["shipping"][key.split(":", 1)[1]] = value
            else:
                raise ToolInvocationError(f"control is not fillable: {key}")
            return {"ok": True}
        if name == "click" and set(arguments) == {"ref"}:
            key = self._resolve_ref(arguments["ref"])
            if key == "apply-cart":
                for product_id, quantity in self._draft["quantities"].items():
                    self.environment.set_cart_quantity(product_id, quantity)
                self._draft["quantities"].clear()
                return self.environment.get_cart()
            if key == "save-shipping":
                shipping = self._draft["shipping"]
                result = self.environment.save_shipping(
                    shipping.get("recipient", ""),
                    shipping.get("address", ""),
                    shipping.get("note", ""),
                )
                self._draft["shipping"].clear()
                return result
            raise ToolInvocationError(f"control is not clickable: {key}")
        if name == "select":
            raise ToolInvocationError("the mock fixture has no select control")
        raise ToolInvocationError(f"invalid DOM invocation: {name}")


def _find_dom_ref(snapshot: dict[str, Any], label: str) -> str:
    for element in snapshot["elements"]:
        if element.get("label") == label:
            return element["ref"]
    raise ToolInvocationError(f"DOM label not found: {label}")


def run_mock_attempt(
    public_task: dict[str, Any], arm: str, initial_state: dict[str, Any]
) -> dict[str, Any]:
    """Run a deterministic, browser-free agent against public task data only."""

    if "evaluation" in public_task or "expected" in public_task:
        raise ValueError("agent payload contains evaluator data")
    environment = MockShopEnvironment.from_state(initial_state)
    interface = MockInterface(environment, arm)
    trace: list[dict[str, Any]] = []
    request_index = 0
    started = time.perf_counter()

    def call(name: str, arguments: dict[str, Any]) -> Any:
        nonlocal request_index
        request_index += 1
        request_id = f"mock-request-{request_index}"
        response_id = f"mock-response-{request_index}"
        trace.append(
            {
                "type": "model_request",
                "request_id": request_id,
                "model": "mock-luna",
                "attempt_index": request_index - 1,
            }
        )
        trace.append(
            {
                "type": "model_response",
                "request_id": request_id,
                "response_id": response_id,
                "status": "completed",
            }
        )
        trace.append(
            {
                "type": "tool_call",
                "call_id": f"mock-call-{request_index}",
                "name": name,
                "arguments": copy.deepcopy(arguments),
            }
        )
        try:
            result = interface.invoke(name, arguments)
        except ToolInvocationError as exc:
            trace.append(
                {
                    "type": "tool_result",
                    "call_id": f"mock-call-{request_index}",
                    "name": name,
                    "status": "error",
                    "result": {"error": str(exc)},
                }
            )
            raise
        trace.append(
            {
                "type": "tool_result",
                "call_id": f"mock-call-{request_index}",
                "name": name,
                "status": "ok",
                "result": copy.deepcopy(result),
            }
        )
        return result

    task_kind = public_task["kind"]
    if arm == "webmcp":
        if task_kind == "catalog":
            products = call("list_products", {})
            candidates = [
                item
                for item in products
                if item["color"] == "blue" and item["stock"] > 0 and item["price"] < 25
            ]
            product = min(candidates, key=lambda item: (item["price"], item["id"]))
            answer = f"The cheapest in-stock blue mug below $25 is {product['name']}."
        elif task_kind == "cart":
            call("get_cart", {})
            call("set_cart_quantity", {"productId": "mug-blue", "quantity": 2})
            call("set_cart_quantity", {"productId": "cable", "quantity": 0})
            answer = "The cart now has two Blue Mugs and no USB-C Cable."
        elif task_kind == "shipping":
            call("save_shipping", public_task["inputs"])
            answer = "The fictional shipping details were saved."
        elif task_kind == "stock":
            try:
                call("set_cart_quantity", {"productId": "mug-blue", "quantity": 99})
            except ToolInvocationError:
                answer = "I could not set that quantity because there is insufficient stock."
            else:
                answer = "The requested quantity was accepted."
        else:
            raise ValueError(f"unsupported mock task kind: {task_kind}")
    else:
        snapshot = call("snapshot", {})
        if task_kind == "catalog":
            answer = "The cheapest in-stock blue mug below $25 is Blue Mug."
        elif task_kind == "cart":
            blue_ref = _find_dom_ref(snapshot, "Quantity for Blue Mug")
            cable_ref = _find_dom_ref(snapshot, "Quantity for USB-C Cable")
            apply_ref = _find_dom_ref(snapshot, "Apply cart quantities")
            call("fill", {"ref": blue_ref, "value": "2"})
            call("fill", {"ref": cable_ref, "value": "0"})
            call("click", {"ref": apply_ref})
            answer = "The cart now has two Blue Mugs and no USB-C Cable."
        elif task_kind == "shipping":
            fields = []
            for field in ("recipient", "address", "note"):
                fields.append(
                    (_find_dom_ref(snapshot, f"Shipping {field}"), public_task["inputs"][field])
                )
            save_ref = _find_dom_ref(snapshot, "Save shipping")
            for reference, value in fields:
                call("fill", {"ref": reference, "value": value})
            call("click", {"ref": save_ref})
            answer = "The fictional shipping details were saved."
        elif task_kind == "stock":
            quantity_ref = _find_dom_ref(snapshot, "Quantity for Blue Mug")
            apply_ref = _find_dom_ref(snapshot, "Apply cart quantities")
            call("fill", {"ref": quantity_ref, "value": "99"})
            try:
                call("click", {"ref": apply_ref})
            except ToolInvocationError:
                answer = "I could not set that quantity because there is insufficient stock."
            else:
                answer = "The requested quantity was accepted."
        else:
            raise ValueError(f"unsupported mock task kind: {task_kind}")

    return {
        "answer": answer,
        "final_state": environment.final_state(),
        "trace": trace,
        "metrics": {
            "model_requests": request_index,
            "tool_invocations": sum(item["type"] == "tool_call" for item in trace),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        },
    }


def sleep_for(seconds: float) -> None:
    """Small picklable sleeper used to prove the supervisor timeout path."""

    time.sleep(seconds)


__all__ = [
    "CATALOG",
    "DOM_TOOLS",
    "EnvironmentError",
    "MockInterface",
    "MockShopEnvironment",
    "SEED_VERSION",
    "ToolInvocationError",
    "WEBMCP_TOOLS",
    "create_seed_state",
    "run_mock_attempt",
    "sleep_for",
    "state_view",
]
