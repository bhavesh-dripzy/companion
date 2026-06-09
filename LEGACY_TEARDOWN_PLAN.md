# Legacy Teardown — Handoff Plan

> **Purpose:** complete the "burn the bridge" teardown — delete `app/services/` entirely so the
> clean `core/` package is the only code path. Hand this to a fresh session; it is self-contained.
>
> **Prime directive:** keep prod green throughout (it runs `core/` now). Delete legacy code only
> *after* every external importer of it is gone. Match the rewrite's conventions: **clean, modular,
> pluggable, ≤250-line files, no `if/elif` provider branching in the pipeline.**

---

## 0. How to use this doc

Work **unit by unit**, in order. Each unit: make the edits → run the gates → (where possible)
**live-verify** → **commit**. Don't batch units into one commit. Re-grep for current line numbers
before editing (line numbers below drift as you edit):

```bash
grep -rn "app\.services\|app/services" companion_api/ core/ | grep -v "\.md:"
```

When that command returns **only docstring/comment mentions** (lines containing `Ported` / `Ports` /
strangler prose), `app/services/` has no live importers and can be deleted (Unit 8).

---

## 1. Where things stand

**Prod (live, account `616551057455` / `ap-south-1`, AWS profile `companion`):**
- API Lambda `companion-serverless-ApiFunctionCE271BD4-8ZNytOKC6UXy` runs the **core pipeline**. We
  removed the manual `LEGACY_PIPELINE=1` env var (it was a hand-applied mitigation, **never in CDK**).
- Verified live: text chat + Zomato MCP return real data; the clean planner is correct.
- Voice (Gemini Live) is **not a code bug** — the Gemini API project is out of prepaid credits
  (WS close `1011`). Fix = top up billing at ai.studio. Nothing to do in code.

**Branch `feat/legacy-teardown` (off `dev`), 3 commits, NOT deployed yet:**
1. `382ec57` — MCP token vault → `core/providers/mcp/token_store.py` + `token_vault.py`; clean-planner scaffold.
2. `57a74c9` — all 6 MCP providers flipped onto the clean generated planner.
3. `a9dc057` — `LEGACY_PIPELINE` fallback deleted from `main.py` + `voice_routes.py`.

**⚠️ Rollback note:** Prod currently runs the image built from `main` (which still contains the
`LEGACY_PIPELINE` fallback), so re-adding `LEGACY_PIPELINE=1` to the prod Lambda env **still rolls back
to legacy today**. Once this branch is merged to `main` and deployed, that escape hatch is gone — so
soak core in prod before deploying the teardown.

---

## 2. What's DONE (don't redo)

- **Cutover to core** (prod Lambda env).
- **Token vault** ported: `core/providers/mcp/token_store.py` (secret↔disk, restore, status,
  `merge_mcp_token_secret`) + `token_vault.py` (`ensure_mcp_tokens_on_disk` + proactive PKCE refresh).
  `remote.hydrate_tokens` uses it. Reuses `remote.py` primitives (`config_dir`, `effective_config_dir`,
  `remote_version`, `repo_root`, `_safe_user`). **Not yet ported:** `persist_mcp_auth_tokens` (still in
  legacy; `routers/mcp.py` OAuth completion uses it — see Unit 7).
- **Planner:** all 6 MCP providers (`zomato, swiggy_food, swiggy_instamart, zepto, district, kite`) now
  plan via `core/`'s generated planner. Mechanism (study before editing providers):
  - `McpProvider` base (`core/providers/mcp/mcp_base.py`): a `planner_preamble` property that
    **auto-loads `<plugin>/planner.md`**, and a `planner_context(self, ctx) -> str` hook (default `""`).
  - `core/pipeline/stages/plan.py` `_generic_plan`: builds the prompt from `planner_preamble` (or the
    generic one) and **prepends `provider.planner_context(ctx)`** (the session brief).
  - Each plugin folder now has `planner.md` (preamble) + `planner_context.py` (`build_context(ctx)`,
    a faithful port of legacy `_format_routing_message`). Providers **no longer define `plan()`**.

---

## 3. Architecture you must preserve

- **Live path:** `companion_api/main.py` routes + `voice_routes.py` → `companion_api/core_shadow.py`
  (`run_core_turn_sync`) → `core/pipeline/runner.py` (7 stages).
- **Pluggable:** adding/owning provider behaviour = files in `core/providers/plugins/<id>/` + one line
  in `core/registry/bootstrap.py`. No pipeline edits, no `if/elif` on provider id.
- **Relocation rule:** when you move a legacy module out of `app/services`, put **pipeline** concerns in
  `core/` and **web** concerns in `companion_api/`. Prefer reusing existing core equivalents over copying.
- **File size:** every file in `core/` ≤ 250 lines (CI-gated).

---

## 4. Verification toolkit

**Live core turn** (read-only flows; the confirmation gate blocks ordering). `admin_bhavesh` has vaulted
Zomato + Swiggy tokens. Kite uses TOOL-auth login (can't headless-test). Zepto/Instamart/District token
availability is uncertain — verify, and treat an auth failure as a *skip*, not a planner regression:

```bash
cd /Users/aashishmalhotra/dev/companion
AWS_PROFILE=companion AWS_REGION=ap-south-1 APP_SECRET_ID=companion/app PYTHONPATH=$PWD \
  .venv/bin/python -c "
import os; os.environ.pop('LEGACY_PIPELINE', None)
from companion_api.core_shadow import run_core_turn_sync
out = run_core_turn_sync({'message':'what are my saved zomato delivery addresses?'}, 'admin_bhavesh')
print(out.get('status'), out.get('routed_provider'), out.get('observable_trace'))
print((out.get('assistant_message') or '')[:200])
"
```

**Prod logs / state:**
```bash
AWS_PROFILE=companion AWS_REGION=ap-south-1 aws logs tail \
  /aws/lambda/companion-serverless-ApiFunctionCE271BD4-8ZNytOKC6UXy --since 20m --format short
# NOTE: mcp-remote "AbortError on _startOrAuthSse" during 'Shutting down...' is BENIGN
# (SSE stream torn down after the tool result already returned). Not a failure.
```

**Gates:**
```bash
.venv/bin/ruff check core scripts/check_file_size.py    # CI gate — core only
.venv/bin/python scripts/check_file_size.py core         # ≤250 lines
.venv/bin/pytest tests/core -q --ignore=tests/test_district_mcp_executor.py  # last is known-broken legacy
```
**`companion_api/` is NOT in the CI ruff gate** (CI lints only `core`), but still run per edited file:
`.venv/bin/ruff check companion_api/<file>` and `.venv/bin/python -m py_compile companion_api/<file>`,
and an import smoke: `PYTHONPATH=$PWD AWS_REGION=ap-south-1 .venv/bin/python -c "import companion_api.main"`.
(There are 2 pre-existing style warnings in `voice_routes.py` — `UP038`, `SIM105` — not yours; leave them.)

---

## 5. Remaining web-layer relocations (the work)

Current live `app/services` importers in `companion_api/` (re-grep for exact lines):

| Importer | Symbol(s) | Source module | Notes |
|---|---|---|---|
| `main.py` | `route_food_query` | `food_llm_router` | used by `send_whatsapp_message` (live REST route, not the pipeline) |
| `main.py` | `resolve_llm_from_env` | `llm_config` | **core has this**: `core/llm/config.py`. See gotcha ↓ |
| `main.py` | `build_hero_greeting` (local import) | `hero_greeting` | `/api/hero/greeting/` route |
| `main.py` | `WhatsAppClient` (local import) | `whatsapp.client` | `scheduled_reminder_handler` |
| `routers/reminders.py` | `execute_reminder_calendar` | `calendar` | reminders REST route |
| `routers/tools.py` | tool-provider CRUD (4 fns) | `tool_runtime` | admin tool CRUD |
| `routers/mcp.py` | `saved_address_label_from_mcp_item` | `food_response_builder` | pure 28-LOC util |
| `routers/mcp.py` | tool-provider CRUD | `tool_runtime` | |
| `routers/mcp.py` | MCP OAuth / pairing / token / test fns | `zomato_mcp_executor`, `swiggy_mcp_executor`, `stdio_mcp_executor` | **biggest**; see Unit 7 |
| `routers/whatsapp.py` | `handle_whatsapp_webhook_event` | `whatsapp.handler` | **needs rewiring**, see Unit 4 |
| `core/providers/plugins/whatsapp/provider.py` | `WhatsAppClient` (local import) | `whatsapp.client` | pipeline outbound send |

### Unit 2 — `food_llm_router` + the `resolve_llm_from_env` swap (do together)
- **Gotcha:** legacy `resolve_llm_from_env` returns a `NamedTuple`; core's (`core/llm/config.py`) returns
  a `@dataclass`. Its live consumer `route_food_query(...)` reads `.provider/.api_key/.model/.base_url`
  (attribute access — fine with the dataclass) but **check `food_llm_router` for any tuple-unpacking** of
  the `ResolvedLlm` before swapping `main.py` line 17 to `from core.llm.config import resolve_llm_from_env`.
- Relocate `route_food_query` (and only what it needs) into `companion_api/` (web concern) — e.g.
  `companion_api/food_routing.py` — or fold into the `send_whatsapp_message` route. Check its dep tail
  (`core/llm/json_mode.py` already ports the JSON-mode logic from `food_llm_router`; reuse it).
- Verify: `import companion_api.main`; hit the logic path mentally; ruff + py_compile.

### Unit 3 — `hero_greeting`
- `build_hero_greeting` (`/api/hero/greeting/`). Relocate to `companion_api/hero_greeting.py`. Check its
  deps (profile cache, possibly weather/LLM). Keep it web-layer.

### Unit 4 — WhatsApp (**rewire, not just move**)
- `app/services/whatsapp/handler.py::handle_whatsapp_webhook_event` **calls the legacy `run_agent_turn`** —
  it must be **rewired to `run_core_turn_sync`** (the core path), then relocated to `companion_api/` (the
  webhook is a web concern). `client.py` is a self-contained HTTP client → move to `companion_api/whatsapp_client.py`
  (or `core/` if the pipeline `WhatsAppProvider` is the primary user — it imports it at
  `core/providers/plugins/whatsapp/provider.py:68`). Update all 3 importers
  (`routers/whatsapp.py`, `main.py` scheduled handler, the core whatsapp provider).

### Unit 5 — `calendar` (reminders route)
- `routers/reminders.py` uses `execute_reminder_calendar` from `app/services/calendar`. Note a **clean
  pipeline** calendar provider already exists (`core/providers/plugins/calendar/`). Either reuse it or
  relocate the executor logic the REST route needs into `companion_api/`. Confirm the core calendar
  provider is itself `app.services`-free first.

### Unit 6 — `tool_runtime` CRUD
- `get_tool_provider, list_tool_providers, save_custom_tool_provider, delete_custom_tool_provider,
  update_tool_provider_tools` used by `routers/tools.py` + `routers/mcp.py`. ~1034 LOC. Relocate to
  `companion_api/tool_runtime.py` (it's DynamoDB-backed CRUD — web/admin concern). Watch its dep tail
  (may pull other `app.services` bits — relocate only what's needed, leave the rest to die with Unit 8).
- Also relocate the 28-LOC `saved_address_label_from_mcp_item` (pure util) into `routers/mcp.py` or a
  small `companion_api/` util.

### Unit 7 — `routers/mcp.py` MCP setup routes (**biggest**)
The OAuth/pairing/token/test routes wrap `zomato_mcp_executor` / `swiggy_mcp_executor` /
`stdio_mcp_executor`. Triage the imported functions:
- **Already in core** (swap to them): `get_mcp_token_status`, `restore_mcp_auth_tokens` →
  `core/providers/mcp/token_store.py`; `_mcp_token_secret`→`mcp_token_secret`,
  `_merge_mcp_token_secret`→`merge_mcp_token_secret`, `_server_url_hash`→`server_url_hash`,
  `_mcp_remote_config_dir`→`remote.config_dir`, `_mcp_remote_version`→`remote.remote_version`,
  `repo_root_from_settings`→`remote.repo_root`.
- **No core equivalent yet** (port or rethink): `start_web_oauth`, `complete_web_oauth`,
  `create_pairing_session`, `complete_pairing_session`, `find_pairing_session_info`,
  `persist_mcp_auth_tokens`, `diagnose_mcp_environment`, `run_tool_plan_sync`,
  `run_swiggy_tool_plan_sync`, `list_mcp_tools_sync`, `list_stdio_mcp_tools_sync`.
  These are MCP onboarding/admin/test helpers. Options: (a) port the OAuth/pairing handshake into a new
  `core/providers/mcp/oauth.py` (clean, reusable); (b) the `run_*_tool_plan_sync` / `list_*_tools` "test"
  routes can be reimplemented on the clean `McpProvider` (discover_tools / run) or dropped if unused by
  the frontend. **Check the frontend** (`frontend/src`) for which `/api/mcp/...` routes are actually called
  before porting vs deleting.

---

## 6. Unit 8 — delete the legacy tree + flags

Only after Units 2–7 (the `app.services` grep returns docstrings-only):

1. **Bootstrap rewire** (`core/registry/bootstrap.py`): the 4 HTTP providers
   (`Twilio/Maps/Calendar/WhatsApp`) are already registered directly. Replace the `MCP_CONFIGS` /
   `LegacyMcpAdapter` loop with direct registration of the 6 clean MCP providers (use the existing
   `_clean_provider_classes()` map). Delete `_DEFAULT_CLEAN_MCP`, `_clean_mcp_ids` (the **`CLEAN_MCP` flag**),
   and the `from core.providers.legacy import ...` import.
2. **Delete** `core/providers/legacy/` (`__init__.py`, `adapter.py`, `configs.py` — `LegacyMcpAdapter` +
   `MCP_CONFIGS`) and `core/providers/mcp/legacy_planner.py` (its only importer is the adapter).
3. **Remove the `LEGACY_PIPELINE` flag** references everywhere (now a no-op): bootstrap comment,
   `README.md` §7 table + "roll back the whole pipeline" row, `CONTRIBUTING.md`, ADRs as needed.
4. **`git rm -r app/services`.** Then:
   - **Dockerfile:** remove `COPY app/services ./app/services`. `main.py:food_ui()` has a legacy fallback
     to `app/templates/app/food_ui.html` — the frontend SPA (`frontend/dist`) is primary; either keep
     `app/templates` (and its `COPY`) or remove the fallback branch in `main.py` and drop `app/` entirely.
   - Grep the whole repo for `app.services` / `app/services` / `run_agent_turn` / `_format_routing_message`
     / `_plan_for_provider` to confirm zero live refs (tests included).
5. **Tests:** delete/port the legacy tests under root `tests/` that import `app.services`
   (e.g. `test_api.py`, `test_uber.py`, `test_maps_agent.py`, `test_leadgen.py`,
   `test_swiggy_mcp_executor.py`, `test_district_mcp_executor.py`). Keep `tests/core/`.
6. **Docs:** update `README.md` (§2 mental model, §3 repo map) + `CONTRIBUTING.md` to drop the
   strangler/"two trees" framing — `core/` is now the only tree.

---

## 7. Stage 5 — repo hygiene + infra audit

**Repo hygiene** (verify unreferenced first — `grep -rn <name>` — then `git rm`):
- Root scratch files: `test_api.py`, `test_lambda_secret.py`, `test_parse.py`, `token.txt`, `token2.txt`,
  `legacy_token.txt`, `auth.json`, `auth2.json`, `response.json`, `ssm_run.py`, `new_scp.json`,
  `gemini-live-test.html`, and the untracked `scripts/ondc_keygen.py`. (Several may be gitignored — check
  `git status` / `.gitignore`.) **These can contain real tokens — don't commit them anywhere; just remove.**
- Stale branches (after confirming merged): `codex/*`, `feat/ui-revamp`, `feat/kite-provider`,
  `feat/voice-status-narration` — local + `origin`.

**AWS infra audit** (READ-ONLY first; confirm orphaned before any delete; profile `companion`):
- DynamoDB: distinguish the **live CDK** tables (`companion-graph-ProfileCacheTable…` /
  `…LeadCacheTable…`, plus `companion-families/-messages/-reminders/-members`) from any **pre-rewrite
  orphans** (e.g. `companion-profile-cache`, `companion-lead-cache` from the old table-name split).
  `aws dynamodb list-tables`; check item counts / last-write before deleting.
- Check for unused stacks / Lambdas / the graph EC2 box (`DEPLOY_EC2`). Don't delete CDK-managed
  resources by hand — remove via CDK (`infra/`) so state stays consistent.

---

## 8. Branches, CI, deploy, rollback

- Promotion: `feature/* → dev → staging → main` (CONTRIBUTING.md). `feat/legacy-teardown` is off `dev`.
- Deploy = **push to `main`** → GitHub Actions `deploy.yml` → `cdk deploy` (DockerImageFunction, ~6.5 min).
  `staging` deploys via `deploy-staging.yml`. Don't deploy the teardown until core has soaked.
- CI on PRs: `ruff check core` + `scripts/check_file_size.py core` + `pytest` + frontend build.
- **Rollback before deploy:** re-add `LEGACY_PIPELINE=1` to the prod Lambda env (works only while prod
  runs `main`'s pre-teardown image). **After the teardown deploys, rollback = redeploy a prior image.**

---

## 9. Suggested order & commit cadence

1. Unit 2 (food_llm_router + resolve swap) → verify `send_whatsapp_message` path → commit.
2. Unit 3 (hero_greeting) → commit.
3. Unit 5 (calendar) → commit.
4. Unit 6 (tool_runtime + saved-address util) → commit.
5. Unit 4 (whatsapp rewire) → live-verify a webhook turn if possible → commit.
6. Unit 7 (mcp.py routes) → check frontend usage → port/drop → commit.
7. Unit 8 (delete app/services + flags + bootstrap rewire) → full grep clean → `pytest tests/core` →
   live-verify all reachable providers → commit.
8. Stage 5 (hygiene + infra) → commit.
9. Open PR `feat/legacy-teardown → dev`; promote dev→staging→main; soak; then it's the only tree.

**At each step:** ruff (core) + file-size + `pytest tests/core` green, edited `companion_api` files
`py_compile` + import-smoke clean, and a live core turn still returns real data.

---

## 10. Memory / context pointers
- Auto-memory: `architecture-rewrite-companion.md` (cutover + teardown progress),
  `core-pipeline-live-validation.md` (live-run recipe), `aws-access-companion-dev.md`,
  `kite-tool-auth-login.md`, `leadgen-graph-pipeline.md`.
- Design of record: `docs/architecture/ARCHITECTURE.md` + ADRs. Repo guide: `README.md`.
