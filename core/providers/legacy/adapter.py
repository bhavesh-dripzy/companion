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
        for legacy_kwarg, slice_key in cfg.session_map.items():
            kwargs[legacy_kwarg] = (ctx.session or {}).get(slice_key)
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
        diagnostics = result.diagnostics or {}
        merged = dict(slice_)
        for legacy_kwarg, slice_key in self.config.session_map.items():
            value = (
                diagnostics.get("diagnostics", {}).get(legacy_kwarg)
                if isinstance(diagnostics.get("diagnostics"), dict)
                else None
            )
            value = value if value is not None else diagnostics.get(legacy_kwarg)
            if value is not None:
                merged[slice_key] = value
        return merged
