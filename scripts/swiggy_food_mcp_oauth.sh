#!/usr/bin/env bash
# OAuth + session for Swiggy Food MCP (keep terminal open until browser auth finishes).
# Tokens are written under MCP_REMOTE_CONFIG_DIR (default: repo .mcp-auth), same layout as Zomato.
# For production VM defaults (/var/www/companion-app), use: scripts/swiggy_food_mcp_prod_oauth.sh
# Usage: bash scripts/swiggy_food_mcp_oauth.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export MCP_REMOTE_CONFIG_DIR="${MCP_REMOTE_CONFIG_DIR:-$ROOT/.mcp-auth}"
mkdir -p "$MCP_REMOTE_CONFIG_DIR"
CLIENT_JS="$ROOT/node_modules/mcp-remote/dist/client.js"
if [[ ! -f "$CLIENT_JS" ]]; then
  echo "Missing $CLIENT_JS — run: npm ci" >&2
  exit 1
fi
cd "$ROOT"
echo "MCP_REMOTE_CONFIG_DIR=$MCP_REMOTE_CONFIG_DIR"
echo "Swiggy Food MCP: https://mcp.swiggy.com/food"
echo "Leave this terminal open until OAuth completes in your browser."
exec node "$CLIENT_JS" https://mcp.swiggy.com/food --auth-timeout 300 --debug
