"""Shared planner-brief helpers for provider-scoped session state.

Session lists (`session_restaurants`, `session_menu`, …) are shared across the
provider fan-out but their ids are platform-specific: a planner that sees
another platform's rows will paste foreign ids into its tools and every call
fails ("restaurant ID not valid"). Plugins build their brief from the helpers
here so each planner sees ONLY its own slice.
"""

from __future__ import annotations

from typing import Any

from core.providers.mcp.placeholders import provider_view


def rows_for_provider(value: Any, provider_id: str) -> list[dict[str, Any]]:
    """Filter a session list to this provider's rows.

    Untagged rows (sessions persisted before provider scoping) are included so
    mid-flight conversations keep working; other providers' rows are dropped."""
    if not isinstance(value, list):
        return []
    return [
        row
        for row in value
        if isinstance(row, dict) and str(row.get("provider") or provider_id) == provider_id
    ]


def placeholders_block(placeholders: Any, provider_id: str) -> str | None:
    """Render the carried-forward placeholder keys visible to this provider."""
    src = placeholders if isinstance(placeholders, dict) else {}
    keys = sorted(k for k in provider_view(src, provider_id) if isinstance(k, str) and k.strip())
    if not keys:
        return None
    return (
        "Carried-forward placeholders available (set by previous turns; "
        "use them directly in tool arguments — the executor will substitute "
        "the real values): " + ", ".join(keys)
    )
