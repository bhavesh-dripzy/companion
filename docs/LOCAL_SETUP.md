# Local development setup

Run Companion on your machine: React UI + FastAPI backend. Credentials live in a **private local file** (never committed).

---

## 1. Prerequisites

| Tool | Version | Check |
|------|---------|--------|
| Python | 3.12+ | `python3 --version` |
| Node.js | 20+ (22 for Capacitor CLI) | `node --version` |
| npm | 10+ | `npm --version` |
| git | any | `git --version` |

Optional (for MCP providers like Zomato):

- **Node** + `npm install` at repo root (installs `mcp-remote`)
- Browser for OAuth login flows

---

## 2. Clone and install dependencies

```bash
cd /path/to/companion

# Python backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Node (mcp-remote + frontend)
npm install
cd frontend && npm install && cd ..
```

---

## 3. Credentials file (your private copy)

```bash
cp CREDENTIALS.local.example.txt CREDENTIALS.local.txt
chmod 600 CREDENTIALS.local.txt
```

Edit **`CREDENTIALS.local.txt`** and fill in:

1. **Local login** — `LEGACY_ADMIN_USERNAME` / `LEGACY_ADMIN_PASSWORD` (e.g. `local_admin` / `local_password`)
2. **At least one LLM key** — `GEMINI_API_KEY`, or `DEEPSEEK_API_KEY`, or `OPENAI_API_KEY`
3. Optional: AWS, Maps, Calendar, voice keys (see template sections)

> `CREDENTIALS.local.txt` is in `.gitignore`. **Never commit it.**

---

## 4. Create `.env` (backend config)

```bash
cp .env.example .env
```

Add these lines to **repo-root `.env`** (minimum for UI login + chat):

```bash
# Local login — required when Cognito is NOT configured
LEGACY_ADMIN_USERNAME=local_admin
LEGACY_ADMIN_PASSWORD=local_password

# Leave Cognito empty for local dev
COGNITO_USER_POOL_ID=
COGNITO_CLIENT_ID=

# At least one LLM key
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash-lite

# Optional: actually run MCP tools (Zomato, etc.)
EXECUTE_TOOLS_BY_DEFAULT=true
MCP_REMOTE_CONFIG_DIR=.mcp-auth
```

Copy values from your filled-in `CREDENTIALS.local.txt`.

**Important:** If `COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID` are set, login goes through AWS Cognito instead of legacy admin — use production credentials or clear those vars for local dev.

---

## 5. Frontend env (optional — voice only)

```bash
cp frontend/.env.example frontend/.env
```

Set `VITE_GEMINI_API_KEY` if you use voice in the browser. Chat text works without this.

---

## 6. Run locally (recommended: two terminals)

### Terminal 1 — API (port 8000)

```bash
cd /path/to/companion
source .venv/bin/activate
uvicorn companion_api.main:app --reload --host 127.0.0.1 --port 8000
```

Wait for: `Application startup complete.`

### Terminal 2 — UI dev server (port 5173)

```bash
cd /path/to/companion/frontend
npm run dev -- --host 127.0.0.1
```

Vite proxies `/api` → `http://localhost:8000`.

### Open in browser

**http://127.0.0.1:5173/**

| Field | Value |
|-------|--------|
| Username | `local_admin` (or your `LEGACY_ADMIN_USERNAME`) |
| Password | `local_password` (or your `LEGACY_ADMIN_PASSWORD`) |

---

## 7. Verify login (curl)

```bash
curl -s -X POST http://127.0.0.1:8000/api/login/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"local_admin","password":"local_password"}'
```

Expected:

```json
{"access_token":"eyJ...","token_type":"bearer","username":"local_admin"}
```

If you get `"Invalid credentials."`:

- Check `LEGACY_ADMIN_USERNAME` / `LEGACY_ADMIN_PASSWORD` are in `.env`
- Restart uvicorn after editing `.env`
- Ensure Cognito vars are empty

---

## 8. MCP providers (Zomato, Swiggy, District, …)

Food/grocery/event tools need OAuth tokens on your machine.

**One-time Zomato example** (from repo root):

```bash
export MCP_REMOTE_CONFIG_DIR="$PWD/.mcp-auth"
mkdir -p "$MCP_REMOTE_CONFIG_DIR"
node node_modules/mcp-remote/dist/client.js \
  https://mcp-server.zomato.com/mcp --auth-timeout 300 --debug
```

Complete the browser login. Tokens are stored under `.mcp-auth/` (gitignored).

Connect other providers from **Settings → Connected Apps** in the UI, or repeat with their MCP URLs (see `.env.example`).

---

## 9. Alternative: single server (production-like)

Build the frontend once and serve everything from FastAPI:

```bash
cd frontend && npm run build && cd ..
source .venv/bin/activate
uvicorn companion_api.main:app --host 127.0.0.1 --port 8765
```

Open **http://127.0.0.1:8765/** — same login credentials.

Use this when you don't need hot reload on the React app.

---

## 10. CLI agent (no browser)

Handles login + session echo automatically:

```bash
source .venv/bin/activate
export LEGACY_ADMIN_USERNAME=local_admin
export LEGACY_ADMIN_PASSWORD=local_password
# ... source .env for LLM keys ...

python scripts/agent_chat.py --reset "show me pizza near me on zomato"
python scripts/agent_chat.py "add the first one to cart"
```

---

## 11. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Login fails | Add `LEGACY_ADMIN_*` to `.env`, restart uvicorn, clear Cognito vars |
| Chat returns errors / no LLM | Set at least one of `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENAI_API_KEY` |
| Tools don't run | Set `EXECUTE_TOOLS_BY_DEFAULT=true` |
| Zomato "not connected" | Run MCP OAuth (§8) or connect in Settings |
| UI loads but API 404 | Start backend on 8000; use port 5173 (not 8000 alone in dev mode) |
| Port in use | `pkill -f uvicorn` or change port |

**Health check:** http://127.0.0.1:8000/api/ → `{"status":"ok"}`

---

## 12. Security reminders

- Never commit `.env`, `CREDENTIALS.local.txt`, or `.mcp-auth/`
- Use `LEGACY_ADMIN_*` only for local dev — production uses Cognito
- Rotate any key that was ever pasted into chat or committed by mistake

---

## File reference

| File | Purpose |
|------|---------|
| `CREDENTIALS.local.example.txt` | Committed template — copy to `CREDENTIALS.local.txt` |
| `CREDENTIALS.local.txt` | Your private secrets (gitignored) |
| `.env` | Backend runtime config (gitignored) |
| `.env.example` | Backend env template |
| `frontend/.env` | Voice keys only (gitignored) |
| `docs/LOCAL_SETUP.md` | This guide |
