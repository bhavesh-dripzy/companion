"""Zepto plugin (clean McpProvider).

Status (migration): product *search* — connection, execution, structured
card-building, and the productVariantId LRU — is ported clean and validated
against real Zepto data (tests/core/test_zepto_provider.py + a live read-only
serviceability→search on admin_bhavesh tokens). Cart cards still run through the
proven legacy executor via the LegacyMcpAdapter; the swap is gated on porting the
cart snapshot + session parity.
"""

from core.providers.plugins.zepto.provider import ZeptoProvider

__all__ = ["ZeptoProvider"]
