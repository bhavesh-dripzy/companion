# Deploying Companion on a Debian / GCP VM

This folder only contains automation for this Django project.

## What to run on the server

Copy the repository to `/var/www/companion-app` (including `manage.py` and `requirements.txt`), then on the VM:

```bash
sudo EXTERNAL_IP=YOUR_PUBLIC_IP bash /var/www/companion-app/deploy/companion-setup-vm.sh
```

The script installs system packages, Python venv + dependencies, `npm install` at the repo root (for `mcp-remote` used by Zomato and Swiggy MCP), runs migrations and `collectstatic`, writes `gunicorn-companion` and nginx `companion-app` site configs, creates `/var/www/companion-app/.mcp-auth`, and restarts services.

## MCP OAuth tokens (Zomato and Swiggy Food)

Both providers use the same `mcp-remote` token layout under one directory (default on the VM: `/var/www/companion-app/.mcp-auth`). Each MCP URL gets its own hash-named files inside that tree. Ownership must allow `www-data` to read the directory and files. Optionally set `MCP_REMOTE_CONFIG_DIR` in `.env` (see `.env.example`).

### Copy tokens from your laptop

Copy your local `~/.mcp-auth/mcp-remote-<version>/` (or the repo’s `.mcp-auth/`) into `/var/www/companion-app/.mcp-auth/` on the VM, matching the `mcp-remote` version folder the server uses (`node_modules/mcp-remote/package.json`).

### Authorize on the VM (browser on the server)

After `npm install` has run in `/var/www/companion-app`:

```bash
# Zomato
sudo -u www-data -H bash /var/www/companion-app/scripts/zomato_mcp_prod_oauth.sh

# Swiggy Food (https://mcp.swiggy.com/food)
sudo -u www-data -H bash /var/www/companion-app/scripts/swiggy_food_mcp_prod_oauth.sh
```

Equivalent from the app directory: `npm run oauth:zomato:prod` and `npm run oauth:swiggy:prod` (same scripts; run as `www-data` on the VM if tokens must live under `/var/www/companion-app/.mcp-auth`).

Local development equivalents (default `.mcp-auth` in the repo root): `scripts/zomato_mcp_oauth.sh`, `scripts/swiggy_food_mcp_oauth.sh`.

Ensure production secrets include `ZOMATO_MCP_URL` and, when using Swiggy, `SWIGGY_FOOD_MCP_URL` (for example `https://mcp.swiggy.com/food`).

## After deploy

- Health: `curl http://YOUR_IP/api/`
- Food UI: open `http://YOUR_IP/` in a browser.
