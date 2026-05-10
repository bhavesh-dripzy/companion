# Companion — Zomato MCP setup

This repo is wired so **Cursor** can talk to **Zomato’s MCP server** (search restaurants, browse menus, cart, checkout, etc.) through your Zomato account.

---

## One-time setup (on your machine)

1. **Install Node.js**  
   You need `node` on your PATH so Cursor can run the local bridge script.

2. **Install dependencies**  
   From this project folder:

   ```bash
   npm install
   ```

   That installs `mcp-remote` into `node_modules/`. Cursor runs it via `.cursor/mcp.json`.

3. **Open this folder as the Cursor workspace**  
   Paths like `node_modules/mcp-remote/dist/proxy.js` are resolved from the **project root**.

4. **Reload MCP in Cursor**  
   Reload the window or refresh MCP servers in settings so **`zomato-mcp`** starts.

5. **Sign in to Zomato**  
   When Cursor first uses the server, it may open **OAuth / login**. Complete that in the browser.  
   You must use an account **allowed for Zomato MCP** (see [Zomato’s MCP readme](https://github.com/Zomato/mcp-server-manifest) — access may be invite-only).

---

## What happens under the hood (short)

1. **Cursor** reads `.cursor/mcp.json` and starts **`zomato-mcp`**.
2. **`mcp-remote`** runs as a tiny **local bridge**: it speaks **stdio** with Cursor on one side and **HTTPS** to Zomato on the other (`https://mcp-server.zomato.com/mcp`).
3. Your **logged-in session** is used so orders and addresses stay tied to your Zomato profile.
4. In chat, the AI can **call Zomato tools** (same idea as plugins): fetch your saved addresses, search by keyword (e.g. “burger”), open menus, build a cart, and so on — according to what each tool supports.

---

## Files that matter

| File | Role |
|------|------|
| `.cursor/mcp.json` | Tells Cursor to run `node …/proxy.js` with Zomato’s MCP URL |
| `package.json` | Pins **`mcp-remote`** so installs are repeatable |
| `node_modules/` | Created by **`npm install`** (ignored by git) |

---

## Typical “food search” flow in chat

1. **Your addresses** — Tools use **`get_saved_addresses_for_user`** so results are near **your saved delivery spots** (not random coordinates).
2. **Search** — e.g. **`get_restaurants_for_keyword`** with a keyword like `burger` or `pizza`.
3. **Menu / items** — Other tools fetch categories or item lists when you pick a restaurant.
4. **Cart & checkout** — Create cart, apply offers if needed, then checkout per tool rules (payments may involve QR/steps inside Zomato’s flow).

The assistant should follow tool docs: **do not invent lat/long** — use **`get_saved_addresses_for_user`** to get **`address_id`**.

---

## If something breaks

- **`node_modules` missing** → Run `npm install` again at the repo root.
- **MCP disconnected** → Reload Cursor; check MCP panel for **`zomato-mcp`** errors.
- **401 / login** → Finish OAuth again; confirm your Zomato account has MCP access.
- **OAuth redirect errors** → Zomato only allows certain OAuth redirect URIs; you may need an approved client or their support (see upstream README).

---

## LLM routing API (`send-message`)

`POST /api/send-message/` accepts JSON `{ "message": "<user text>" }` and asks **Gemini or OpenAI** (see env below) for a **`food_routing_plan`**: structured `intent`, `constraints`, and an ordered **`tool_plan`** compatible with **Zomato MCP tools** (placeholders like `__ADDRESS_ID__` are substituted later after address lookup).

**Environment:** `config/settings.py` loads **`.env`** via **python-dotenv**.

By default **`GEMINI_API_KEY`** or **`GOOGLE_API_KEY`** is used (**Gemini**) when set (`GEMINI_FOOD_ROUTER_MODEL`, default **`gemini-2.5-flash-lite`** — avoids frequent free-tier exhaustion on **`gemini-2.0-flash`**). Otherwise **`OPENAI_API_KEY`** (`OPENAI_FOOD_ROUTER_MODEL`, default `gpt-4o-mini`). Optional **`FOOD_ROUTER_PROVIDER`** = `gemini` or `openai` overrides auto selection.

Quick test: **`python scripts/verify_gemini.py`** (Gemini key) or **`python scripts/verify_openai.py`**.

Never commit `.env`; use **`.env.example`** as a template.

### Full flow (Gemini/OpenAI → Zomato MCP)

**`POST /api/food/query/`** — JSON `{ "message": "best pizza near me", "address_id": "<optional>", "execute_zomato": true }`.  
Runs the LLM plan, starts **`node_modules/mcp-remote`** (OAuth token must already work for Zomato on this machine — same login as Cursor), and returns **`food_routing_plan`** plus **`zomato.steps`** with live tool payloads (`get_saved_addresses_for_user`, `get_restaurants_for_keyword`, etc.).

Requirements: **`npm install`** at repo root, **`Node`** on `PATH`. Prefer **`GEMINI_FOOD_ROUTER_MODEL=gemini-2.5-flash-lite`** (free‑tier quotas on `gemini-2.0-flash` often show `limit: 0`).

**Payload options:** `include_whatsapp_link` (default `true`), `llm_optional` (never fail HTTP 503 on LLM/key issues).

Pricing and live restaurant payloads are **not** in this layer yet—they come from executing the MCP `tool_plan` in a separate step.

---

## Disclaimer

Zomato’s MCP terms, eligibility, and features are controlled by **Zomato**. This readme only describes **how this repo connects Cursor to their hosted server**.
