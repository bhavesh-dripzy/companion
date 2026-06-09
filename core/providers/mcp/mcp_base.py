"""`McpProvider` — the shared base for every MCP-backed provider.

Implements `discover_tools` and `run` once: open ONE session per turn, then for
each call clear stale placeholders, substitute, guard unresolved placeholders,
call the tool, parse the envelope, and learn new placeholders. A concrete plugin
(Phase 4) supplies only `stdio_spec` (command/args/env incl. tokens) and its
`build_attachments` / `merge` — roughly 200 lines instead of a 3,500-line executor.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from functools import cache
from pathlib import Path
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.session import ProviderSessionState
from core.contracts.tool import AuthMode, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ProviderResult, ToolResult
from core.middleware.errors import UnresolvedPlaceholderError
from core.providers.base import ExecCtx
from core.providers.mcp.address import extract_address_rows
from core.providers.mcp.connection import StdioSpec, call_tool, list_tools, mcp_session
from core.providers.mcp.placeholders import PlaceholderResolver
from core.providers.mcp.result_parse import tool_result_payload
from core.providers.runner import run_calls


@cache
def _load_planner_md(cls: type) -> str | None:
    """Read the `planner.md` preamble colocated with a provider plugin (cached)."""
    try:
        return (Path(inspect.getfile(cls)).parent / "planner.md").read_text(encoding="utf-8")
    except (OSError, TypeError):
        return None


class McpProvider(ABC):
    id: str = ""
    label: str = ""
    auth_mode: AuthMode = AuthMode.OAUTH
    session_cls: type[ProviderSessionState] | None = None
    resolver: PlaceholderResolver = PlaceholderResolver()
    # Address-taking providers (Zomato/Swiggy) set this to their "list saved
    # addresses" tool to enable __ADDRESS_ID__ auto-resolution in before_run.
    # Left None for providers that don't take a delivery address (Kite, Maps, …).
    saved_addresses_tool: str | None = None
    # The flat session_* key this provider's saved addresses round-trip under
    # (Instamart/Zepto namespace their own; food uses the shared one).
    saved_addresses_session_key: str = "session_addresses"

    @property
    def planner_preamble(self) -> str | None:
        """The provider's tuned planner preamble (prompt-as-data: `<plugin>/planner.md`).

        Drop a `planner.md` next to the plugin and the generated planner uses it — no
        wiring. Falls back to the generic preamble when absent."""
        return _load_planner_md(type(self))

    def planner_context(self, ctx: Any) -> str:
        """A per-provider session brief prepended to the planner input.

        Default: none. Plugins override to surface their session_* state (restaurants,
        cart, holdings, …) so the planner reuses cached ids instead of re-searching.
        Compose from the shared renderers in `core.providers.mcp.planner_context`."""
        return ""

    @abstractmethod
    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        """Build the stdio launch spec (mcp-remote command + token env) for this turn."""

    async def before_run(self, session: Any, ctx: ExecCtx) -> None:
        """Resolve `__ADDRESS_ID__` once, before the first call, for address-taking
        providers. Restores the legacy resolution order (planner.md): request
        address_id → carried `__ADDRESS_ID__` → `session_addresses[0]` → one-shot
        auto-fetch. Without it, every food turn emits an unresolved placeholder
        (the frontend sends `address_id:''`) and the user sees "I need your location".
        No-op for providers that leave ``saved_addresses_tool`` None."""
        if ctx.address_id and "__ADDRESS_ID__" not in ctx.placeholders:
            ctx.placeholders["__ADDRESS_ID__"] = ctx.address_id
        if not self.saved_addresses_tool or "__ADDRESS_ID__" in ctx.placeholders:
            return
        # Carried saved addresses from a prior turn (the frontend echoes them back).
        key = self.saved_addresses_session_key
        carried = ctx.session.get(key) if isinstance(ctx.session, dict) else None
        if isinstance(carried, list) and carried:
            aid = str((carried[0] or {}).get("address_id") or "").strip()
            if aid:
                ctx.placeholders["__ADDRESS_ID__"] = aid
                return
        # One-shot auto-fetch. Best-effort: any failure leaves __ADDRESS_ID__
        # unresolved (same as before), so this can never make a turn worse.
        # extract_address_rows is dual-key + `id`-aware (Instamart rows key on
        # `id`, which resolver.learn would miss) and gives the first SAVED address.
        try:
            rows = extract_address_rows(tool_result_payload(await call_tool(
                session, self.saved_addresses_tool, {}
            )))
        except Exception:
            return
        if rows:
            aid = str(rows[0].get("address_id") or "").strip()
            if aid:
                ctx.placeholders["__ADDRESS_ID__"] = aid

    async def discover_tools(self) -> list[Tool]:
        async with mcp_session(self.stdio_spec(ExecCtx())) as session:
            raw = await list_tools(session)
        return [
            Tool(
                name=item["name"],
                provider=self.id,
                description=item.get("description", ""),
                params=item.get("input_schema") or {},
                auth_mode=self.auth_mode,
                source="mcp",
            )
            for item in raw
        ]

    async def run(self, calls: list[ToolCall], ctx: ExecCtx) -> ProviderResult:
        async with mcp_session(self.stdio_spec(ctx)) as session:
            await self.before_run(session, ctx)

            async def execute_one(call: ToolCall) -> ToolResult:
                self.resolver.clear_stale(ctx.placeholders, call.tool)
                args = self.resolver.substitute(dict(call.arguments), ctx.placeholders)
                unresolved = self.resolver.unresolved(args)
                if unresolved:
                    raise UnresolvedPlaceholderError(
                        f"unresolved placeholders: {', '.join(unresolved)}"
                    )
                raw = await call_tool(session, call.tool, args)
                envelope = tool_result_payload(raw)
                learned = self.resolver.learn(ctx.placeholders, envelope)
                return ToolResult(
                    tool=call.tool,
                    provider=self.id,
                    ok=not bool(envelope.get("is_error")),
                    arguments=args,
                    data=envelope,
                    learned_placeholders=learned,
                )

            return await run_calls(execute_one, calls=calls, provider_id=self.id, ctx=ctx)

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        return []

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        return slice_
