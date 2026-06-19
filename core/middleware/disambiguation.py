"""Collapse a same-capability-class WRITE onto a single provider.

Grocery discovery fans out to both Instamart and Zepto so prices can be compared,
but a cart mutation must land on exactly one. Once any provider in a capability
class plans a mutating call this turn, keep only the winner's mutating calls — the
provider with an active cart, else the first routed (the orchestrator's seed) — and
drop the rest. Reads are left fanned out. Keyed on the declared `capability_class`
and `cart_session_key` plus the `Tool.mutating` flag, so any same-class provider
pair (two grocery, two food) inherits the behaviour with no per-pair code.
"""

from __future__ import annotations

from typing import Any

from core.contracts.tool_call import ToolCall, ToolPlan
from core.registry.provider_registry import ProviderRegistry


def _mutating(registry: ProviderRegistry, provider_id: str, call: ToolCall) -> bool:
    tool = registry.tools.get(call.tool, provider=provider_id)
    return bool(tool and tool.mutating)


def _cart_has_items(legacy: dict[str, Any], key: str) -> bool:
    cart = legacy.get(key) if key else None
    return isinstance(cart, dict) and bool(cart.get("items"))


def _winner(registry: ProviderRegistry, legacy: dict[str, Any], provider_ids: list[str]) -> str:
    """The provider a same-class write belongs to: active-cart holder, else seed."""
    for pid in provider_ids:
        key = getattr(registry.get(pid), "cart_session_key", "") or ""
        if _cart_has_items(legacy, key):
            return pid
    return provider_ids[0]


def collapse_same_class_writes(
    plans: dict[str, ToolPlan], registry: ProviderRegistry, legacy: dict[str, Any]
) -> list[dict[str, Any]]:
    """Drop losing providers' mutating calls in place; return the collapse events."""
    by_class: dict[str, list[str]] = {}
    for pid in plans:
        cls = getattr(registry.get(pid), "capability_class", "") or ""
        if cls:
            by_class.setdefault(cls, []).append(pid)
    events: list[dict[str, Any]] = []
    for cls, pids in by_class.items():
        if len(pids) < 2:
            continue
        if not any(_mutating(registry, pid, c) for pid in pids for c in plans[pid].calls):
            continue
        winner = _winner(registry, legacy, pids)
        dropped: list[str] = []
        for pid in pids:
            if pid == winner:
                continue
            plan = plans[pid]
            keep = [c for c in plan.calls if not _mutating(registry, pid, c)]
            if len(keep) != len(plan.calls):
                dropped.append(pid)
                plans[pid] = plan.model_copy(update={"calls": keep})
        if dropped:
            events.append({"capability": cls, "kept": winner, "dropped": dropped})
    return events
