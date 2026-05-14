#!/usr/bin/env bash
# Production VM: browser OAuth for Swiggy Food MCP (https://mcp.swiggy.com/food).
#
# Run as the same user as gunicorn (typically www-data):
#   sudo -u www-data -H bash /var/www/companion-app/scripts/swiggy_food_mcp_prod_oauth.sh
#
# Serverless (Lambda): use the Companion API pairing / OAuth (provider=swiggy_food); tokens
# are stored in Secrets Manager (companion/mcp-tokens), same layout as Zomato.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export MCP_REMOTE_CONFIG_DIR="${MCP_REMOTE_CONFIG_DIR:-$ROOT/.mcp-auth}"
mkdir -p "$MCP_REMOTE_CONFIG_DIR"
echo "MCP_REMOTE_CONFIG_DIR=$MCP_REMOTE_CONFIG_DIR (Swiggy Food prod OAuth)"
echo "VM tip: sudo -u www-data -H bash $0"
exec bash "$ROOT/scripts/swiggy_food_mcp_oauth.sh"
