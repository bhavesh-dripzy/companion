"""Strangler adapter: run an MCP provider via its proven legacy executor.

During migration each MCP provider plugs in here, reusing the battle-tested
`run_*_tool_plan_sync` executor + attachment collectors unchanged — so parity is
automatic (no re-port, no fixtures needed yet). The executor/collector internals
move into a clean `McpProvider` subclass per provider at cutover (Phase 8), once
live fixtures exist. Tools come from the seed catalog meanwhile.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode, ProviderResult, ToolResult
from core.providers.base import ExecCtx


def _resolve(path: str) -> Callable[..., Any]:
    module_name, attr = path.split(":")
    return getattr(importlib.import_module(module_name), attr)


# session_* kwargs accepted by the legacy `_format_routing_message` context builder.
_FORMAT_SESSION_KEYS = frozenset(
    {
        "session_restaurants",
        "session_addresses",
        "session_menu",
        "session_movies",
        "session_theatres",
        "session_events",
        "session_venues",
        "session_artists",
        "session_reservation",
        "session_zepto_products",
        "session_zepto_addresses",
        "session_zepto_cart",
        "session_instamart_products",
        "session_instamart_addresses",
        "session_instamart_cart",
        "session_kite_instruments",
        "session_kite_holdings",
        "session_kite_positions",
        "session_kite_open_orders",
        "session_kite_order_ticket",
    }
)


@dataclass
class LegacyMcpConfig:
    id: str
    label: str
    auth_mode: AuthMode
    run_fn_path: str  # "module:attr" of the legacy run_*_tool_plan_sync
    url_env: str | None = None
    default_url: str | None = None
    pass_address: bool = True  # executor takes address_id
    pass_location: bool = True  # executor takes location_text + user_geo
    # legacy session_* kwarg -> session-slice key (read in, written back from diagnostics)
    session_map: dict[str, str] = field(default_factory=dict)
    collector_paths: tuple[str, ...] = ()  # "module:attr" of legacy attachment collectors
    run_fn: Callable[..., Any] | None = None  # test seam; overrides run_fn_path


class LegacyMcpAdapter:
    def __init__(self, config: LegacyMcpConfig) -> None:
        self.config = config
        self.id = config.id
        self.label = config.label
        self.auth_mode = config.auth_mode
        self.session_cls = None

    async def discover_tools(self) -> list[Tool]:
        return []  # tools come from the seed catalog during migration

    async def run(self, calls: list[ToolCall], ctx: ExecCtx) -> ProviderResult:
        tool_plan = [{"tool": c.tool, "arguments": dict(c.arguments)} for c in calls]
        raw = await asyncio.to_thread(self._invoke, tool_plan, ctx)
        ok = bool(raw.get("ok"))
        results = [
            self._to_result(step) for step in raw.get("steps") or [] if isinstance(step, dict)
        ]
        return ProviderResult(
            provider=self.id,
            results=results,
            ok=ok,
            diagnostics=raw,
            error=None if ok else ErrorCode.UPSTREAM_ERROR,
            error_detail=raw.get("error_detail"),
        )

    def _to_result(self, step: dict[str, Any]) -> ToolResult:
        envelope = step.get("result") if isinstance(step.get("result"), dict) else {}
        is_error = bool(envelope.get("is_error"))
        return ToolResult(
            tool=str(step.get("tool") or ""),
            provider=self.id,
            ok=not is_error,
            arguments=step.get("arguments") if isinstance(step.get("arguments"), dict) else {},
            data=envelope,
            error=ErrorCode.UPSTREAM_ERROR if is_error else None,
        )

    def _invoke(self, tool_plan: list[dict[str, Any]], ctx: ExecCtx) -> dict[str, Any]:
        cfg = self.config
        run_fn = cfg.run_fn or _resolve(cfg.run_fn_path)
        url = os.getenv(cfg.url_env, cfg.default_url) if cfg.url_env else cfg.default_url
        kwargs: dict[str, Any] = {
            "mcp_url": url,
            "user_id": ctx.user_id,
            "session_placeholders": dict(ctx.placeholders),
        }
        if cfg.pass_address:
            kwargs["address_id"] = ctx.address_id
        if cfg.pass_location:
            kwargs.update(location_text=None, user_geo=ctx.user_geo)
        # ctx.session is the flat legacy bag, keyed by the executor's session_* names.
        for legacy_kwarg in cfg.session_map:
            kwargs[legacy_kwarg] = (ctx.session or {}).get(legacy_kwarg)
        out = run_fn(tool_plan, **kwargs)
        return (
            out
            if isinstance(out, dict)
            else {"ok": False, "steps": [], "error_detail": "bad executor return"}
        )

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        raw_tool_results = {self.id: result.diagnostics}
        cards: list[Attachment] = []
        for path in self.config.collector_paths:
            for card in _resolve(path)(raw_tool_results) or []:
                if isinstance(card, dict):
                    cards.append(
                        Attachment(**card) if "kind" in card else Attachment(kind="card", **card)
                    )
        return cards

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        """Write the executor's new session_* (from diagnostics) into the flat bag."""
        raw = result.diagnostics or {}
        inner = raw.get("diagnostics") if isinstance(raw.get("diagnostics"), dict) else {}
        merged = dict(slice_)
        for legacy_kwarg in self.config.session_map:
            value = inner.get(legacy_kwarg)
            value = value if value is not None else raw.get(legacy_kwarg)
            if value is not None:
                merged[legacy_kwarg] = value
        return merged

    async def plan(self, ctx: Any) -> Any:
        """Delegate planning to the provider's tuned legacy planner + context builder.

        Reuses `_format_routing_message` (session context) and `_plan_for_provider`
        (the provider's domain agent) so plan quality matches the legacy exactly.
        Returns a ToolPlan normalized against the registry, or None to fall back.
        """
        raw = await asyncio.to_thread(self._plan_sync, ctx)
        if not isinstance(raw, dict):
            return None
        from core.middleware.validator import normalize_plan

        plan, _dropped = normalize_plan(raw, provider=self.id, tools=ctx.deps.registry.tools)
        return plan

    def _plan_sync(self, ctx: Any) -> dict[str, Any] | None:
        from app.services.agents.pipeline import _format_routing_message, _plan_for_provider

        session = ctx.session
        profile = ctx.profile.model_dump() if ctx.profile else None
        bag = {k: v for k, v in (session.legacy or {}).items() if k in _FORMAT_SESSION_KEYS}
        routing_message = _format_routing_message(
            ctx.request.message,
            ctx.request.history,
            session_state_keys=list(session.placeholders.keys()),
            selected_address_id=session.selected_address_id,
            pending_variant_choice=session.pending_variant_choice or None,
            user_profile=profile,
            **bag,
        )
        return _plan_for_provider(
            self.id, routing_message=routing_message, llm=ctx.llm, user_id=ctx.request.user_id or ""
        )
