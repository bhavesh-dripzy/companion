"""The unified tool schema — one model for MCP-discovered and HTTP-declared tools.

This single source of truth replaces the markdown tool lists, the `_ALLOWED_TOOLS`
frozenset, and the per-provider tool sets scattered across the legacy code. The
registry derives the planner prompt, dispatch, and validation from `Tool` (ADR-0002).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AuthMode(str, Enum):
    """How a provider authenticates."""

    OAUTH = "oauth"  # mcp-remote OAuth handshake (zomato, swiggy, zepto, district, instamart)
    URL = "url"  # secret embedded in the MCP URL (zapier-style custom servers)
    TOOL = "tool"  # provider-side login tool, e.g. kite `login`
    NONE = "none"  # static credentials in Secrets Manager (twilio, uber, maps, calendar)


class ConfirmPolicy(str, Enum):
    """Whether a tool needs explicit user confirmation before it runs."""

    NEVER = "never"
    SINGLE = "single"  # one explicit user "yes" (most cart mutations / checkouts)
    TWO_STEP = "two_step"  # real-money confirmation, e.g. Kite order placement


class Tool(BaseModel):
    """A single callable tool, provider-agnostic.

    `params` is a JSON-Schema (draft-07) object describing the arguments. For MCP
    tools it is populated from the server's `inputSchema` at discovery time; for
    HTTP tools it is declared in the plugin. An empty dict means "unconstrained".
    """

    model_config = ConfigDict(frozen=True)

    name: str
    provider: str
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    auth_mode: AuthMode = AuthMode.OAUTH
    confirm: ConfirmPolicy = ConfirmPolicy.NEVER
    mutating: bool = False
    # Namespaced session keys this tool reads (e.g. "zomato.cart").
    session_reads: tuple[str, ...] = ()
    # Placeholders this tool's result can supply downstream (e.g. "__CART_ID__").
    placeholder_outputs: tuple[str, ...] = ()
    # Alternative names that normalize to this tool (legacy `_TOOL_ALIASES`).
    aliases: tuple[str, ...] = ()
    source: Literal["mcp", "static"] = "mcp"

    def matches(self, name: str) -> bool:
        """True if `name` is this tool's canonical name or one of its aliases."""
        return name == self.name or name in self.aliases
