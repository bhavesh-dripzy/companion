"""Providers package.

The provider base/runner and the per-provider plugins were removed with the
legacy execution layer. What remains under here is the MCP OAuth/token layer
(`core.providers.mcp`) plus two self-contained plugin helpers the OAuth router
still uses (`plugins/kite/transport.py`, `plugins/zepto/address.py`).
"""
