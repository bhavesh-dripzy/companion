"""Normalized results of tool execution.

Every provider executor returns `ToolResult`s, replacing the loose
`{ok, steps, diagnostics, error_detail}` dicts. One provider's failure becomes a
typed result, never an exception that aborts the others (ADR-0003).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    """The error taxonomy surfaced by the middleware."""

    AUTH_REQUIRED = "auth_required"  # OAuth/login needed (e.g. mcp-remote has no tokens)
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_ARGS = "invalid_args"  # failed schema validation
    UNRESOLVED_PLACEHOLDER = "unresolved_placeholder"  # __CART_ID__ never supplied
    NEEDS_LOCATION = "needs_location"  # provider needs GPS coords, none available
    NOT_SERVICEABLE = "not_serviceable"  # address not in delivery zone / item out of stock
    TOOL_TIMEOUT = "tool_timeout"
    UPSTREAM_ERROR = "upstream_error"  # provider returned an error envelope
    CONFIRMATION_REQUIRED = "confirmation_required"
    UNKNOWN = "unknown"


class Effect(str, Enum):
    """Whether a mutating tool actually changed state — the narrator's ground truth.

    None on a read (no claim to make); set only on tools flagged `Tool.mutating`.
    """

    APPLIED = "applied"
    NOT_APPLIED = "not_applied"


class ToolResult(BaseModel):
    """The outcome of a single tool call."""

    tool: str
    provider: str
    ok: bool = True
    arguments: dict[str, Any] = Field(default_factory=dict)
    data: Any = None  # the parsed provider envelope (restaurants, menu, cart, ...)
    error: ErrorCode | None = None
    error_detail: str | None = None
    effect: Effect | None = None  # set on mutating tools: did the write actually apply?
    # Placeholders this call resolved for downstream steps (e.g. {"__CART_ID__": "abc"}).
    learned_placeholders: dict[str, str] = Field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return not self.ok


class ProviderResult(BaseModel):
    """All results from one provider in a turn, plus its session diagnostics."""

    provider: str
    ok: bool = True
    results: list[ToolResult] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    error: ErrorCode | None = None
    error_detail: str | None = None

    @property
    def failed(self) -> bool:
        return not self.ok

    @property
    def ran_any(self) -> bool:
        return bool(self.results)
