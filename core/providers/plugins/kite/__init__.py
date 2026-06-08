"""Kite (Zerodha) plugin (clean McpProvider).

Status (migration): CUT OVER (clean by default). The read-only market/account
surfaces — instruments, holdings, positions, open orders, margin — are ported
clean and validated live against a real Zerodha account. The two-step ORDER
SAFETY (never auto-place/modify/cancel until the user confirms) is enforced
entirely by the planner prompt (`prompts/trading.system.md`), which `plan()`
delegates to unchanged — so flipping the provider preserves it identically. The
structured `session_kite_order_ticket` / order-ticket card is vestigial in the
legacy code (no path ever populates it; the two-step UX is the planner's
read-tools + natural-language "confirm?" reply), so nothing is lost. merge()
preserves any round-tripped session_kite_order_ticket verbatim.
"""

from core.providers.plugins.kite.provider import KiteProvider

__all__ = ["KiteProvider"]
