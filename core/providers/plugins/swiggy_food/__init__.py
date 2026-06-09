"""Swiggy Food plugin (clean McpProvider) — the sole Swiggy Food path.

Restaurant + menu *search* — connection, execution, card-building, and session
LRU — are validated against real Swiggy data (tests/core/test_swiggy_food_provider.py
+ live read-only searches on admin_bhavesh tokens). Mutating order flows execute
through the same generic `McpProvider.run`; richer order-flow cards are a future
enhancement (no fixture is capturable without mutating real orders).
"""

from core.providers.plugins.swiggy_food.provider import SwiggyFoodProvider

__all__ = ["SwiggyFoodProvider"]
