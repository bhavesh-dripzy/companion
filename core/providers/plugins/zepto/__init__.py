"""Zepto plugin (clean McpProvider) — the sole Zepto path.

Product *search* — connection, execution, structured card-building, and the
productVariantId LRU — is validated against real Zepto data
(tests/core/test_zepto_provider.py + a live read-only serviceability→search on
admin_bhavesh tokens). Mutating cart flows execute through the same generic
`McpProvider.run`; a richer cart-snapshot card is a future enhancement.
"""

from core.providers.plugins.zepto.provider import ZeptoProvider

__all__ = ["ZeptoProvider"]
