"""Swiggy Instamart plugin (clean McpProvider).

Status (migration): product *search* — connection, execution, card-building, and
the spinId LRU — is ported clean and validated against real Instamart data
(tests/core/test_swiggy_instamart_provider.py + a live read-only search on
admin_bhavesh tokens). Cart cards still run through the proven legacy executor via
the LegacyMcpAdapter (registered in bootstrap); the swap is gated on porting the
cart snapshot + session parity.
"""

from core.providers.plugins.swiggy_instamart.provider import SwiggyInstamartProvider

__all__ = ["SwiggyInstamartProvider"]
