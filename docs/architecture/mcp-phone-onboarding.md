# Phone-first MCP onboarding

Connecting an MCP provider happens entirely from the phone. `GET /api/mcp/oauth/start`
negotiates a per-provider **completion mode** at connect time and the frontend adapts;
nothing is hardcoded per provider, so a provider relaxing its redirect-URI policy
upgrades itself to the seamless path automatically.

## Completion modes

`core/providers/mcp/oauth.py::_negotiate_client` tries, in order:

1. **`redirect` (seamless)** — dynamic client registration (RFC 7591) with the
   Companion-hosted callback `{PUBLIC_BASE_URL}/api/mcp/oauth/callback`. The phone
   browser does the whole round trip: Connect → provider login (OTP arrives on the
   same phone) → hosted callback exchanges the code (PKCE) and vaults mcp-remote-
   compatible token files → result page bounces back to `/?mcp_oauth=success&provider=X`
   → the SPA reopens Settings on that provider, now Connected.
2. **`paste` (one manual step)** — used when the provider's DCR rejects our domain
   (redirect-URI domain allowlist) or returns a fixed client pinned to other URIs.
   We register `http://localhost:3334/oauth/callback` (universally allowed — it is
   what mcp-remote uses), or reuse a localhost URI already on the fixed client. The
   user logs in on the phone, lands on an unreachable `localhost` URL whose query
   string carries `code`+`state`, copies the address, and pastes it into Settings.
   `POST /api/mcp/oauth/complete/` extracts the pair and runs the same token
   exchange server-side (we hold the PKCE verifier; token endpoints do not care who
   calls them).
3. **pairing (laptop fallback)** — the legacy `curl | python3` mcp-remote script,
   only if OAuth cannot be negotiated at all (no DCR advertised, network failure).

Pending sessions live in Secrets Manager `companion/mcp-tokens` →
`users.<user>.oauth_pending` (15-min TTL); completion writes the standard
`mcp-remote-<ver>/<hash>_{tokens,client_info,code_verifier}` files to `/tmp` + vault.

## Provider behavior (probed live 2026-06-11)

| Provider | DCR w/ hosted https callback | Mode |
|---|---|---|
| Zomato | accepted; Hydra-style — validates redirect at `/authorize`, consent rendered | `redirect` |
| Swiggy Food / Instamart | accepted (static `swiggy-mcp` client echoes our URI) | `redirect` (see risk below) |
| Zepto | rejected — "not from allowed domains"; `http://localhost:*` allowed | `paste` |
| District | rejected — 403 "Invalid redirect_uris"; localhost allowed; Hydra-style | `paste` |
| Groww | register returns a fixed shared client pinned to other URIs (incl. `localhost:52155`) | `paste` via its own localhost URI |
| Kite | TOOL auth (login tool, session-scoped) — outside this flow | — |

**Swiggy risk:** its `/register` echoes whatever redirect URI you send and the login
page renders regardless, so a post-login "invalid redirect_uri" is possible. If that
shows up, set `MCP_OAUTH_PASTE_PROVIDERS=swiggy_food,swiggy_instamart` (comma list,
env or `companion/app` secret) to pin those providers to paste mode — no deploy needed.

## Config

- `PUBLIC_BASE_URL` (`companion/app` secret) — must be the SPA origin
  (`https://houzzat.in`): it is both the registered OAuth callback domain and where
  the post-OAuth bounce-back lands (the login token lives in that origin's
  localStorage). The Google Calendar OAuth app's console redirect URI must match it.
- `MCP_OAUTH_PASTE_PROVIDERS` — force paste completion per provider id.

## Long-term

Zepto/District/Groww maintain redirect-domain allowlists that already include agent
platforms (e.g. `claude.ai`). Getting `houzzat.in` allowlisted upgrades them to the
seamless `redirect` mode with zero code change.
