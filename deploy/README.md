# Deploying Companion on a Debian / GCP VM

This folder only contains automation for this Django project.

## What to run on the server

Copy the repository to `/var/www/companion-app` (including `manage.py` and `requirements.txt`), then on the VM:

```bash
sudo EXTERNAL_IP=YOUR_PUBLIC_IP bash /var/www/companion-app/deploy/companion-setup-vm.sh
```

The script installs system packages, Python venv + dependencies, `npm install` at the repo root (for Zomato `mcp-remote`), runs migrations and `collectstatic`, writes `gunicorn-companion` and nginx `companion-app` site configs, creates `/var/www/companion-app/.mcp-auth`, and restarts services.

## Zomato MCP (OAuth tokens)

Copy your local `~/.mcp-auth/mcp-remote-<version>/` files into `/var/www/companion-app/.mcp-auth/` on the VM, matching the `mcp-remote` version folder your server uses (`node_modules/mcp-remote/package.json`). Ownership must be readable by `www-data`. Optionally set `MCP_REMOTE_CONFIG_DIR` in `.env` (see `.env.example`).

## After deploy

- Health: `curl http://YOUR_IP/api/`
- Food UI: open `http://YOUR_IP/` in a browser.
