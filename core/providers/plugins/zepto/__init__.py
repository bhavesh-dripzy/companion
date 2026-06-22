"""Zepto — only the address-row parser survives the teardown.

The provider/agent logic was removed with the orchestrator. `address.py`
remains because the MCP/OAuth router (`companion_api/routers/mcp.py`) uses
`extract_zepto_address_rows` for the saved-addresses surface. It is
self-contained (stdlib only).
"""
