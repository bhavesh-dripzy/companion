"""`McpProvider` — the shared base for every MCP-backed provider.

Implements `discover_tools` and `run` once: open ONE session per turn, then for
each call clear stale placeholders, substitute, guard unresolved placeholders,
call the tool, parse the envelope, and learn new placeholders. A concrete plugin
(Phase 4) supplies only `stdio_spec` (command/args/env incl. tokens) and its
`build_attachments` / `merge` — roughly 200 lines instead of a 3,500-line executor.
"""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from functools import cache
from pathlib import Path
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.session import ProviderSessionState
from core.contracts.tool import AuthMode, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode, ProviderResult, ToolResult
from core.middleware.errors import ToolTimeoutError, UnresolvedPlaceholderError
from core.middleware.repair import (
    backoff_delay,
    classify_envelope,
    error_text,
    repair_arguments,
)
from core.providers.base import ExecCtx
from core.providers.mcp.address import extract_address_rows
from core.providers.mcp.connection import StdioSpec, call_tool, list_tools, mcp_session
from core.providers.mcp.digest import lean_digest
from core.providers.mcp.nearest import pick_address_id
from core.providers.mcp.placeholders import PlaceholderResolver
from core.providers.mcp.planner_context import rows_for_provider
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
    saved_addresses_tool: str | None = None
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

    def normalize_args(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Hook: repair provider-specific argument shapes the planner gets wrong
        (e.g. re-nest filter keys). Default: pass through unchanged."""
        return args

    def before_call(self, tool: str, args: dict[str, Any], ctx: ExecCtx) -> dict[str, Any] | None:
        """Hook: gate a call after substitution/normalization. Returning an
        envelope dict SKIPS the MCP call and uses it as the result (e.g. defer
        a cart add that needs a variant choice the user hasn't made)."""
        return None

    def harvest(self, tool: str, envelope: dict[str, Any], ctx: ExecCtx) -> None:  # noqa: B027 — optional hook, intentionally a no-op
        """Hook: accumulate per-turn state from a call's envelope into
        `ctx.scratch` for later calls in the same plan (e.g. a menu index)."""

    async def before_run(self, session: Any, ctx: ExecCtx) -> None:
        """Resolve __ADDRESS_ID__ before the first call (address-taking providers).

        Order: request address_id, then carried session addresses, then a
        one-shot fetch via ``saved_addresses_tool``. Both lookups pick the
        saved address nearest ``ctx.user_geo`` (#18), falling back to the
        first row when no geo/coords exist. No-op when the attr is None."""
        if ctx.address_id and "__ADDRESS_ID__" not in ctx.placeholders:
            ctx.placeholders["__ADDRESS_ID__"] = ctx.address_id
        if not self.saved_addresses_tool or "__ADDRESS_ID__" in ctx.placeholders:
            return
        key = self.saved_addresses_session_key
        carried = ctx.session.get(key) if isinstance(ctx.session, dict) else None
        aid = pick_address_id(rows_for_provider(carried, self.id), ctx.user_geo)
        if aid:
            ctx.placeholders["__ADDRESS_ID__"] = aid
            return
        try:
            raw = await call_tool(session, self.saved_addresses_tool, {})
            rows = extract_address_rows(tool_result_payload(raw))
        except Exception:
            return
        aid = pick_address_id(rows, ctx.user_geo)
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

    async def _call_with_timeout_retry(
        self, session: Any, tool: str, args: dict[str, Any], ctx: ExecCtx
    ) -> Any:
        """Tier-1 transient retry: one re-send after a timeout, read-only tools only
        (a timed-out mutation may have landed server-side — never re-send those)."""
        try:
            return await call_tool(session, tool, args)
        except TimeoutError as exc:
            if tool not in ctx.idempotent_tools:
                raise ToolTimeoutError(f"{tool} timed out") from exc
            await asyncio.sleep(backoff_delay(1))
            try:
                return await call_tool(session, tool, args)
            except TimeoutError as exc2:
                raise ToolTimeoutError(f"{tool} timed out after retry") from exc2

    async def _try_repair(
        self, session: Any, call: ToolCall, args: dict[str, Any], envelope: dict, ctx: ExecCtx
    ) -> tuple[dict[str, Any], dict]:
        """Tier-2: one LLM pass to fix server-rejected arguments, budget-gated."""
        if not (ctx.complete_json and ctx.llm and ctx.repair_budget.take()):
            return args, envelope
        repaired = await repair_arguments(
            tool=call.tool,
            schema=ctx.tool_schemas.get(call.tool),
            failed_args=args,
            server_error=error_text(envelope),
            intent=ctx.intent_summary,
            llm=ctx.llm,
            complete_json=ctx.complete_json,
        )
        if repaired is None:
            return args, envelope
        new_args = self.normalize_args(
            call.tool, self.resolver.substitute(repaired, ctx.placeholders)
        )
        if self.resolver.unresolved(new_args) or new_args == args:
            return args, envelope
        raw = await self._call_with_timeout_retry(session, call.tool, new_args, ctx)
        return new_args, tool_result_payload(raw)

    async def run(self, calls: list[ToolCall], ctx: ExecCtx) -> ProviderResult:
        async with mcp_session(self.stdio_spec(ctx)) as session:
            await self.before_run(session, ctx)

            async def execute_one(call: ToolCall) -> ToolResult:
                self.resolver.clear_stale(ctx.placeholders, call.tool)
                self.resolver.learn_from_args(ctx.placeholders, call.tool, call.arguments)
                args = self.resolver.substitute(dict(call.arguments), ctx.placeholders)
                args = self.normalize_args(call.tool, args)
                unresolved = self.resolver.unresolved(args)
                if unresolved:
                    raise UnresolvedPlaceholderError(
                        f"unresolved placeholders: {', '.join(unresolved)}"
                    )
                error: ErrorCode | None = None
                detail: str | None = None
                envelope = self.before_call(call.tool, args, ctx)
                if envelope is None:
                    raw = await self._call_with_timeout_retry(session, call.tool, args, ctx)
                    envelope = tool_result_payload(raw)
                    if envelope.get("is_error"):
                        if classify_envelope(envelope) is ErrorCode.INVALID_ARGS:
                            args, envelope = await self._try_repair(
                                session, call, args, envelope, ctx
                            )
                        if envelope.get("is_error"):
                            error = classify_envelope(envelope)
                            detail = error_text(envelope) or None
                learned = self.resolver.learn(ctx.placeholders, envelope)
                self.harvest(call.tool, envelope, ctx)
                return ToolResult(
                    tool=call.tool,
                    provider=self.id,
                    ok=error is None,
                    arguments=args,
                    data=envelope,
                    error=error,
                    error_detail=detail,
                    # Transient placeholders chain in-plan via ctx.placeholders but
                    # never persist (outlet-specific ids go stale across turns).
                    learned_placeholders={
                        k: v for k, v in learned.items() if k not in self.resolver.transient
                    },
                )

            return await run_calls(execute_one, calls=calls, provider_id=self.id, ctx=ctx)

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        return []

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        return slice_

    def summarize_result(self, tool_result: ToolResult) -> str | None:
        """Hook: a compact, complete digest of one tool result for the narrator.

        Large envelopes (a single Zomato search row is ~5KB of image URLs) are
        useless under the narrator's raw-JSON truncation — whatever ranks first
        becomes "the answer" and the rest reads as "not found". Plugins override
        for their high-traffic envelopes; the default lean digest covers every
        other tool (orders, tracking, coupons, slots, quotes…) generically."""
        data = tool_result.data if isinstance(tool_result.data, dict) else None
        return lean_digest(data) if data else None
