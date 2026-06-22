"""Kite (Zerodha) — only the OAuth/login transport survives the teardown.

The provider/agent logic was removed with the orchestrator. `transport.py`
remains because the MCP/OAuth router (`companion_api/routers/mcp.py`) uses it
for Kite's TOOL-auth login (probe/start/save/clear sid). It is self-contained
(imports only `core.providers.mcp.token_store` + stdlib).
"""
