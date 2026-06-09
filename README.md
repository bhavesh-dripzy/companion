# Companion — multi-provider food / q-commerce / going-out / finance assistant

Companion is a conversational + voice assistant. A user message (or voice turn) is routed to a
domain, one or more **provider plugins** plan and run tool calls (mostly over **MCP**), and the
assistant narrates the result with rich attachments — restaurant/menu/cart cards, payment QR,
product grids, movie/event cards, stock quotes. A separate **knowledge-graph / leadgen** subsystem
learns from each turn asynchronously and personalizes future ones.

It runs **serverless**: FastAPI on AWS Lambda (via Mangum) behind an HTTP API Gateway, in account
`616551057455` / `ap-south-1`.

> **The clean, pluggable `core/` package is the only pipeline.** The strangler rewrite is complete —
> the old `app/services/` monolith has been fully torn down. If you're an AI agent or new contributor,
> **read [§2 Mental model](#2-mental-model-read-this-first) first.**

---

## Table of contents

1. [TL;DR for agents](#1-tldr-for-agents)
2. [Mental model (read this first)](#2-mental-model-read-this-first)
3. [Repo map — where everything lives](#3-repo-map--where-everything-lives)
4. [The request lifecycle (the pipeline)](#4-the-request-lifecycle-the-pipeline)
5. [Providers (the plugin model)](#5-providers-the-plugin-model)
6. [Knowledge graph & leadgen](#6-knowledge-graph--leadgen)
7. [Config & feature flags](#7-config--feature-flags)
8. [Local dev, test, lint](#8-local-dev-test-lint)
9. [Branches, CI, environments, deploy](#9-branches-ci-environments-deploy)
10. ["Where do I look if I want to…"](#10-where-do-i-look-if-i-want-to)
11. [Deeper docs & links](#11-deeper-docs--links)

---

## 1. TL;DR for agents

- **Live code path:** `companion_api/main.py` (FastAPI routes) → `companion_api/core_shadow.py`
  (`run_core_turn_sync`) → **`core/` pipeline** (`core/pipeline/runner.py`). There is no other path.
- **The architecture you build on is `core/`.** Every file is **≤250 lines** (CI-enforced).
- **Adding a provider = a plugin + one line in `core/registry/bootstrap.py`.** No pipeline edits.
  See [CONTRIBUTING.md](CONTRIBUTING.md#adding-a-provider-the-pluggable-path).
- **Design doc:** [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md) +
  [ADRs](docs/architecture/adr/). **Branch/env rules:** [CONTRIBUTING.md](CONTRIBUTING.md).
- **Lint/test gates:** `ruff check core`, `python scripts/check_file_size.py core`, `pytest tests/core`.
- **Don't** add `asyncio.run` inside the pipeline — it's async-first ([ADR-0003](docs/architecture/adr/0003-async-first-pipeline.md)).

---

## 2. Mental model (read this first)

**`core/` is the only tree.** The original `app/services/` monolith — `agents/pipeline.py` (~4k
lines), the per-provider `*_mcp_executor.py` files, `tool_runtime.py`'s if/elif dispatch — has been
deleted. Everything runs through the clean, pluggable rewrite:

- **contracts** (pure Pydantic), a **registry**, a **7-stage async pipeline**, and **provider plugins**.
- Every file is **≤250 lines** (CI-enforced). The pipeline is **async-first** (no `asyncio.run` inside it).
- Planning (generated planner + per-plugin `planner.md`/`planner_context`), the MCP token vault, and
  the OAuth/pairing connect flows all live in `core/` now — there is no legacy fallback.

The web layer (`companion_api/`) holds FastAPI routes + DynamoDB repos and calls into `core/` via
`core_shadow.run_core_turn_sync`. The knowledge-graph extraction package (`leadgen/`) runs on the
separate graph EC2 worker. Design rationale is in the [ADRs](docs/architecture/adr/).

---

## 3. Repo map — where everything lives

```
core/                              # ★ the clean architecture (build here)
  contracts/                       # pure Pydantic: Tool, ToolCall/ToolPlan, ToolResult,
                                   #   SessionState, Attachment, TurnRequest/Response, RoutingDecision
  llm/                             # async LLM client, robust JSON parser, fn-calling schema
  registry/                        # ProviderRegistry + ToolRegistry + bootstrap.py (the ONE
                                   #   registration point — every provider registered in one line)
  middleware/                      # planner_prompt (generated), validator, dispatcher, confirmation, errors
  pipeline/                        # runner.py + stages/{personalize,route,plan,execute,collect,narrate,persist}.py
  providers/
    base.py                        # the Provider Protocol (the plugin interface)
    mcp/                           # shared MCP base: connection.py, remote.py (mcp-remote launch),
                                   #   token_store.py + token_vault.py (OAuth vault), oauth.py + pairing.py
                                   #   (hosted connect flows), diagnostics.py, placeholders.py,
                                   #   result_parse.py, mcp_base.py (McpProvider), food_session.py, lru.py
    http/                          # shared HTTP provider base (statically-declared tools)
    plugins/                       # ★ one folder per provider — see §5
  boundaries/                      # KG/leadgen seam (TurnEventPublisher, ProfileReadService protocols)
  app_factory.py                   # wires registry + boundaries + llm into a PipelineRunner

companion_api/                     # the web layer (FastAPI)
  main.py                          # app + router includes; _companion_agent_turn entrypoint
  core_shadow.py                   # adapts a web request → core TurnRequest → runner → legacy-shaped envelope
  routers/                         # auth, mcp, chats, reminders, profile, maps, whatsapp, calendar, tools
  voice_routes.py                  # realtime voice (OpenAI Realtime / Gemini Live)
  secrets.py, settings.py          # secret + env resolution
  *_repo.py / profile_cache.py     # DynamoDB repositories

leadgen/                           # KG extraction package (schema, Neo4j client, LLM extractor)
                                   #   — runs on the graph EC2 worker; the Lambda only enqueues to SQS

infra/                             # AWS CDK (Python)
  app.py                           # env selector: COMPANION_ENV=dev|staging|prod → stack names/domain
  companion_stack.py               # Lambda + API GW + Cognito + DynamoDB + Secrets + EventBridge
  graph_stack.py                   # SQS + cache tables (+ EC2/Neo4j only when DEPLOY_EC2=true)

deploy/graph-ec2/                  # the graph-worker box: graph_service.py (FastAPI) + worker.py (SQS consumer)
frontend/                          # React SPA (Vite) — chat UI, admin graph dashboard
tests/                             # tests/core/ = the suite; root tests/ = companion_api (web-layer) tests
docs/architecture/                 # ARCHITECTURE.md + adr/*.md  ← the design of record
scripts/check_file_size.py         # the ≤250-line gate
.github/workflows/                 # ci.yml, deploy.yml (main), deploy-staging.yml (staging)
```

---

## 4. The request lifecycle (the pipeline)

Chat **and** voice run the **same** 7-stage async pipeline (`core/pipeline/runner.py`):

```
TurnRequest → Personalize → Route → Plan → Execute → Collect → Narrate → Persist → TurnResponse
```

| Stage | File | Responsibility |
|-------|------|----------------|
| Personalize | `stages/personalize.py` | Profile read (KG seam, fail-open) + home/office address resolution. |
| Route | `stages/route.py` | Orchestrator LLM → `RoutingDecision{task_type, providers[]}` (+ stickiness). Name an app → that app; name none → all relevant providers (issue #14). |
| Plan | `stages/plan.py` | Per-provider planner → validated `ToolPlan`. Prompt = generated preamble + each plugin's `planner.md`/`planner_context`. |
| Execute | `stages/execute.py` | Provider fan-out (`asyncio.gather`), partial-failure tolerant → `ToolResult`s. |
| Collect | `stages/collect.py` | `provider.build_attachments(result)` in a fixed render-priority order. |
| Narrate | `stages/narrate.py` | Response LLM → `assistant_message` + LLM-driven card/cart filtering. |
| Persist | `stages/persist.py` | Per-provider `merge()` of session state + non-blocking side-channels (leadgen, message store). |

Full design + the parity traps to preserve: [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md).
Async-first rationale: [ADR-0003](docs/architecture/adr/0003-async-first-pipeline.md).

---

## 5. Providers (the plugin model)

A provider is a plugin implementing the `Provider` protocol (`core/providers/base.py`):
`discover_tools()`, `run(calls, ctx)`, `build_attachments(result)`, `merge(slice_, result)`.
`McpProvider` / `HttpProvider` supply most of it so a concrete plugin stays ~small. Planning is the
base's job (generated preamble + the plugin's `planner.md` + `planner_context`).

**MCP providers** (`core/providers/plugins/`) — all six are clean `McpProvider`s, live-validated:

| id | folder | surfaces | cards |
|----|--------|----------|-------|
| `zomato` | `zomato/` | restaurant/menu search, cart, checkout | restaurant, menu, cart, payment QR |
| `swiggy_food` | `swiggy_food/` | restaurant/menu search, cart | restaurant, menu, cart |
| `swiggy_instamart` | `swiggy_instamart/` | grocery search, cart | product grid, cart |
| `zepto` | `zepto/` | grocery search, cart | product grid, cart |
| `district` | `district/` | dining/movies/theatres/events/venues/artists, table booking | typed discovery + reservation |
| `kite` | `kite/` | quotes/holdings/positions/orders/margin (Zerodha); two-step order confirm | instrument/holdings/positions/margin |

**HTTP providers** (statically-declared tools): `twilio`, `maps`, `calendar`, `whatsapp`.

**How MCP connection works:** `core/providers/mcp/remote.py` launches the `mcp-remote` stdio proxy
(`node_modules/mcp-remote/dist/proxy.js`) against the provider URL; OAuth tokens are vaulted in
Secrets Manager `companion/mcp-tokens` (shared across all envs — hardcoded). Kite uses **TOOL auth**
(a `login` tool returns a Zerodha URL the user visits; session-scoped).

**Add a provider:** see [CONTRIBUTING.md → Adding a provider](CONTRIBUTING.md#adding-a-provider-the-pluggable-path).
You create `core/providers/plugins/<id>/` and add one line to `core/registry/bootstrap.py`.

---

## 6. Knowledge graph & leadgen

A decoupled, async subsystem (issue #28) behind two protocols in `core/boundaries/`
(`TurnEventPublisher`, `ProfileReadService`) — the pipeline imports neither the queue nor the graph
client directly.

- **Write path:** `Persist` fire-and-forget enqueues a turn event to **SQS**; the EC2 graph worker
  (`deploy/graph-ec2/worker.py`) long-polls, runs LLM extraction (facts/interests/leads), updates
  **Neo4j**, and writes projections to the DynamoDB caches (`companion-graph-ProfileCache…` /
  `…LeadCache…`).
- **Read path:** `Personalize` reads the profile cache (fail-open) to inject facts + resolve
  home/office addresses.
- The Neo4j/graph EC2 box only exists when `DEPLOY_EC2=true` (prod). Dev/staging run with the KG
  seam stubbed/fail-open.

Decouple rationale: [ADR-0004](docs/architecture/adr/0004-decouple-kg-leadgen.md).

---

## 7. Config & feature flags

| Flag / env | Default | Effect |
|------------|---------|--------|
| `COMPANION_ENV` | `prod` | CDK env selector (`dev`/`staging`/`prod`) → stack names, resource prefix, domain. |
| `DEPLOY_EC2` | `false` | `=true` provisions the Neo4j/graph EC2 box (prod only). |
| `APP_SECRET_ID` | per-env | Secrets Manager id for LLM/API keys (`companion[-env]/app`). |
| `NODE_BINARY` | `node` | node used to spawn the mcp-remote proxy. |

Secrets (Secrets Manager): `companion/app` (LLM keys), `companion/mcp-tokens` (OAuth vault, shared),
plus `*/neo4j-key`, `*/graph-service-token`. App config resolves via `companion_api/secrets.py`.

---

## 8. Local dev, test, lint

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && npm install        # npm: mcp-remote proxy

cp .env.example .env                                   # add at least one LLM key
uvicorn companion_api.main:app --reload --port 8000    # run the API

# gates (what CI runs on core/)
ruff check core && ruff format --check core
python scripts/check_file_size.py core                 # ≤250 lines
pytest tests/core -q                                   # the new suite
```

Test markers (`pyproject.toml`): `unit` (fast, no I/O), `integration` (mocked external),
`component` (per-provider vs recorded fixtures), `live` (real services — never in CI).
Run e.g. `pytest -m "component and not live"`.

---

## 9. Branches, CI, environments, deploy

Promotion is one-directional (full rules in [CONTRIBUTING.md](CONTRIBUTING.md)):

```
feature/* ──PR──> dev ──promote──> staging ──promote──> main
                  (integration)    (pre-prod mirror)    (prod)
```

- **CI** (`.github/workflows/ci.yml`) on PRs into dev/staging/main: ruff + 250-line gate + pytest + frontend build.
- **Deploy** is CDK via GitHub Actions: `deploy.yml` (push to `main` → prod), `deploy-staging.yml`
  (push to `staging` → `companion-staging-*`). Each env is isolated (prefixed stacks/tables); only
  prod has a custom domain + EC2. DynamoDB is `PAY_PER_REQUEST` (on-demand), so non-prod envs idle ≈ $0.
- **Manual CDK:** `cd infra && cdk deploy --all` (uses `COMPANION_ENV` to pick the env).

Env parameterization: [ADR-0005](docs/architecture/adr/0005-branching-ci-environments.md).

---

## 10. "Where do I look if I want to…"

| I want to… | Look at |
|------------|---------|
| Add a new provider | `core/providers/plugins/<id>/` + `core/registry/bootstrap.py`; [CONTRIBUTING](CONTRIBUTING.md#adding-a-provider-the-pluggable-path) |
| Change how a card renders | that provider's `cards.py` / `*_cards.py` in its plugin folder |
| Change routing / which provider handles a task | `core/pipeline/stages/route.py` |
| Change the planner prompt | `core/middleware/planner_prompt.py` (generic preamble) or the plugin's `planner.md` / `planner_context.py` |
| Touch session state (cart/menu/restaurant memory) | the provider's `session.py` / `merge()` |
| Add/modify an HTTP route | `companion_api/routers/*.py` |
| Work on voice | `companion_api/voice_routes.py` |
| Change MCP connection / token handling | `core/providers/mcp/remote.py`, `connection.py` |
| Change infra (tables, Lambda, domain) | `infra/companion_stack.py`, `infra/graph_stack.py`, `infra/app.py` |
| Understand the KG/leadgen flow | `core/boundaries/`, `leadgen/`, `deploy/graph-ec2/`, [ADR-0004](docs/architecture/adr/0004-decouple-kg-leadgen.md) |

---

## 11. Deeper docs & links

- **Architecture (design of record):** [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md)
- **ADRs:** [strangler rewrite](docs/architecture/adr/0001-strangler-rewrite-on-dev.md) ·
  [unified tool schema + registry](docs/architecture/adr/0002-unified-tool-schema-and-registry.md) ·
  [async-first pipeline](docs/architecture/adr/0003-async-first-pipeline.md) ·
  [decouple KG/leadgen](docs/architecture/adr/0004-decouple-kg-leadgen.md) ·
  [branching + CI + environments](docs/architecture/adr/0005-branching-ci-environments.md)
- **Contributing (branches, conventions, adding a provider):** [`CONTRIBUTING.md`](CONTRIBUTING.md)
- **Infra:** [`infra/README.md`](infra/README.md)
- **Umbrella issue:** #26 (architecture rewrite). Related: #27 (unified schema), #28 (KG decouple),
  #29 (web decomposition), #24 (branching/CI), #14 (provider-agnostic routing).
