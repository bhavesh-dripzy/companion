"""ProviderAdapter — base for the only provider-specific code.

Wraps the kept MCP transport (`core.providers.mcp.connection` + `remote`) and declares a
provider's capabilities + cart strategy. Concrete adapters implement `search`, `write_cart`,
`read_cart` (+ parsers) against their MCP tool shapes; everything above them is generic.
Capabilities are simple string flags so the DomainHandler can branch + fall back.
"""

from __future__ import annotations

import contextlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from core.agent.contracts import Cart, CartResult, Product
from core.agent.errors import classify
from core.providers.mcp import connection_cache
from core.providers.mcp.connection import call_tool, mcp_session
from core.providers.mcp.remote import hydrate_tokens, repo_root, stdio_spec
from core.providers.mcp.token_store import has_usable_tokens

# Capability flags
SEARCHABLE = "searchable"
BATCH_SEARCHABLE = "batch_searchable"
CARTABLE = "cartable"
SERVICEABLE = "serviceable"  # read-only serviceability check
CHECKOUTABLE = "checkoutable"


@dataclass
class ExecCtx:
    """Per-call execution context for a provider (the bits its tools need)."""

    user_id: str
    address_id: str | None = None
    device_id: str | None = None
    store_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def mcp_raw(result: Any) -> dict[str, Any]:
    """CallToolResult → {is_error, structured, text}."""
    text = ""
    for b in getattr(result, "content", []) or []:
        text += getattr(b, "text", "") or ""
    return {
        "is_error": bool(getattr(result, "isError", False)),
        "structured": getattr(result, "structuredContent", None),
        "text": text,
    }


class ProviderAdapter:
    """Subclass and set the class attrs + implement the async methods."""

    id: str = ""
    domains: tuple[str, ...] = ()
    mcp_url: str = ""
    capabilities: frozenset[str] = frozenset()
    cart_strategy: str = "replace_all_fragile"
    free_delivery_minor: int = 0  # item-total to unlock free delivery / drop the small-cart fee

    def has(self, cap: str) -> bool:
        return cap in self.capabilities

    def is_connected(self, user_id: str) -> bool:
        """Non-interactive: does this user have usable MCP tokens for this provider?

        Restores any vault-stored tokens to disk (no browser / OAuth — that only happens
        when an MCP *session* opens), then checks the file. Used to gate the search/cart
        fan-out so a disconnected app is SKIPPED, never auth-prompted.

        Memoized in `connection_cache` (TTL + invalidated on connect/disconnect) so a hot
        path doesn't re-read the token vault every turn; on a miss we do the real check."""
        cached = connection_cache.get(user_id, self.mcp_url)
        if cached is not None:
            return cached
        root = repo_root()
        with contextlib.suppress(Exception):
            hydrate_tokens(self.mcp_url, user_id=user_id, root=root)
        connected = has_usable_tokens(root=root, mcp_url=self.mcp_url, user_id=user_id)
        connection_cache.set(user_id, self.mcp_url, connected)
        return connected

    def classify(self, message: str):
        return classify(self.id, message)

    @asynccontextmanager
    async def open_session(self, user_id: str):
        """Open one MCP session (tokens hydrated by `stdio_spec`); reuse for the turn."""
        spec = stdio_spec(self.mcp_url, user_id=user_id, root=repo_root())
        async with mcp_session(spec) as session:
            yield session

    async def _call(self, session: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return mcp_raw(await call_tool(session, tool, args))

    async def ensure_context(self, session: Any, ctx: ExecCtx) -> None:
        """Stateful providers (Zepto: select store; Food: restaurant) set up here. Default no-op."""
        return None

    async def serviceable(self, session: Any, ctx: ExecCtx) -> tuple[bool, str]:
        """Read-only serviceability when `SERVICEABLE`. Returns (ok, message). Default: unknown→ok."""
        return True, ""

    async def default_context(self, session: Any, user_id: str) -> dict[str, Any]:
        """Resolve a usable default delivery context (address/geo/device) for this provider.
        Used to seed `provider_session` on first use. Returns {} if none."""
        return {}

    async def list_addresses(self, session: Any) -> list[dict[str, Any]]:
        """The user's saved delivery addresses as [{id, label, lat, lng}] (for switch_context).
        Default: none."""
        return []

    # ----- to implement per provider -----
    async def search(self, session: Any, query: str, ctx: ExecCtx) -> list[Product]:
        raise NotImplementedError

    async def write_cart(
        self, session: Any, items: list[tuple[str, int, str]], ctx: ExecCtx, *, replace: bool
    ) -> CartResult:
        """`items` = [(item_id, qty, secondary_id)]. Build provider args, call MCP, parse+classify."""
        raise NotImplementedError

    async def read_cart(self, session: Any, ctx: ExecCtx) -> CartResult:
        raise NotImplementedError

    async def clear(self, session: Any, ctx: ExecCtx) -> None:
        """Empty the cart (for replace-all providers that reject an empty items array). Default no-op."""
        return None

    async def price_cart(self, session: Any, ctx: ExecCtx, cart: Cart) -> None:
        """Fill the cart's final total + bill from the provider's checkout-pricing step. Default
        no-op (Instamart's get_cart already carries the bill). Zepto fetches it via a separate call
        and may set `cart.unavailable` when the store is closed / not serviceable."""
        return None

    def parse_cart(self, raw: dict[str, Any]) -> Cart:
        raise NotImplementedError
