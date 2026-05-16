#!/usr/bin/env python3
"""
Zomato Food Ordering Bot with OpenAI Structured Outputs.
Uses Pydantic models for all tool payloads - GPT returns structured JSON.
"""

import json
import os
import sys
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import requests
except ImportError:
    import subprocess as sub
    sub.check_call([sys.executable, "-m", "uv", "add", "requests"])
    import requests

try:
    from openai import OpenAI
    from pydantic import BaseModel, Field
except ImportError:
    import subprocess as sub
    sub.check_call([sys.executable, "-m", "uv", "add", "pydantic"])
    from openai import OpenAI
    from pydantic import BaseModel, Field


BASE_URL = "https://mcp-server.zomato.com/mcp"
SESSION_FILE = os.path.expanduser("~/.zomato_mcp_session.json")
TOKEN_DIR = Path(os.path.expanduser("~/.zomato_mcp_tokens"))
TOKEN_DIR.mkdir(parents=True, exist_ok=True)
MCP_REMOTE_VERSION = "0.1.38"


# PYDANTIC MODELS

class GetSavedAddressesForUser(BaseModel):
    pass


class GetRestaurantsForKeyword(BaseModel):
    address_id: str
    keyword: str
    page_size: int = 10
    filter: Optional[dict] = None


class GetMenuItemsListing(BaseModel):
    res_id: int
    address_id: str


class GetRestaurantMenuByCategories(BaseModel):
    res_id: int
    categories: list[str]
    address_id: str


class CartItemInput(BaseModel):
    variant_id: str
    quantity: int = 1
    add_ons: Optional[list[dict]] = None


class CreateCart(BaseModel):
    res_id: int
    items: list[CartItemInput]
    address_id: str
    payment_type: str
    promo_code: Optional[str] = None


class CheckoutCart(BaseModel):
    cart_id: str


class GetOrderHistory(BaseModel):
    address_id: str
    search_query: Optional[str] = None


class ToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool: str
    result: Any
    success: bool = True


# TOKEN MANAGEMENT

def save_session(data: dict) -> None:
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_session() -> dict | None:
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    return None


def clear_session() -> None:
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)


def _get_token_path() -> Path:
    return TOKEN_DIR / "zomato_tokens.json"


def save_tokens(tokens: dict) -> None:
    with open(_get_token_path(), "w") as f:
        json.dump(tokens, f, indent=2)


def load_tokens() -> dict | None:
    path = _get_token_path()
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return None


def clear_tokens() -> None:
    path = _get_token_path()
    if path.exists():
        os.remove(path)


def run_mcp_remote_oauth(timeout_seconds: int = 300) -> dict[str, Any]:
    if shutil.which("npx") is None:
        return {"error": "npx not found. Install Node.js 20+."}

    config_dir = tempfile.mkdtemp(prefix="zomato-mcp-auth-")
    env = os.environ.copy()
    env["MCP_REMOTE_CONFIG_DIR"] = config_dir

    cmd = ["npx", "-y", f"mcp-remote@{MCP_REMOTE_VERSION}", BASE_URL, "--auth-timeout", str(timeout_seconds)]

    print("\nStarting MCP authorization through mcp-remote...")
    print("A browser window should open automatically.")
    print("Complete the sign-in with your phone + OTP, then return here.\n")

    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)

    def stream_output():
        if not proc.stdout:
            return
        for line in proc.stdout:
            print(line.rstrip())

    threading.Thread(target=stream_output, daemon=True).start()

    try:
        proc.communicate(timeout=timeout_seconds + 10)
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"error": f"mcp-remote timed out after {timeout_seconds} seconds"}

    if proc.returncode != 0:
        return {"error": f"mcp-remote failed with code {proc.returncode}"}

    return {"status": "success", "config_dir": config_dir}


def find_tokens_in_dir(config_dir: str) -> dict[str, Any] | None:
    config_path = Path(config_dir)
    if not config_path.exists():
        return None

    import hashlib
    url_hash = hashlib.md5(BASE_URL.encode()).hexdigest()

    for pattern in [f"*{url_hash}*", "*tokens*", "*mcp-remote*"]:
        for token_file in config_path.rglob(pattern):
            if token_file.name.endswith("_tokens.json") and token_file.is_file():
                try:
                    tokens = json.loads(token_file.read_text())
                    if "access_token" in tokens:
                        return tokens
                except (json.JSONDecodeError, OSError):
                    pass
    return None


# MCP REQUEST HANDLER

def make_request(tool_name: str, arguments: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}}

    try:
        resp = requests.post(BASE_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()

        if 'text/event-stream' in resp.headers.get('Content-Type', ''):
            for line in resp.text.split('\n'):
                if line.startswith('data:'):
                    data = line[5:].strip()
                    if data:
                        try:
                            return json.loads(data)
                        except json.JSONDecodeError:
                            continue
            return {"error": "No valid JSON in SSE response"}

        return resp.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def parse_mcp_response(response: dict[str, Any]) -> Any:
    if "result" not in response:
        return response
    content = response["result"].get("content", [])
    if content and isinstance(content, list):
        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return response


# ZOMATO BOT WITH STRUCTURED OUTPUTS

class ZomatoBot:
    def __init__(self, api_key: str | None = None):
        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self.tokens = load_tokens()
        self.authenticated = bool(self.tokens and self.tokens.get("access_token"))

        self.addresses: list[dict] = []
        self.selected_address_id: str | None = None
        self.restaurants: list[dict] = []
        self.selected_restaurant: dict | None = None
        self.menu_items: list[dict] = []
        self.cart_id: str | None = None

    def _get_tool_schemas(self) -> list[dict]:
        return [
            {"type": "function", "function": {"name": "get_saved_addresses_for_user", "description": "Get user's saved delivery addresses. Call this first to get address_id.", "parameters": {"type": "object", "properties": {}, "required": []}}},
            {"type": "function", "function": {"name": "get_restaurants_for_keyword", "description": "Search restaurants by keyword. Requires address_id from get_saved_addresses_for_user.", "parameters": GetRestaurantsForKeyword.model_json_schema()}},
            {"type": "function", "function": {"name": "get_menu_items_listing", "description": "Get restaurant menu categories.", "parameters": GetMenuItemsListing.model_json_schema()}},
            {"type": "function", "function": {"name": "get_restaurant_menu_by_categories", "description": "Get detailed menu items for categories.", "parameters": GetRestaurantMenuByCategories.model_json_schema()}},
            {"type": "function", "function": {"name": "create_cart", "description": "Create food cart with items.", "parameters": CreateCart.model_json_schema()}},
            {"type": "function", "function": {"name": "checkout_cart", "description": "Place order. Returns QR code for payment.", "parameters": CheckoutCart.model_json_schema()}},
            {"type": "function", "function": {"name": "get_order_history", "description": "Get user's order history.", "parameters": GetOrderHistory.model_json_schema()}},
            {"type": "function", "function": {"name": "get_order_tracking_info", "description": "Track active orders.", "parameters": {"type": "object", "properties": {}, "required": []}}},
        ]

    def call_mcp(self, tool_name: str, arguments: dict) -> Any:
        if not self.authenticated:
            return {"error": "Not authenticated"}

        token = self.tokens.get("access_token") if self.tokens else None
        result = make_request(tool_name, arguments, token)
        return parse_mcp_response(result)

    def _build_system_prompt(self) -> str:
        address_info = ""
        if self.selected_address_id:
            address_info = f"\n- Selected address_id: {self.selected_address_id}"
        if self.addresses:
            address_info += f"\n- User has {len(self.addresses)} saved addresses"
        if self.restaurants:
            address_info += f"\n- Last search found {len(self.restaurants)} restaurants"
        if self.selected_restaurant:
            address_info += f"\n- Selected restaurant: {self.selected_restaurant.get('name')} (res_id: {self.selected_restaurant.get('res_id')})"

        return f"""You are a Zomato food ordering assistant. Follow this workflow:

1. If no address selected, call get_saved_addresses_for_user first
2. Then call get_restaurants_for_keyword with address_id and user query as keyword
3. Show results and let user pick a restaurant
4. Then call get_menu_items_listing to get categories
5. Then call get_restaurant_menu_by_categories to get items
6. Build cart with create_cart
7. Confirm and checkout with checkout_cart

Current bot state: {address_info or "No context yet - start fresh"}

Use structured outputs only."""

    def handle_user_message(self, user_message: str) -> tuple[str, list[ToolCall], list[ToolResult]]:
        messages = [{"role": "system", "content": self._build_system_prompt()}, {"role": "user", "content": user_message}]
        tools = self._get_tool_schemas()

        response = self.client.chat.completions.create(model="gpt-4o-mini", messages=messages, tools=tools, tool_choice="auto")

        msg = response.choices[0].message
        tool_calls = []
        tool_results = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                tool_calls.append(ToolCall(tool=tool_name, arguments=args))

                if tool_name == "get_saved_addresses_for_user":
                    result = self.call_mcp(tool_name, args)
                    if isinstance(result, dict) and "addresses" in result:
                        self.addresses = result["addresses"]
                    tool_results.append(ToolResult(tool=tool_name, result=result))

                elif tool_name == "get_restaurants_for_keyword":
                    result = self.call_mcp(tool_name, args)
                    if isinstance(result, dict) and "results" in result:
                        self.restaurants = result["results"]
                    tool_results.append(ToolResult(tool=tool_name, result=result))

                elif tool_name == "get_menu_items_listing":
                    result = self.call_mcp(tool_name, args)
                    if isinstance(result, dict):
                        mappings = result.get("data", {}).get("item_mappings", [])
                        self.menu_items = mappings
                    tool_results.append(ToolResult(tool=tool_name, result=result))

                elif tool_name == "get_restaurant_menu_by_categories":
                    result = self.call_mcp(tool_name, args)
                    if isinstance(result, dict):
                        items = result.get("data", {}).get("menu_items", [])
                        self.menu_items = items
                    tool_results.append(ToolResult(tool=tool_name, result=result))

                elif tool_name == "create_cart":
                    result = self.call_mcp(tool_name, args)
                    if isinstance(result, dict) and result.get("success"):
                        self.cart_id = result.get("cart", {}).get("cart_id")
                    tool_results.append(ToolResult(tool=tool_name, result=result))

                elif tool_name == "checkout_cart":
                    result = self.call_mcp(tool_name, args)
                    tool_results.append(ToolResult(tool=tool_name, result=result))

                elif tool_name == "get_order_history":
                    result = self.call_mcp(tool_name, args)
                    tool_results.append(ToolResult(tool=tool_name, result=result))

                elif tool_name == "get_order_tracking_info":
                    result = self.call_mcp(tool_name, args)
                    tool_results.append(ToolResult(tool=tool_name, result=result))

        reply = msg.content or "I didn't understand that. What would you like to eat?"
        return reply, tool_calls, tool_results

    def format_response_for_user(self, tool_calls: list[ToolCall], tool_results: list[ToolResult]) -> str:
        if not tool_results:
            return ""

        responses = []

        for tc, tr in zip(tool_calls, tool_results):
            if tc.tool == "get_saved_addresses_for_user" and isinstance(tr.result, dict):
                addrs = tr.result.get("addresses", [])
                if addrs:
                    self.addresses = addrs
                    if not self.selected_address_id:
                        self.selected_address_id = addrs[0]["address_id"]
                    resp = "📍 Your saved addresses:\n"
                    for i, a in enumerate(addrs[:5]):
                        resp += f"{i+1}. {a['location_name'][:60]}\n"
                    resp += "\nI'll use your first address. What would you like to eat?"
                    responses.append(resp)

            elif tc.tool == "get_restaurants_for_keyword" and isinstance(tr.result, dict):
                results = tr.result.get("results", [])
                if results:
                    self.restaurants = results
                    resp = "🍽️ Found these restaurants:\n"
                    for i, r in enumerate(results[:5]):
                        resp += f"{i+1}. **{r['name']}** - {r['rating']}⭐ ({r['eta']}, {r['distance']}km)\n"
                        if r.get("res_offer"):
                            resp += f"   🎉 {r['res_offer']}\n"
                    resp += "\nWhich restaurant would you like to order from?"
                    responses.append(resp)
                else:
                    responses.append("No restaurants found. Try a different search.")

            elif tc.tool == "get_menu_items_listing" and isinstance(tr.result, dict):
                mappings = tr.result.get("data", {}).get("item_mappings", [])
                if mappings:
                    cats = list(set(cat for m in mappings for c in m.get("categories", []) for cat in [c]))
                    resp = "📋 Menu categories:\n"
                    for i, c in enumerate(cats[:6]):
                        resp += f"{i+1}. {c}\n"
                    resp += "\nWhich category would you like to see?"
                    responses.append(resp)

            elif tc.tool == "get_restaurant_menu_by_categories" and isinstance(tr.result, dict):
                items = tr.result.get("data", {}).get("menu_items", [])
                if items:
                    self.menu_items = items
                    resp = "🍕 Available items:\n"
                    for i, item in enumerate(items[:8]):
                        price = item.get("price", 0)
                        resp += f"{i+1}. {item.get('name')} - ₹{price}\n"
                    resp += "\nWhat would you like to add to your cart? (e.g., '1, 3' or '1')"
                    responses.append(resp)
                else:
                    responses.append("No items found in this category.")

            elif tc.tool == "create_cart" and isinstance(tr.result, dict):
                if tr.result.get("success"):
                    cart = tr.result.get("cart", {})
                    resp = f"✅ Added to cart! Cart ID: {cart.get('cart_id', 'N/A')}\n"
                    resp += "Type 'checkout' to place your order."
                    responses.append(resp)
                else:
                    responses.append(f"Failed to create cart: {tr.result}")

            elif tc.tool == "checkout_cart" and isinstance(tr.result, dict):
                if tr.result.get("success"):
                    data = tr.result.get("data", {})
                    resp = "🎉 Order placed successfully!\n"
                    if data.get("qr_code_url"):
                        resp += f"QR Code URL: {data['qr_code_url']}\n"
                    resp += "You can pay via the QR code."
                    responses.append(resp)
                else:
                    resp = f"Checkout info: {json.dumps(tr.result, indent=2)[:500]}"
                    responses.append(resp)

            elif tc.tool == "get_order_history":
                responses.append(f"Order history: {json.dumps(tr.result, indent=2)[:500]}")

            elif tc.tool == "get_order_tracking_info":
                responses.append(f"Tracking info: {json.dumps(tr.result, indent=2)[:500]}")

        return "\n\n".join(responses) if responses else ""


# MAIN

def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY not set.")
        sys.exit(1)

    print("=" * 50)
    print("  Zomato Food Ordering Bot")
    print("=" * 50)

    bot = ZomatoBot(api_key=api_key)

    if bot.authenticated and bot.tokens:
        print("\n[Session loaded - you are authenticated]")
    else:
        print("\n[Starting authentication flow...]")
        result = run_mcp_remote_oauth()
        if "error" in result:
            print(f"\n[Error] {result['error']}")
            sys.exit(1)
        tokens = find_tokens_in_dir(result["config_dir"])
        if tokens:
            save_tokens(tokens)
            bot.tokens = tokens
            bot.authenticated = True
            print("\n[OK] Authentication successful!")
        else:
            print("\n[Error] Could not find tokens")
            sys.exit(1)

    print("\nBot: Hi! I'm your Zomato assistant. What would you like to eat today?")
    print("(Type 'quit' to exit)\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if user_input.lower() in ("quit", "exit", "q"):
                print("\nBot: Goodbye! Happy eating! 🍔")
                break
            if not user_input:
                continue

            reply, tool_calls, tool_results = bot.handle_user_message(user_input)
            formatted = bot.format_response_for_user(tool_calls, tool_results)

            if formatted:
                print(f"\nBot: {formatted}\n")
            elif reply:
                print(f"\nBot: {reply}\n")

        except KeyboardInterrupt:
            print("\n\nBot: Goodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


if __name__ == "__main__":
    main()