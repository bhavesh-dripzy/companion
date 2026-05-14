#!/usr/bin/env bash
# Production VM: browser OAuth for Zomato MCP; writes mcp-remote tokens next to the app.
#
# Run as the same user as gunicorn (typically www-data) so the API can read tokens:
#   sudo -u www-data -H bash /var/www/companion-app/scripts/zomato_mcp_prod_oauth.sh
#
# Serverless (Lambda): there is no browser on the function — use the Companion API instead:
#   POST /api/mcp/pairing/?provider=zomato  (or GET /api/mcp/oauth/start?provider=zomato when supported).
# Tokens sync to Secrets Manager (companion/mcp-tokens) and restore on cold start.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export MCP_REMOTE_CONFIG_DIR="${MCP_REMOTE_CONFIG_DIR:-$ROOT/.mcp-auth}"
mkdir -p "$MCP_REMOTE_CONFIG_DIR"
echo "MCP_REMOTE_CONFIG_DIR=$MCP_REMOTE_CONFIG_DIR (Zomato prod OAuth)"
echo "VM tip: sudo -u www-data -H bash $0"
exec bash "$ROOT/scripts/zomato_mcp_oauth.sh"
