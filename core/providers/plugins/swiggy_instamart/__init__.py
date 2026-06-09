"""Swiggy Instamart plugin (clean McpProvider) — the sole Instamart path.

Product *search* — connection, execution, card-building, and the spinId LRU — is
validated against real Instamart data (tests/core/test_swiggy_instamart_provider.py
+ a live read-only search on admin_bhavesh tokens). Mutating cart flows execute
through the same generic `McpProvider.run`; a richer cart-snapshot card is a
future enhancement.
"""

from core.providers.plugins.swiggy_instamart.provider import SwiggyInstamartProvider

__all__ = ["SwiggyInstamartProvider"]
