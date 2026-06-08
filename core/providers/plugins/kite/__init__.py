"""Kite (Zerodha) plugin (clean McpProvider).

Status (migration): the read-only market/account surfaces — instruments,
holdings, positions, open orders, margin — are ported clean (connection, TOOL-auth
login, execution, typed card-building, instruments LRU + snapshot session state)
and validated live against a real Zerodha account (read-only). The mutating order
flow (two-step confirm + order-ticket round-trip) stays on the proven legacy
executor via the LegacyMcpAdapter; the swap is gated on porting it + session parity.
"""

from core.providers.plugins.kite.provider import KiteProvider

__all__ = ["KiteProvider"]
