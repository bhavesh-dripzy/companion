"""Swiggy Food plugin (clean McpProvider).

Status (migration): restaurant + menu *search* — connection, execution,
card-building, and session LRU — are ported clean and validated against real
Swiggy data (tests/core/test_swiggy_food_provider.py + live read-only searches on
admin_bhavesh tokens). Cart cards still run through the proven legacy executor via
the LegacyMcpAdapter (registered in bootstrap); swapping SwiggyFoodProvider in
there is gated on porting the order-flow enrichment + a fixture that can't be
captured without mutating real orders.
"""

from core.providers.plugins.swiggy_food.provider import SwiggyFoodProvider

__all__ = ["SwiggyFoodProvider"]
