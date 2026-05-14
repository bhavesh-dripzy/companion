#!/usr/bin/env bash
# OAuth + tools/list against Zomato MCP (process must stay running until browser + OTP finish).
# Tokens are written under MCP_REMOTE_CONFIG_DIR (default: repo .mcp-auth).
# For production VM defaults (/var/www/companion-app), use: scripts/zomato_mcp_prod_oauth.sh
# Usage: bash scripts/zomato_mcp_oauth.sh
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
echo "Leave this terminal open until OAuth + OTP complete."
exec node "$CLIENT_JS" https://mcp-server.zomato.com/mcp --auth-timeout 300 --debug
