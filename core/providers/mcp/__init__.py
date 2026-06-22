"""MCP OAuth/token + connection layer.

The `McpProvider` base and its execution/card helpers were removed with the
legacy per-provider agents. What remains are the OAuth handshake, token vault,
remote/stdio connection, and result parsing primitives — imported as submodules
by the OAuth router and address helpers.
"""
