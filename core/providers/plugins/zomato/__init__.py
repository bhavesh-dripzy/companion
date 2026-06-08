"""Zomato plugin (clean McpProvider).

Status (migration): restaurant *search* — connection, execution, card-building,
and session LRU — is ported clean and validated against real Zomato data
(tests/core/test_zomato_provider.py + a live read-only search). Cart / menu /
payment cards still run through the proven legacy executor via the
LegacyMcpAdapter (registered in bootstrap); swapping ZomatoProvider in there is
gated on porting the order-flow enrichment those cards need + fixtures that can't
be captured without mutating real orders.
"""

from core.providers.plugins.zomato.provider import ZomatoProvider

__all__ = ["ZomatoProvider"]
