"""Zomato plugin (clean McpProvider).

Status (migration): restaurant + menu *search* — connection, execution,
card-building, and session LRU — are ported clean and validated against real
Zomato data (tests/core/test_zomato_provider.py + live read-only searches). Cart /
payment cards still run through the proven legacy executor via the
LegacyMcpAdapter (registered in bootstrap); swapping ZomatoProvider in there is
gated on porting the order-flow enrichment those cards need + fixtures that can't
be captured without mutating real orders.
"""

from core.providers.plugins.zomato.provider import ZomatoProvider

__all__ = ["ZomatoProvider"]
