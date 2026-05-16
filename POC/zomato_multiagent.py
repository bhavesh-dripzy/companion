#!/usr/bin/env python3
"""
Multi-Agent Zomato Food Ordering Bot using OpenAI's Agent SDK (openai-agents).

Architecture:
  ┌──────────────────────────────────────────────────────────┐
  │                    Main Agent                           │
  │  (Orchestrator - handles conversation, routes to Zomato) │
  │                                                          │
  │  • Greets user, understands intent                       │
  │  • Calls Zomato Agent as a tool when MCP ops needed      │
  │  • Can also hand off to Zomato Agent for full flow       │
  │  • Formats final output for the user                     │
  └──────────────────┬───────────────────────────────────────┘
                     │ tool call / handoff
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │                   Zomato Agent                           │
  │  (Specialist - all Zomato MCP tools + structured output)  │
  │                                                           │
  │  • 8 MCP tools for Zomato operations                     │
  │  • Returns structured ZomatoResponse (Pydantic)           │
  │  • Instructions dynamically built with current state      │
  └──────────────────────────────────────────────────────────┘

State Management:
  - Global ZomatoState dataclass shared across all agents & tools
  - Updated by tools after each MCP call
  - State snapshot included in ZomatoResponse

Post-Processing (non-hardcoded):
  - ZomatoHooks(RunHooks): on_agent_end, on_tool_end, on_handoff
  - Structured outputs from Zomato Agent (not hardcoded text formatters)
  - Main Agent naturally formats output based on structured data

Usage:
  uv run python zomato_multiagent.py

Requires:
  - OPENAI_API_KEY environment variable
  - Node.js 20+ (for mcp-remote OAuth)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import requests
from agents import (
    Agent,
    ModelSettings,
    RunHooks,
    RunContextWrapper,
    Runner,
    function_tool,
    handoff,
    set_default_openai_api,
)
from agents.result import RunResult
from pydantic import BaseModel, Field, field_validator


class CartAddOn(BaseModel):
    """An add-on or extra for a food item."""
    add_on_id: str = Field(description="The add-on option identifier")
    quantity: int = Field(default=1, description="How many of this add-on")


class CartItemRequest(BaseModel):
    """A single menu item to add to the cart."""
    variant_id: str = Field(description="The variant/option ID of the food item")
    quantity: int = Field(default=1, description="How many of this item to order")
    add_ons: list[CartAddOn] | None = Field(
        default=None,
        description="Optional add-ons or extras for this item",
    )

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: int) -> int:
        return max(v, 1)

# ── Configuration ────────────────────────────────────────────────────
BASE_URL = "https://mcp-server.zomato.com/mcp"
TOKEN_DIR = Path(os.path.expanduser("~/.zomato_mcp_tokens"))
TOKEN_DIR.mkdir(parents=True, exist_ok=True)
MCP_REMOTE_VERSION = "0.1.38"

# Use Chat Completions API for broader LLM compatibility
# (default is Responses API which is OpenAI-only)
set_default_openai_api("chat_completions")


# ── State Management ─────────────────────────────────────────────────
@dataclass
class ZomatoState:
    """Mutable state shared across all agents and tools.

    This is a global singleton that persists across conversation turns.
    Tools update it directly after each MCP call. The Zomato Agent's
    instructions are dynamically rebuilt from this state before each turn.
    """

    addresses: list[dict] = field(default_factory=list)
    selected_address_id: str | None = None
    restaurants: list[dict] = field(default_factory=list)
    selected_restaurant: dict | None = None
    menu_items: list[dict] = field(default_factory=list)
    cart_id: str | None = None
    tokens: dict | None = None
    authenticated: bool = False
    handoff_history: list[dict] = field(default_factory=list)
    last_agent: str | None = None
    stage: str = "initial"

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot excluding tokens."""
        d = asdict(self)
        d.pop("tokens", None)
        return d


_state: ZomatoState | None = None


def get_state() -> ZomatoState:
    global _state
    if _state is None:
        _state = ZomatoState()
    return _state


# ── Structured Output (Post-Processing Foundation) ───────────────────
class ZomatoResponse(BaseModel):
    """Structured output from the Zomato Agent.

    Instead of hardcoding a 'format_response_for_user' method (as in the
    original script), the LLM returns this structured object. The Main
    Agent then presents the message to the user.

    State tracking is handled by the global ZomatoState + hooks,
    not via dict fields (which fail Agent SDK strict JSON schema).
    """

    message: str = Field(
        description="Natural language response to present to the user"
    )
    stage: str = Field(
        description="Current ordering stage: initial|address|restaurants|menu|cart|checkout|done"
    )
    needs_followup: bool = Field(
        default=True,
        description="Whether the agent expects more user input",
    )


# ── Token / Auth (reused from original script) ───────────────────────
def save_tokens(tokens: dict) -> None:
    (TOKEN_DIR / "zomato_tokens.json").write_text(json.dumps(tokens, indent=2))


def load_tokens() -> dict | None:
    path = TOKEN_DIR / "zomato_tokens.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


# ── MCP Communication (reused from original script) ──────────────────
def make_mcp_request(
    tool_name: str, arguments: dict[str, Any], token: str | None = None
) -> dict[str, Any]:
    """Send a JSON-RPC request to the Zomato MCP server."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }

    try:
        resp = requests.post(BASE_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()

        # Handle SSE responses
        if "text/event-stream" in resp.headers.get("Content-Type", ""):
            for line in resp.text.split("\n"):
                if line.startswith("data:"):
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
    """Extract the JSON payload from an MCP response envelope."""
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


# ── Tool Definitions (@function_tool) ────────────────────────────────
# These tools wrap the raw MCP calls and update the global ZomatoState.
# They are registered on the Zomato Agent and NOT on the Main Agent.
#
# Note: RunContextWrapper is intentionally omitted since we use global
# state. Each tool signature is derived from type hints + docstring.

@function_tool
def get_saved_addresses_for_user() -> str:
    """Get the user's saved delivery addresses from Zomato. Call this first to get an address_id, which is needed for all subsequent operations."""
    state = get_state()
    if not state.authenticated:
        return json.dumps({"error": "Not authenticated. Run authentication first."})

    token = state.tokens.get("access_token") if state.tokens else None
    result = make_mcp_request("get_saved_addresses_for_user", {}, token)
    parsed = parse_mcp_response(result)

    if isinstance(parsed, dict) and "addresses" in parsed:
        state.addresses = parsed["addresses"]
        if not state.selected_address_id and parsed["addresses"]:
            state.selected_address_id = parsed["addresses"][0].get("address_id")
            state.stage = "address"

    return json.dumps(parsed)


@function_tool
def get_restaurants_for_keyword(
    keyword: str, address_id: str, page_size: int = 10
) -> str:
    """Search for restaurants on Zomato by food keyword/craving. Requires an address_id which you get from get_saved_addresses_for_user."""
    state = get_state()
    if not state.authenticated:
        return json.dumps({"error": "Not authenticated"})

    token = state.tokens.get("access_token") if state.tokens else None
    result = make_mcp_request(
        "get_restaurants_for_keyword",
        {"keyword": keyword, "address_id": address_id, "page_size": page_size},
        token,
    )
    parsed = parse_mcp_response(result)

    if isinstance(parsed, dict) and "results" in parsed:
        state.restaurants = parsed["results"]
        state.stage = "restaurants"

    return json.dumps(parsed)


@function_tool
def get_menu_items_listing(res_id: int, address_id: str) -> str:
    """Get the menu categories for a restaurant. Call this after the user selects a restaurant to show them what categories are available."""
    state = get_state()
    if not state.authenticated:
        return json.dumps({"error": "Not authenticated"})

    token = state.tokens.get("access_token") if state.tokens else None
    result = make_mcp_request(
        "get_menu_items_listing",
        {"res_id": res_id, "address_id": address_id},
        token,
    )
    parsed = parse_mcp_response(result)

    if isinstance(parsed, dict):
        mappings = parsed.get("data", {}).get("item_mappings", [])
        if mappings:
            state.menu_items = mappings
            state.stage = "menu"
            for r in state.restaurants:
                if r.get("res_id") == res_id:
                    state.selected_restaurant = r
                    break

    return json.dumps(parsed)


@function_tool
def get_restaurant_menu_by_categories(
    res_id: int, categories: list[str], address_id: str
) -> str:
    """Get detailed menu items (with prices) for specific categories. Call after the user picks a category from get_menu_items_listing."""
    state = get_state()
    if not state.authenticated:
        return json.dumps({"error": "Not authenticated"})

    token = state.tokens.get("access_token") if state.tokens else None
    result = make_mcp_request(
        "get_restaurant_menu_by_categories",
        {"res_id": res_id, "categories": categories, "address_id": address_id},
        token,
    )
    parsed = parse_mcp_response(result)

    if isinstance(parsed, dict):
        items = parsed.get("data", {}).get("menu_items", [])
        if items:
            state.menu_items = items

    return json.dumps(parsed)


@function_tool
def create_cart(
    res_id: int,
    items: list[CartItemRequest],
    address_id: str,
    payment_type: str = "COD",
    promo_code: str | None = None,
) -> str:
    """Create a food cart with selected items. Each item needs a variant_id and quantity. Payment type: COD (Cash on Delivery) or ONLINE."""
    state = get_state()
    if not state.authenticated:
        return json.dumps({"error": "Not authenticated"})

    token = state.tokens.get("access_token") if state.tokens else None
    items_dict = [item.model_dump(exclude_none=True) for item in items]
    args: dict[str, Any] = {
        "res_id": res_id,
        "items": items_dict,
        "address_id": address_id,
        "payment_type": payment_type,
    }
    if promo_code:
        args["promo_code"] = promo_code

    result = make_mcp_request("create_cart", args, token)
    parsed = parse_mcp_response(result)

    if isinstance(parsed, dict) and parsed.get("success"):
        cart = parsed.get("cart", {})
        state.cart_id = cart.get("cart_id")
        state.stage = "cart"

    return json.dumps(parsed)


@function_tool
def checkout_cart(cart_id: str) -> str:
    """Place the order for the current cart. Confirms the order and returns payment info (QR code etc.)."""
    state = get_state()
    if not state.authenticated:
        return json.dumps({"error": "Not authenticated"})

    token = state.tokens.get("access_token") if state.tokens else None
    result = make_mcp_request("checkout_cart", {"cart_id": cart_id}, token)
    parsed = parse_mcp_response(result)

    if isinstance(parsed, dict) and parsed.get("success"):
        state.stage = "done"

    return json.dumps(parsed)


@function_tool
def get_order_history(address_id: str, search_query: str | None = None) -> str:
    """Get the user's past Zomato orders. Use search_query to filter by restaurant name or dish."""
    state = get_state()
    if not state.authenticated:
        return json.dumps({"error": "Not authenticated"})

    token = state.tokens.get("access_token") if state.tokens else None
    args: dict[str, Any] = {"address_id": address_id}
    if search_query:
        args["search_query"] = search_query

    result = make_mcp_request("get_order_history", args, token)
    return json.dumps(parse_mcp_response(result))


@function_tool
def get_order_tracking_info() -> str:
    """Track the user's currently active Zomato orders. Shows status, ETA, and delivery progress."""
    state = get_state()
    if not state.authenticated:
        return json.dumps({"error": "Not authenticated"})

    token = state.tokens.get("access_token") if state.tokens else None
    result = make_mcp_request("get_order_tracking_info", {}, token)
    return json.dumps(parse_mcp_response(result))


# ── Post-Processing Hooks ────────────────────────────────────────────
class ZomatoHooks(RunHooks[Any]):
    """Lifecycle hooks for post-processing agent events.

    This is the non-hardcoded post-processing layer. Instead of a rigid
    format_response_for_user() method (original script), we use:

    1. on_agent_end  → capture and potentially transform agent output
    2. on_handoff     → track agent transitions for observability
    3. on_tool_end    → state is updated directly in tool functions

    The hooks fire at every stage of agent execution and provide
    a generic interception point for logging, metrics, state capture,
    response transformation, etc.
    """

    async def on_agent_start(
        self, context: RunContextWrapper[Any], agent: Agent[Any]
    ) -> None:
        """Pre-processing: reset state tracking for this agent turn."""
        state = get_state()
        state.last_agent = agent.name

    async def on_agent_end(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        output: Any,
    ) -> None:
        """Post-processing: capture agent output for observability.

        This is the primary post-processing hook. For the Zomato Agent,
        the output is a ZomatoResponse. For the Main Agent, it's text.
        State tracking is done via global state + tool updates.
        """
        state = get_state()
        state.last_agent = agent.name

    async def on_handoff(
        self,
        context: RunContextWrapper[Any],
        from_agent: Agent[Any],
        to_agent: Agent[Any],
    ) -> None:
        """Post-processing: log agent handoffs for observability."""
        state = get_state()
        entry = {
            "from": from_agent.name,
            "to": to_agent.name,
            "timestamp": time.time(),
        }
        state.handoff_history.append(entry)

    async def on_tool_start(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        tool: Any,
    ) -> None:
        pass

    async def on_tool_end(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        tool: Any,
        result: Any,
    ) -> None:
        pass


# ── Agent Setup ──────────────────────────────────────────────────────
def build_zomato_instructions() -> str:
    """Dynamically build Zomato Agent instructions with current state injected.

    This is how state flows into the agent without hardcoded context
    passing. Before each run, we rebuild the instructions from the
    current ZomatoState.
    """
    state = get_state()

    ctx_parts: list[str] = []
    if state.selected_address_id:
        ctx_parts.append(f"- Selected address_id: {state.selected_address_id}")
    if state.addresses:
        ctx_parts.append(f"- Found {len(state.addresses)} saved addresses")
    if state.restaurants:
        ctx_parts.append(
            f"- Last restaurant search returned {len(state.restaurants)} results"
        )
    if state.selected_restaurant:
        name = state.selected_restaurant.get("name", "?")
        rid = state.selected_restaurant.get("res_id", "?")
        ctx_parts.append(f"- Currently viewing: {name} (res_id: {rid})")
    if state.menu_items:
        ctx_parts.append(f"- {len(state.menu_items)} menu items loaded")
    if state.cart_id:
        ctx_parts.append(f"- Active cart ID: {state.cart_id}")
    ctx_parts.append(f"- Current ordering stage: {state.stage}")

    context_block = "\n".join(ctx_parts) if ctx_parts else "  (No context yet - start fresh)"

    return f"""You are the Zomato food ordering specialist agent. You handle ALL Zomato MCP operations.

AVAILABLE TOOLS (call only when needed — follow the workflow below):
1. get_saved_addresses_for_user — Fetch saved delivery addresses
2. get_restaurants_for_keyword — Search restaurants by food keyword
3. get_menu_items_listing — Get menu categories for a restaurant
4. get_restaurant_menu_by_categories — Get items with prices for specific categories
5. create_cart — Build a cart with selected items
6. checkout_cart — Confirm and pay for the order
7. get_order_history — Look up past orders
8. get_order_tracking_info — Check active order status

WORKFLOW:
1. If no address_id is set, call get_saved_addresses_for_user first
2. Then call get_restaurants_for_keyword with address_id + user's craving
3. Present restaurants clearly; let the user pick one
4. Call get_menu_items_listing to show menu categories
5. If user wants specific items, call get_restaurant_menu_by_categories
6. Build the cart with create_cart when user selects items + quantities
7. Call checkout_cart when user confirms the order

DEAL DETECTION:
When the user asks for "cheapest", "best deal", "maximum discount":
- Parse discount amounts from the res_offer field (e.g. "Flat ₹150 OFF" → 150)
- Show the best value option first
- Sort other options by discount descending

RESPONSE REQUIREMENTS:
- NEVER show raw JSON to the user
- Present information clearly and conversationally
- Always return a ZomatoResponse with a helpful message for the user

CURRENT BOT STATE:
{context_block}
"""


# Register all MCP tools on a list
_zomato_tools = [
    get_saved_addresses_for_user,
    get_restaurants_for_keyword,
    get_menu_items_listing,
    get_restaurant_menu_by_categories,
    create_cart,
    checkout_cart,
    get_order_history,
    get_order_tracking_info,
]

# The Zomato Agent: a specialist that can use all 8 MCP tools
# and returns structured ZomatoResponse objects.
zomato_agent = Agent[Any](
    name="Zomato Agent",
    instructions=build_zomato_instructions(),
    tools=_zomato_tools,
    output_type=ZomatoResponse,
    model_settings=ModelSettings(temperature=0.3),
)

# Prepare the Zomato Agent as a tool for the Main Agent.
# This lets the Main Agent delegate MCP operations to the specialist
# while retaining control of the conversation flow.
zomato_tool = zomato_agent.as_tool(
    tool_name="order_food",
    tool_description="Delegate food ordering operations to the Zomato specialist. Pass the user's full request as input. Handles: restaurant search, menu browsing, cart creation, checkout, order history, and order tracking.",
    max_turns=15,  # Allow multiple tool calls within the Zomato Agent
)

# Prepare a handoff target: the Main Agent can transfer control
# to the Zomato Agent for a full ordering conversation.
zomato_handoff = handoff(
    agent=zomato_agent,
    tool_name_override="transfer_to_zomato",
    tool_description_override="Transfer the conversation to the Zomato ordering specialist for complete food ordering assistance.",
)

# The Main Agent: orchestrator that handles conversation flow.
# It can:
# 1. Respond directly to general chat
# 2. Call order_food tool (Zomato Agent as tool) for MCP operations
# 3. Hand off to Zomato Agent via transfer_to_zomato for full ordering
main_agent = Agent[Any](
    name="Main Agent",
    instructions="""You are a friendly food ordering assistant. Your role is to understand what the user wants and route them appropriately.

CAPABILITIES:
1. **General chat** → respond directly
2. **Food ordering** → use the `order_food` tool to delegate to the Zomato specialist
3. **Handoff** → use `transfer_to_zomato` to transfer control to the Zomato specialist for a complete ordering conversation

HOW TO USE THE order_food TOOL:
- When the user wants to order food, search restaurants, browse menus, create a cart, checkout, or view order history/tracking
- Pass the user's COMPLETE request as input to the tool
- The Zomato specialist will handle all MCP operations and return structured results
- Present the results naturally to the user
- If the response says needs_followup=true, ask the user what they'd like to do next

EXAMPLE FLOW:
1. User: "I want pizza" → call order_food("I want pizza")
2. Zomato returns restaurants → you: "Here are pizza places near you..."
3. User: "Show me Dominos" → call order_food("Show me Dominos menu")
4. Zomato returns menu → you: "Here's Dominos menu..."
5. Continue until order is placed or user is done

OUTPUT STYLE:
- Be friendly and conversational
- Format restaurant info nicely with ratings and discounts
- Ask clear follow-up questions to move the order forward
""",
    tools=[zomato_tool],
    handoffs=[zomato_handoff],
    model_settings=ModelSettings(temperature=0.7),
)


def refresh_zomato_instructions() -> None:
    """Rebuild Zomato Agent instructions from the latest state.

    Call before each agent run so the specialist agent is aware of
    the current ordering context (addresses, restaurants, cart, etc.).
    """
    zomato_agent.instructions = build_zomato_instructions()


# ── OAuth Flow (reused from original script) ─────────────────────────
def run_mcp_remote_oauth(timeout_seconds: int = 300) -> dict[str, Any]:
    """Launch mcp-remote OAuth flow in a subprocess."""
    if shutil.which("npx") is None:
        return {"error": "npx not found. Install Node.js 20+."}

    config_dir = tempfile.mkdtemp(prefix="zomato-mcp-auth-")
    env = os.environ.copy()
    env["MCP_REMOTE_CONFIG_DIR"] = config_dir

    cmd = [
        "npx", "-y",
        f"mcp-remote@{MCP_REMOTE_VERSION}",
        BASE_URL,
        "--auth-timeout", str(timeout_seconds),
    ]

    print("\nStarting MCP authorization through mcp-remote...")
    print("A browser window should open automatically.")
    print("Complete the sign-in with your phone + OTP, then return here.\n")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

    def _stream_output() -> None:
        if not proc.stdout:
            return
        for line in proc.stdout:
            print(line.rstrip())

    threading.Thread(target=_stream_output, daemon=True).start()

    try:
        proc.communicate(timeout=timeout_seconds + 10)
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"error": f"mcp-remote timed out after {timeout_seconds} seconds"}

    if proc.returncode != 0:
        return {"error": f"mcp-remote failed with code {proc.returncode}"}

    return {"status": "success", "config_dir": config_dir}


def find_tokens_in_dir(config_dir: str) -> dict[str, Any] | None:
    """Find mcp-remote token files in the given config directory."""
    config_path = Path(config_dir)
    if not config_path.exists():
        return None

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


# ── Conversation Loop ────────────────────────────────────────────────

def post_process_response(response: str | ZomatoResponse | None) -> str:
    """Final post-processing step before displaying output to the user.

    This is a generic post-processor that handles edge cases,
    formatting cleanup, and logging. Unlike the original script's
    hardcoded format_response_for_user, this is a stateless,
    type-driven transformation.
    """
    if response is None:
        return ""
    if isinstance(response, ZomatoResponse):
        return response.message
    text = str(response).strip()
    if not text:
        return ""
    return text


async def run_agent_turn(
    user_message: str,
) -> tuple[str, RunResult | None]:
    """Execute one conversation turn through the Main Agent.

    The Main Agent may:
    1. Respond directly (general chat)
    2. Call the Zomato Agent as a tool (single-turn delegation)
    3. Hand off to the Zomato Agent (full ordering flow)

    Returns (formatted_response_text, raw_result).
    """
    # Inject latest state into Zomato Agent's instructions
    refresh_zomato_instructions()

    result = await Runner.run(
        main_agent,
        user_message,
        hooks=ZomatoHooks(),
    )

    # Extract and post-process the final output
    response = result.final_output
    text = post_process_response(response)
    return text, result


# ── Auth Setup ───────────────────────────────────────────────────────
def setup_authentication() -> bool:
    """Authenticate with Zomato MCP. Returns True if successful."""
    state = get_state()

    tokens = load_tokens()
    if tokens and tokens.get("access_token"):
        state.tokens = tokens
        state.authenticated = True
        print("\n[✓] Loaded existing session — you are authenticated")
        return True

    print("\n[→] No saved session found. Starting Zomato authentication...")
    result = run_mcp_remote_oauth()

    if "error" in result:
        print(f"\n[✗] {result['error']}")
        return False

    tokens = find_tokens_in_dir(result["config_dir"])
    if tokens:
        save_tokens(tokens)
        state.tokens = tokens
        state.authenticated = True
        print("\n[✓] Authentication successful! Tokens saved.")
        return True

    print("\n[✗] Could not find tokens after authentication.")
    return False


# ── Entry Point ──────────────────────────────────────────────────────
def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY not set.")
        print("Set it with: export OPENAI_API_KEY='sk-...'")
        sys.exit(1)

    os.environ["OPENAI_API_KEY"] = api_key

    print("=" * 64)
    print("  Multi-Agent Zomato Ordering Bot")
    print("  • Main Agent (orchestrator)")
    print("  • Zomato Agent (MCP specialist) — tool + handoff patterns")
    print("  • Structured outputs — no hardcoded formatters")
    print("  • RunHooks — lifecycle-based post-processing")
    print("  • Global state — shared across agents and turns")
    print("=" * 64)

    # Authenticate
    if not setup_authentication():
        sys.exit(1)

    state = get_state()

    print("\n" + "─" * 64)
    print("  Multi-Agent Architecture: Main Agent ↔ Zomato Agent")
    print("  • The Main Agent orchestrates the conversation")
    print("  • The Zomato Agent handles all Zomato MCP operations")
    print("  • Post-processing via RunHooks (no hardcoded formatters)")
    print("─" * 64)

    print("\nBot: Hi! I'm your multi-agent Zomato assistant. What would you like to eat?")
    print("(Type 'quit' to exit, 'status' to see agent state, 'reset' to start over)\n")

    conversation_history: list = []

    while True:
        try:
            user_input = input("You: ").strip()
            if user_input.lower() in ("quit", "exit", "q"):
                total_handoffs = len(state.handoff_history)
                print("\nBot: Goodbye! Happy eating!")
                if total_handoffs > 0:
                    print(f"[Agent handoffs occurred: {total_handoffs}]")
                break

            if not user_input:
                continue

            if user_input.lower() == "status":
                _print_status(state)
                continue

            if user_input.lower() == "reset":
                _reset_state()
                print("\nBot: State reset. How can I help you?")
                continue

            # Run the agent turn
            response_text, result = asyncio.run(
                run_agent_turn(user_input)
            )

            if response_text:
                print(f"\nBot: {response_text}\n")
            else:
                print("\nBot: Let me know what you'd like to order!\n")

            # Maintain conversation history for context (limit size)
            if result:
                conversation_history = result.to_input_list()
                if len(conversation_history) > 30:
                    conversation_history = conversation_history[-30:]

        except KeyboardInterrupt:
            print("\n\nBot: Goodbye!")
            break
        except Exception as e:
            print(f"\n[Error] {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()
            print("\nBot: Something went wrong. Please try again.")


def _print_status(state: ZomatoState) -> None:
    """Pretty-print the current agent state."""
    print()
    print("  ┌─ Agent State ──────────────────────────────")
    print(f"  │ Stage:         {state.stage}")
    print(f"  │ Authenticated: {state.authenticated}")
    print(f"  │ Last Agent:    {state.last_agent or '—'}")
    print(f"  │ Addresses:     {len(state.addresses)} saved")
    print(f"  │ Address ID:    {state.selected_address_id or '—'}")
    print(f"  │ Restaurants:   {len(state.restaurants)} found")
    res_name = state.selected_restaurant.get("name") if state.selected_restaurant else None
    print(f"  │ Selected Rest: {res_name or '—'}")
    print(f"  │ Menu Items:    {len(state.menu_items)} loaded")
    print(f"  │ Cart ID:       {state.cart_id or '—'}")
    print(f"  │ Handoffs:      {len(state.handoff_history)}")
    for i, h in enumerate(state.handoff_history[-3:], 1):
        print(f"  │   {i}. {h['from']} → {h['to']}")
    print("  └────────────────────────────────────────────")


def _reset_state() -> None:
    """Reset the global state for a fresh session."""
    global _state
    _state = ZomatoState()
    # Re-load tokens so we don't need re-auth
    tokens = load_tokens()
    if tokens:
        _state.tokens = tokens
        _state.authenticated = True


if __name__ == "__main__":
    main()
