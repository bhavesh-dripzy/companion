#!/usr/bin/env python3
import json
import os
import sys
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import requests
except ImportError:
    print("Installing requests...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "uv", "add", "requests"])
    import requests

try:
    from openai import OpenAI
except ImportError:
    print("Installing openai...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "uv", "add", "openai"])
    from openai import OpenAI


BASE_URL = "https://mcp-server.zomato.com/mcp"

SESSION_FILE = os.path.expanduser("~/.zomato_mcp_session.json")


def save_session(session_data: dict):
    with open(SESSION_FILE, "w") as f:
        json.dump(session_data, f)


def load_session() -> dict | None:
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    return None


def clear_session():
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)


def make_mcp_request(tool_name: str, arguments: dict[str, Any], session: dict | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if session and "access_token" in session:
        headers["Authorization"] = f"Bearer {session['access_token']}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }

    try:
        response = requests.post(BASE_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def bind_user_number(phone_number: str) -> dict[str, Any]:
    return make_mcp_request("bind_user_number", {"phone_number": phone_number})


def bind_user_number_verify_code(phone_number: str, request_id: str, code: str) -> dict[str, Any]:
    return make_mcp_request(
        "bind_user_number_verify_code",
        {"phone_number": phone_number, "request_id": request_id, "code": code}
    )


def get_all_restaurants(user_location: str, page_size: int = 10, filters: list | None = None) -> dict[str, Any]:
    return make_mcp_request("get_all_restaurants", {
        "user_location": user_location,
        "page_size": page_size,
        "filters": filters or []
    })


def get_restaurants_for_keyword(user_location: str, keyword: str, page_size: int = 10, filters: list | None = None) -> dict[str, Any]:
    return make_mcp_request("get_restaurants_for_keyword", {
        "user_location": user_location,
        "keyword": keyword,
        "page_size": page_size,
        "filters": filters or []
    })


def get_restaurant_menu_by_category(res_id: str, category: str, location: str | None = None) -> dict[str, Any]:
    args = {"res_id": res_id, "category": category}
    if location:
        args["location"] = location
    return make_mcp_request("get_restaurant_menu_by_category", args)


def get_menu_items_listing(res_id: str, location: str | None = None) -> dict[str, Any]:
    args = {"res_id": res_id}
    if location:
        args["location"] = location
    return make_mcp_request("get_menu_items_listing", args)


def get_saved_addresses_for_user() -> dict[str, Any]:
    return make_mcp_request("get_saved_addresses_for_user", {})


def create_cart(res_id: str, items: list[dict[str, Any]], precise_location: str, promo_code: str | None = None) -> dict[str, Any]:
    args = {"res_id": res_id, "items": items, "precise_location": precise_location}
    if promo_code:
        args["promo_code"] = promo_code
    return make_mcp_request("create_cart", args)


def checkout_cart(cart_id: str | None = None) -> dict[str, Any]:
    args = {}
    if cart_id:
        args["cart_id"] = cart_id
    return make_mcp_request("checkout_cart", args)


def get_search_order_history(location: str, search_query: str | None = None) -> dict[str, Any]:
    args = {"location": location}
    if search_query:
        args["search_query"] = search_query
    return make_mcp_request("get_search_order_history", args)


def get_order_tracking_info() -> dict[str, Any]:
    return make_mcp_request("get_order_tracking_info", {})


def get_cart_offers(cart_id: str, location: str) -> dict[str, Any]:
    return make_mcp_request("get_cart_offers", {"cart_id": cart_id, "location": location})


TOOLS = [
    {"type": "function", "function": {"name": "get_all_restaurants", "description": "Find nearby restaurants based on location and preferences", "parameters": {"type": "object", "properties": {"user_location": {"type": "string", "description": "User location (address, area, lat/long)"}, "page_size": {"type": "integer", "description": "Number of results to return"}, "filters": {"type": "array", "description": "Dynamic filters from get_dynamic_search_filters"}}, "required": ["user_location"]}}},
    {"type": "function", "function": {"name": "get_restaurants_for_keyword", "description": "Search restaurants by name, cuisine, dish, or filters", "parameters": {"type": "object", "properties": {"user_location": {"type": "string", "description": "User location"}, "keyword": {"type": "string", "description": "Search keyword (restaurant name, cuisine, dish)"}, "page_size": {"type": "integer", "description": "Number of results"}, "filters": {"type": "array", "description": "Search filters"}}, "required": ["user_location", "keyword"]}}},
    {"type": "function", "function": {"name": "get_restaurant_menu_by_category", "description": "Get restaurant menu for a specific category", "parameters": {"type": "object", "properties": {"res_id": {"type": "string", "description": "Restaurant ID"}, "category": {"type": "string", "description": "Menu category name"}, "location": {"type": "string", "description": "User location for delivery"}}, "required": ["res_id", "category"]}}},
    {"type": "function", "function": {"name": "get_menu_items_listing", "description": "Get compact list of dishes mapped to categories", "parameters": {"type": "object", "properties": {"res_id": {"type": "string", "description": "Restaurant ID"}, "location": {"type": "string", "description": "User location"}}, "required": ["res_id"]}}},
    {"type": "function", "function": {"name": "get_saved_addresses_for_user", "description": "Get all saved delivery addresses", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "bind_user_number", "description": "Start phone number binding - sends OTP", "parameters": {"type": "object", "properties": {"phone_number": {"type": "string", "description": "Phone number with country code (e.g., 919876543210)"}}, "required": ["phone_number"]}}},
    {"type": "function", "function": {"name": "bind_user_number_verify_code", "description": "Verify OTP and complete authentication", "parameters": {"type": "object", "properties": {"phone_number": {"type": "string", "description": "Phone number"}, "request_id": {"type": "string", "description": "Request ID from bind_user_number response"}, "code": {"type": "string", "description": "OTP code received on phone"}}, "required": ["phone_number", "request_id", "code"]}}},
    {"type": "function", "function": {"name": "create_cart", "description": "Create a cart with items, variants, and add-ons", "parameters": {"type": "object", "properties": {"res_id": {"type": "string", "description": "Restaurant ID"}, "items": {"type": "array", "description": "Items with variant_id, quantity, add_ons"}, "precise_location": {"type": "string", "description": "Delivery location"}, "promo_code": {"type": "string", "description": "Optional promo code"}}, "required": ["res_id", "items", "precise_location"]}}},
    {"type": "function", "function": {"name": "checkout_cart", "description": "Place order for a cart (may return QR for payment)", "parameters": {"type": "object", "properties": {"cart_id": {"type": "string", "description": "Cart ID from create_cart"}}, "required": ["cart_id"]}}},
    {"type": "function", "function": {"name": "get_search_order_history", "description": "Get user's order history", "parameters": {"type": "object", "properties": {"location": {"type": "string", "description": "User location"}, "search_query": {"type": "string", "description": "Search within order history"}}, "required": ["location"]}}},
    {"type": "function", "function": {"name": "get_order_tracking_info", "description": "Track active orders", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_cart_offers", "description": "Get applicable promos for a cart", "parameters": {"type": "object", "properties": {"cart_id": {"type": "string", "description": "Cart ID"}, "location": {"type": "string", "description": "User location"}}, "required": ["cart_id", "location"]}}},
]


TOOL_FUNCTIONS = {
    "get_all_restaurants": get_all_restaurants,
    "get_restaurants_for_keyword": get_restaurants_for_keyword,
    "get_restaurant_menu_by_category": get_restaurant_menu_by_category,
    "get_menu_items_listing": get_menu_items_listing,
    "get_saved_addresses_for_user": get_saved_addresses_for_user,
    "bind_user_number": bind_user_number,
    "bind_user_number_verify_code": bind_user_number_verify_code,
    "create_cart": create_cart,
    "checkout_cart": checkout_cart,
    "get_search_order_history": get_search_order_history,
    "get_order_tracking_info": get_order_tracking_info,
    "get_cart_offers": get_cart_offers,
}


def execute_tool(tool_name: str, arguments: dict[str, Any], session: dict | None = None) -> str:
    if tool_name not in TOOL_FUNCTIONS:
        return f"Error: Unknown tool {tool_name}"
    try:
        result = TOOL_FUNCTIONS[tool_name](**arguments)
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error executing {tool_name}: {str(e)}"


class ZomatoChatbot:
    SYSTEM_PROMPT = """You are a helpful assistant for Zomato food ordering. You can help users with:
- Restaurant discovery: Search by location, cuisine, dish, or keyword
- Menu browsing: View restaurant menus and categories
- Cart creation: Add items with variants and add-ons
- Food ordering: Place orders and track them
- Order history: View past orders

IMPORTANT - Authentication:
- Users must authenticate via phone OTP before placing orders
- First call bind_user_number with their phone number
- Then call bind_user_number_verify_code with the OTP received
- Store the session for subsequent requests

Always ask for user's location/area when searching restaurants.
Confirm cart contents before checkout.
Payment is via QR code (returned by checkout_cart)."""

    def __init__(self, api_key: str | None = None):
        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": self.SYSTEM_PROMPT}]
        self.session = load_session()
        self.pending_auth: dict | None = None

    def chat(self, user_message: str) -> str:
        self.messages.append({"role": "user", "content": user_message})

        response = self.client.chat.completions.create(
            model="gpt-5.4-nano", messages=self.messages, tools=TOOLS, tool_choice="auto"
        )

        assistant_message = response.choices[0].message

        assistant_msg_dict: dict[str, Any] = {"role": "assistant"}
        if assistant_message.content:
            assistant_msg_dict["content"] = assistant_message.content
        if assistant_message.tool_calls:
            assistant_msg_dict["tool_calls"] = assistant_message.tool_calls
        self.messages.append(assistant_msg_dict)

        if assistant_message.tool_calls:
            tool_results = []
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                print(f"\n[Calling tool: {tool_name}]")

                if tool_name == "bind_user_number":
                    result = bind_user_number(**arguments)
                    if result.get("result", {}).get("request_id"):
                        self.pending_auth = {
                            "phone_number": arguments["phone_number"],
                            "request_id": result["result"]["request_id"]
                        }
                    tool_results.append({"tool_call_id": tool_call.id, "output": json.dumps(result, indent=2)})
                    print(f"[Result]: {result}")
                elif tool_name == "bind_user_number_verify_code":
                    if self.pending_auth:
                        arguments["request_id"] = self.pending_auth["request_id"]
                    result = bind_user_number_verify_code(**arguments)
                    if result.get("result", {}).get("status") == "success":
                        self.session = {"access_token": result["result"].get("access_token")}
                        save_session(self.session)
                    tool_results.append({"tool_call_id": tool_call.id, "output": json.dumps(result, indent=2)})
                    print(f"[Result]: {result}")
                else:
                    result = execute_tool(tool_name, arguments, self.session)
                    tool_results.append({"tool_call_id": tool_call.id, "output": result})
                    print(f"[Result]: {result[:500]}..." if len(result) > 500 else f"[Result]: {result}")

            for tr in tool_results:
                self.messages.append({"role": "tool", "tool_call_id": tr["tool_call_id"], "content": tr["output"]})

            response = self.client.chat.completions.create(
                model="gpt-5.4-nano", messages=self.messages
            )
            assistant_message = response.choices[0].message
            final_msg_dict: dict[str, Any] = {"role": "assistant"}
            if assistant_message.content:
                final_msg_dict["content"] = assistant_message.content
            self.messages.append(final_msg_dict)

        return assistant_message.content or "No response"


def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set.")
        print("Please set your key in .env file or export OPENAI_API_KEY")
        sys.exit(1)

    print("=" * 60)
    print("Zomato MCP Chatbot")
    print("=" * 60)
    print("I can help you with:")
    print("  - Restaurant search and discovery")
    print("  - Menu browsing")
    print("  - Cart creation and ordering")
    print("  - Order tracking")
    print("\nType 'quit' or 'exit' to end the conversation.")
    print("=" * 60)

    if load_session():
        print("\n[Session found - previous auth detected]")

    bot = ZomatoChatbot(api_key=api_key)

    while True:
        try:
            user_input = input("\nYou: ").strip()
            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break
            if not user_input:
                continue

            response = bot.chat(user_input)
            print(f"\nAssistant: {response}")
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


if __name__ == "__main__":
    main()