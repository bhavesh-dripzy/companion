# Companion — Lead-Generation & User Knowledge-Graph Plan

> **Status:** Proposed (awaiting build sign-off)
> **Author:** drafted with Claude Code, 2026-05-30
> **Pivot:** Companion is going *free-for-all* and monetizing via **lead generation**.
> This document is the architecture + implementation plan for (a) a per-user
> **knowledge graph** ("digital profile" + interests + relationships) and (b) a
> **lead store** mined from conversations, plus the agent changes that make the
> assistant *use* that memory (e.g. auto-confirming delivery to your saved office).

---

## 0. TL;DR

- **Graph = brain, DynamoDB = cache.** A self-managed **Neo4j Community Edition** on a
  single **EC2** instance is the source of truth for the user knowledge graph + leads.
  A **DynamoDB "profile-cache" table** holds a denormalized hot projection the
  user-facing Lambda reads on the request path (fast, no VPC, no EC2 on the hot path).
- **Extraction is asynchronous.** Each conversation turn is persisted to DynamoDB and an
  event is pushed to **SQS**. An **extraction worker running on the EC2** long-polls SQS,
  runs an LLM to extract entities/relations/leads, upserts them into Neo4j, and
  write-through-updates the DynamoDB cache. The user never waits on extraction.
- **Agent gets a memory.** `run_agent_turn()` gains a **profile-read hook** that reads the
  DynamoDB cache and injects known facts (office/home address, food preferences, open
  leads) into the planner/responder — so "order lunch to office" auto-resolves to
  *"Sector 13, DLF, Gurugram"* and "looking at ACs" surfaces/links a lead.
- **No VPC on the hot path.** The API Lambda stays out of any VPC. Only the EC2's
  graph-service (token + TLS, on a `houzzat.in` subdomain) and the on-box worker touch
  Neo4j. Neo4j's Bolt port is never exposed to the internet.
- **Scope now:** *Foundation + Personalization*. Monetization (lead scoring, dedup at
  scale, merchant export) is schema-scaffolded but built later.
- **Consent-ready, DPDP-aware:** every profile fact and lead carries consent metadata;
  profiling runs internally from first use, but **no lead may be exported/sold until
  `consent_status = granted`.**

---

## 1. Accounts & Access (already set up)

| Thing | Value |
|---|---|
| Org management account | `314359811237` (root login lives here; has `carqr-*` projects) |
| **Companion account** | **`616551057455`** = `companion-dev` (member account) |
| Org id | `o-3l3nevgp99` |
| Region | `ap-south-1` (Mumbai) |
| Deployed stack | `companion-serverless` |
| Route 53 zone | `houzzat.in` (in companion-dev) |

**Access method (cross-account assume-role):** root cannot assume IAM roles, so we created
a least-privilege deployer in the management account:

- IAM user `companion-deployer` in `314359811237`, inline policy allows **only**
  `sts:AssumeRole` on `arn:aws:iam::616551057455:role/OrganizationAccountAccessRole`.
- `~/.aws/config` profile **`companion`** → `role_arn = …/OrganizationAccountAccessRole`,
  `source_profile = companion-source`, `region = ap-south-1`.
- `~/.aws/credentials` profile **`companion-source`** holds the deployer's access key.
- **Usage:** `aws --profile companion …` transparently operates inside `companion-dev`.
- Verified: `aws --profile companion sts get-caller-identity` → account `616551057455`.

> Security note: the management-account `default` profile is **root** — recommend
> replacing it with a non-root IAM admin/SSO profile and rotating root keys.

---

## 2. Goal

1. **Digital profile per user** as a graph: who they are (age, language, city), durable
   attributes (works at *Sector 13, DLF, Gurugram*; lives at …), and interests (food/
   cuisines, electronics research, furniture, salon/services, travel).
2. **Leads** mined from conversation: e.g. "looking at ramen on Zomato" → food lead;
   "researching a 1.5-ton AC" → electronics lead; "salon near me" → services lead.
   AC/TV/furniture/salon have **no fulfillment path today** → they are *pure leads*.
3. **Memory the LLM can query and act on:** at confirmation time, reuse stored facts
   ("deliver to your office in Sector 13?"); personalize discovery to known interests.
4. **Monetization-ready** lead store (category, intent, product specifics, budget signal,
   recency, location, consent) for later routing to merchants/affiliate networks.

---

## 3. Current Architecture (verified)

- **Compute:** FastAPI app (`companion_api/main.py`, ~65 KB) wrapped with Mangum on a
  **single Lambda, *not* in a VPC** (Docker image, Python 3.12 + Node 20, 1024 MB / 120 s).
  A second 512 MB Lambda handles scheduled reminders. API Gateway HTTP API at `houzzat.in`.
- **Data:** **DynamoDB**, 4 tables (PAY_PER_REQUEST, PITR):
  - `companion-families` — PK `family_id`
  - `companion-members` — PK `member_id`; GSIs `phone-number-index`, `family-index`
  - `companion-messages` — PK `member_id`, SK `created_at_message_id` — **defined in CDK
    but never written to** (no repository exists). ← we will finally use it.
  - `companion-reminders` — PK `member_id`, SK `reminder_id`
  Repository pattern in `companion_api/dynamodb.py` (`ReminderRepository`,
  `MemberRepository`); `_resource()`, `_json_safe()`, `utc_now_iso()` helpers.
- **Auth:** Cognito (JWKS-verified JWT) or legacy HS256. `get_current_user()` returns a
  **`username` string** (`claims["username"]` or `sub`); that string is the `user_id`
  passed into the agent. (No email/phone surfaced to the agent today.)
- **Agent loop** (`app/services/agents/pipeline.py:run_agent_turn`, line 907):
  - signature: `run_agent_turn(*, user_message, address_id, execute_tools, user_id, llm,
    conversation_history=None, user_geo=None, session_state=None, session_restaurants=…,
    session_addresses=…, session_movies=…, session_theatres=…, session_menu=…,
    pending_variant_choice=…) -> dict`
  - flow: **orchestrator** LLM classifies `task_type` → **domain planner** builds a
    `tool_plan` → **MCP executor** runs provider tools → **response LLM** writes
    `assistant_message`. Returns an envelope: `assistant_message`, `routed_provider`,
    `tool_execution`, `attachments`, and all `session_*` echoed back.
  - `task_type ∈ {food_delivery, going_out, finance_investing, health_nutrition,
    normal_query, reminder_calendar, maps_query}` (orchestrator `_KNOWN_TASKS` +
    `prompts/orchestrator.system.md`).
- **Integrations (MCP):** Zomato, Swiggy Food, Swiggy Instamart, Zepto, District
  (going-out/movies), Google Maps, Google Calendar, WhatsApp; finance (Kite/Groww) and
  calorie tracker are stubs.
- **The gap:** there is **no persistent memory**. The old Django `Memory` model was
  **deleted** in migration 0002. All context (`session_state`, `session_addresses`,
  `session_menu`, history) is **ephemeral**, round-tripped through the client and lost on
  refresh. Identity is fragmented: web = Cognito `username`, WhatsApp = `phone → member_id`.

---

## 4. Key Decisions & Rationale

| Decision | Choice | Why |
|---|---|---|
| Graph engine | **Neo4j Community on EC2** (self-managed) | Your directive. Cypher is the most LLM-friendly query language (text→Cypher tool), huge ecosystem, free. Alternatives noted: FalkorDB (Redis-module, fast, smaller footprint), Memgraph. |
| Why not Amazon Neptune | **Ruled out** | Requires a VPC → NAT (~$32/mo) + Lambda cold-start ENI penalty; breaks the no-VPC hot path. |
| Hot-path store | **DynamoDB profile-cache** | Lambda-native, no VPC, single-digit-ms reads, ~$5–25/mo. The agent reads the cache, never Neo4j, on the request path. |
| Graph connectivity | **EC2 worker + token/TLS HTTPS graph-service** | Keeps API Lambda out of any VPC. Neo4j Bolt stays bound to localhost; only the on-box worker talks to it. Admin/lead queries hit the graph-service over HTTPS. |
| Async transport | **SQS** | Simple for a self-managed EC2 consumer to long-poll (vs. DynamoDB Streams + KCL). Decouples extraction latency/cost from the user reply. |
| Extraction model | **Gemini 2.5 Flash-Lite** (primary) via existing `llm_config` fallback chain | Already wired, cheap, structured-output capable. DeepSeek/OpenAI as fallback. |
| Scope (now) | **Foundation + Personalization** | Your whole brief *is* personalization; monetization needs graph data to exist first. |
| Consent | **Consent-ready schema; gate export** | India DPDP-safe; capture internally now, block selling until opt-in. |

---

## 5. Target Architecture

```
                         ┌──────────────────────────────────────────┐
        user ───────────▶│  API Gateway → API Lambda  (NO VPC)       │
   (web / WhatsApp)       │                                          │
                          │  run_agent_turn()                        │
                          │   1. profile-read hook ──┐               │
                          │   2. orchestrator→planner→executor→reply │
                          │   3. persist turn ───────┼──┐            │
                          │   4. enqueue turn event ─┼──┼──┐         │
                          └──────────────────────────┼──┼──┼─────────┘
                                                      │  │  │
                       reads cache (fast) ◀───────────┘  │  │
                                                          │  │
                ┌──── DynamoDB ───────────────────────────▼──┼───────┐
                │  profile-cache   (hot facts agent reads)   │       │
                │  messages        (durable turn log) ───────┘       │
                │  lead-cache      (open-lead projection)            │
                │  families/members/reminders (existing)             │
                └────────────────────────────────────────┬──────────┘
                                                          │ enqueue
                                                   ┌──────▼───────┐
                                                   │   SQS queue  │
                                                   │ (turn events)│
                                                   └──────┬───────┘
                                                          │ long-poll
   EC2  (companion-dev, ap-south-1, public IP/EIP)        │
   graph.houzzat.in (Caddy auto-TLS :443)  ───────────────┼──────────────┐
   ┌──────────────────────────────────────────────────────▼────────────┐ │
   │  extraction-worker (python):                                       │ │
   │    SQS msg → LLM extract (entities/relations/leads)                │ │
   │           → upsert Neo4j  → write-through DynamoDB cache           │ │
   │  graph-service (FastAPI, token auth, behind Caddy):                │ │
   │    /profile/{uid}  /leads  /query (text→Cypher)  /health           │◀┘ admin / lead consumers
   │  Neo4j Community (Bolt 127.0.0.1:7687, browser 7474 localhost)     │
   └────────────────────────────────────────────────────────────────────┘
```

**Request-path latency added: ~0** (one extra DynamoDB read for the profile + two
fire-and-forget writes). Extraction happens entirely off-path on the EC2.

---

## 6. Data Models

### 6.1 Canonical identity

Introduce a **`canonical_user_id`** to unify web (`username`/Cognito `sub`) and WhatsApp
(`member_id`/phone):

- v1: use the existing `user_id` string from `run_agent_turn` (Cognito username/sub) as the
  canonical key. Store auxiliary identifiers (`email`, `phone`, `member_id`, `family_id`)
  as `:User` node properties + a `cache` record, enabling later identity merge.
- Identity-resolution step in the worker: if a turn arrives with a phone (WhatsApp) that
  matches a `:User.phone`, attach to that user; else create/merge by canonical id.

### 6.2 Neo4j graph schema (source of truth)

**Node labels**

- `:User { id (canonical), username, email?, phone?, member_id?, family_id?, name?, age?,
  city?, language?, created_at, updated_at }`
- `:Place { id, kind: home|office|other, label, address_line, lat?, lng?, provider_address_id?,
  normalized_key }`  ← canonical address node (dedup target for "my office")
- `:Category { name }`  (food, grocery, electronics, furniture, salon_services, travel_cab, …)
- `:Interest { id, category, detail }` (e.g. category=food, detail="ramen")
- `:Lead { id, category, status: open|qualified|won|lost|expired, product, attrs (json),
  budget_signal?, intent_score?, confidence, source_provider?, created_at, last_seen_at,
  consent_status }`
- `:Brand`/`:Product` (optional, later) for richer electronics/furniture leads.

**Relationships** (all carry `since`, `confidence`, `valid_from`, `valid_to?`, `source`)

- `(:User)-[:WORKS_AT]->(:Place)` , `(:User)-[:LIVES_AT]->(:Place)`
- `(:User)-[:INTERESTED_IN { weight }]->(:Category)`
- `(:User)-[:LIKES]->(:Interest)`  (fine-grained: cuisines, dish types)
- `(:User)-[:GENERATED]->(:Lead)` , `(:Lead)-[:IN_CATEGORY]->(:Category)`
- `(:Lead)-[:DELIVER_TO]->(:Place)` (when a place is implied)

**Constraints / indexes** (run once on bootstrap)

```cypher
CREATE CONSTRAINT user_id     IF NOT EXISTS FOR (u:User)     REQUIRE u.id IS UNIQUE;
CREATE CONSTRAINT place_key   IF NOT EXISTS FOR (p:Place)    REQUIRE (p.id) IS UNIQUE;
CREATE CONSTRAINT category_nm IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE;
CREATE CONSTRAINT lead_id     IF NOT EXISTS FOR (l:Lead)     REQUIRE l.id IS UNIQUE;
CREATE INDEX place_norm       IF NOT EXISTS FOR (p:Place)    ON (p.normalized_key);
CREATE INDEX lead_cat_status  IF NOT EXISTS FOR (l:Lead)     ON (l.category, l.status);
```

**Temporal validity / fact change** ("Aashish changed jobs"): never hard-delete. Set
`valid_to = now()` on the old relationship and create a new one with `valid_from = now()`.
Reads default to `WHERE r.valid_to IS NULL`.

**Example queries**

```cypher
// Agent: what's the user's office address?  (worker keeps cache in sync; this is the truth)
MATCH (u:User {id:$uid})-[r:WORKS_AT]->(p:Place) WHERE r.valid_to IS NULL RETURN p;

// Leads: all open AC leads in Gurugram, freshest first
MATCH (u:User)-[:GENERATED]->(l:Lead {category:'electronics', status:'open'})
WHERE l.product CONTAINS 'AC' AND u.city = 'Gurugram'
RETURN u.id, l ORDER BY l.last_seen_at DESC;
```

### 6.3 DynamoDB `companion-profile-cache` (hot projection the Lambda reads)

Single table, one item per user (plus optional per-section items):

```
PK = USER#<canonical_user_id>     SK = PROFILE
{
  user_id, name, age, city, language,
  addresses: {                     # canonical places for instant confirmation
    office: { label, address_line, lat, lng, provider_address_id },
    home:   { ... }
  },
  interests: [ {category, weight, details:[...]}, ... ],   # top-N
  open_leads: [ {lead_id, category, product, status, last_seen_at}, ... ],  # top-N
  consent_status: implicit|granted|withdrawn,
  updated_at
}
```

- **Written by:** the EC2 worker (write-through after each Neo4j upsert).
- **Read by:** the API Lambda profile-read hook (1 `get_item`, single-digit ms).
- Bounded size (top-N interests/leads) to stay tiny and fast.

### 6.4 `companion-messages` (finally wired) + SQS turn event

Persist each turn (fixes the unused table) and emit an event:

```
messages item: PK=USER#<uid>  SK=<iso_ts>#<uuid>
  { role, text, intent/task_type, provider, metadata(json), created_at }

SQS message (turn event):
  { canonical_user_id, username, member_id?, phone?, turn_id,
    user_message, assistant_message, task_type, routed_provider,
    session_addresses?, tool_summary?, geo?, ts }
```

### 6.5 `companion-lead-cache` (optional split) / leads in graph

Leads live in Neo4j (truth) + a DynamoDB projection for admin/export queries. A GSI on
`(category, status)` supports "all open electronics leads". For v1 we can fold open leads
into the profile-cache `open_leads` and add a dedicated `lead-cache` table in the
monetization phase.

**Sellable lead fields (target):** `category, product/specifics, budget_signal, location,
intent_score, confidence, recency(last_seen_at), source_provider, consent_status,
withdrawal_token`.

---

## 7. Extraction Pipeline (on EC2 worker)

For each SQS turn event:

1. **Load context:** recent turns for the user (from `messages`) + current graph snapshot
   (so the LLM can dedup against known facts).
2. **LLM extract** (Gemini Flash-Lite, structured output) → JSON:
   ```json
   {
     "profile_facts": [
       {"type":"work_address","value":"Sector 13, DLF, Gurugram","confidence":0.9},
       {"type":"age","value":"30","confidence":0.6}
     ],
     "interests": [{"category":"food","detail":"ramen","weight":0.7}],
     "leads": [
       {"category":"electronics","product":"1.5 ton split AC","budget_signal":"mid",
        "intent_score":0.8,"confidence":0.85}
     ]
   }
   ```
3. **Entity resolution / dedup:** normalize addresses (lowercase, strip punctuation) →
   `normalized_key`; `MERGE` `:Place` on it so "my office" is one node. Merge interests by
   `(category, detail)`. Merge open leads by `(category, product-fingerprint)` within a
   freshness window (don't create a new AC lead every turn — update `last_seen_at`).
4. **Temporal update:** on conflicting facts (new work address), retire the old edge
   (`valid_to=now()`), add the new one.
5. **Confidence gate:** write facts with `confidence ≥ 0.6`; queue low-confidence for a
   second-pass re-prompt (optional, later).
6. **Upsert Neo4j** (Cypher `MERGE`), then **write-through** the DynamoDB profile-cache.
7. **Idempotency:** keep `turn_id`; skip already-processed events (SQS at-least-once).

---

## 8. Agent Integration (code changes)

All in the existing app; the Lambda never calls Neo4j.

### 8.1 Profile-read hook (personalization)
- **File:** `app/services/agents/pipeline.py`, in `run_agent_turn` after `new_turn()` /
  history normalization and **before** orchestrator classification.
- **Action:** `profile = ProfileCacheRepository().get(user_id)` (new repo, §8.4). Build a
  compact "known facts" block and:
  - inject into the **orchestrator/planner/responder** prompt context (so replies are
    interest-aware);
  - pre-resolve **delivery address**: if the message implies "to office/home" and no
    `address_id` is set, use `profile.addresses.office.provider_address_id` and surface a
    confirmation ("Deliver to your office — Sector 13, DLF, Gurugram?").
- **Latency:** one DynamoDB `get_item`. Fail-open (no profile → today's behavior).

### 8.2 Persist-turn + enqueue hook (foundation)
- **File:** `companion_api/main.py` — the `/api/agent/query/` and `/api/food/query/`
  handlers (which call `run_agent_turn`), **after** the turn returns.
- **Action:** write user+assistant messages to `companion-messages` (new
  `MessageRepository`) and `sqs.send_message(...)` the turn event. Wrap in try/except so a
  failure never breaks the user response. (Could also be a thin wrapper in `pipeline.py`.)

### 8.3 New modules
```
companion_api/profile_cache.py          # DynamoDB ProfileCacheRepository (read on hot path)
companion_api/messages_repo.py          # DynamoDB MessageRepository (persist turns)
companion_api/leadgen_queue.py          # SQS enqueue helper
app/services/leadgen/schema.py          # Pydantic: ProfileFact, Interest, Lead, TurnEvent
app/services/leadgen/extractor.py       # LLM extraction (shared; runs on EC2 worker)
app/services/leadgen/graph_client.py    # Neo4j driver wrapper + Cypher upserts (EC2)
deploy/graph-ec2/worker.py              # SQS poller → extractor → graph → cache
deploy/graph-ec2/graph_service.py       # FastAPI: /profile /leads /query /health
deploy/graph-ec2/cloud-init.yaml        # bootstrap (Neo4j + worker + service + Caddy)
deploy/graph-ec2/Caddyfile              # graph.houzzat.in reverse proxy, auto-TLS
```

### 8.4 Repository pattern (matches existing `dynamodb.py`)
```python
class ProfileCacheRepository:
    def __init__(self, table_name: str | None = None):
        self.table = _resource().Table(table_name or settings.PROFILE_CACHE_TABLE)
    def get(self, user_id: str) -> dict | None:
        r = self.table.get_item(Key={"PK": f"USER#{user_id}", "SK": "PROFILE"})
        return _json_safe(r.get("Item")) if r.get("Item") else None
    def put(self, user_id: str, profile: dict) -> None:
        self.table.put_item(Item={"PK": f"USER#{user_id}", "SK": "PROFILE",
                                  **profile, "updated_at": utc_now_iso()})
```
- **`companion_api/settings.py`:** add `PROFILE_CACHE_TABLE`, `MESSAGES_TABLE` (exists),
  `LEADGEN_QUEUE_URL`, `GRAPH_SERVICE_URL`, `GRAPH_SERVICE_TOKEN_SECRET`.

---

## 9. EC2 Provisioning (self-managed graph)

| Setting | Value |
|---|---|
| AMI | Amazon Linux 2023 (arm64) via SSM latest param |
| Instance | **t4g.medium** (4 GB; comfortable for Neo4j heap+pagecache + worker + Caddy). Budget: t4g.small (2 GB). |
| Storage | 20–30 GB gp3 EBS (encrypted) |
| Network | default VPC, public subnet, **Elastic IP** |
| DNS | Route 53 A record `graph.houzzat.in` → EIP |
| Ports (SG) | inbound **443** (graph-service via Caddy) from anywhere or AWS ranges; **22** SSH from your IP only; **Bolt 7687 / 7474 NOT exposed** (localhost only) |
| IAM instance profile | `sqs:ReceiveMessage/DeleteMessage` on the queue; `dynamodb` r/w on cache+messages; `secretsmanager:GetSecretValue` on `companion/app`, `companion/graph-*` |

**cloud-init bootstrap:**
1. Install Docker + docker-compose (or podman).
2. Run **Neo4j Community** container: Bolt bound `127.0.0.1:7687`, browser `127.0.0.1:7474`,
   auth password from Secrets Manager, volumes on EBS. Apply schema constraints (§6.2).
3. Deploy **worker** + **graph-service** (systemd units or compose), env from Secrets
   Manager (LLM keys from `companion/app`, Neo4j creds, graph-service bearer token).
4. Install **Caddy** with `Caddyfile` → auto-TLS for `graph.houzzat.in`, reverse-proxy to
   the local graph-service; require `Authorization: Bearer <token>`.
5. Health checks + CloudWatch agent (basic logs/metrics).

> Neo4j Community = single instance, no clustering/HA. Acceptable for v1 (truth can be
> rebuilt from the `messages` log). Enable EBS snapshots + a nightly `neo4j-admin dump` to S3.

---

## 10. Infrastructure-as-Code (CDK additions)

In `infra/companion_stack.py`:

1. **DynamoDB** `companion-profile-cache` (PK `PK`, SK `SK`, PAY_PER_REQUEST, PITR). Later:
   `companion-lead-cache` with GSI `(category,status)`.
2. **SQS** `companion-leadgen-turns` (+ DLQ, visibility timeout > worker max processing).
3. **EC2**: instance, instance profile/role, security group, Elastic IP, Route 53 A record;
   user-data = cloud-init. (Alternatively a separate `infra/graph_stack.py` to keep the
   serverless stack lean.)
4. **Secrets**: `companion/graph-service` (bearer token), `companion/neo4j` (password).
5. **Grants/env on the API Lambda**: read/write `profile-cache` + `messages`, send to SQS;
   env `PROFILE_CACHE_TABLE`, `LEADGEN_QUEUE_URL`, `GRAPH_SERVICE_URL`,
   `GRAPH_SERVICE_TOKEN_SECRET`.
6. **EC2 role grants**: SQS consume, DynamoDB r/w (cache+messages), Secrets read.

```python
# sketch
profile_cache = self._table("ProfileCache", f"{p}-profile-cache", partition_key="PK", sort_key="SK")
turns_q       = sqs.Queue(self, "LeadgenTurns", queue_name=f"{p}-leadgen-turns",
                          visibility_timeout=Duration.seconds(120),
                          dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=5, queue=dlq))
profile_cache.grant_read_write_data(api_function); turns_q.grant_send_messages(api_function)
api_function.add_environment("PROFILE_CACHE_TABLE", profile_cache.table_name)
api_function.add_environment("LEADGEN_QUEUE_URL", turns_q.queue_url)
```

---

## 11. Networking & Security

- API Lambda stays **non-VPC**; it only touches DynamoDB + SQS (both via public AWS
  endpoints) + the existing LLM/MCP egress. No NAT, no cold-start ENI penalty.
- **Neo4j Bolt never leaves the box** (bound to localhost). Only the on-box worker uses it.
- **graph-service** is the *only* externally reachable graph surface: HTTPS only (Caddy
  auto-TLS), **bearer-token** auth (token in Secrets Manager), least-privilege endpoints.
- Secrets in **Secrets Manager** (`companion/app`, `companion/neo4j`, `companion/graph-service`);
  EC2 reads via instance role, Lambda via its role.
- **DPDP / consent:** `consent_status` on user + every lead; export endpoints **refuse**
  records unless `granted`; `withdrawal_token` + a purge path (delete user subgraph + cache).
  Audit trail via the immutable `messages` log.
- SSH restricted to your IP; consider **SSM Session Manager** instead of opening 22.

---

## 12. Cost Estimate (low scale)

| Item | ~Monthly |
|---|---|
| EC2 t4g.medium (on-demand, ap-south-1) | ~$24 (≈$10 if t4g.small / Savings Plan) |
| EBS 30 GB gp3 | ~$3 |
| Elastic IP (attached) | $0 |
| SQS + DynamoDB (cache/messages, low traffic) | ~$1–5 |
| Neo4j Community license | $0 |
| LLM extraction (Gemini Flash-Lite, per turn) | usage-based, ~cents/day at low volume |
| **Total infra** | **~$30/mo** + LLM usage |

vs. Neptune (~$140–380/mo) or Neo4j Aura (~$150–250/mo). Self-managed EC2 is the cheapest
path that still gives a *real* graph DB.

---

## 13. Phased Roadmap

### Phase 0 — Access ✅ (done)
- Cross-account `companion` profile assuming `OrganizationAccountAccessRole`. Verified.

### Phase 1 — Foundation (graph + cache + pipeline, agent unchanged)
1. CDK: `profile-cache` table, SQS queue + DLQ, secrets. `cdk deploy`.
2. Provision EC2 (Neo4j + worker + graph-service + Caddy), `graph.houzzat.in` DNS + TLS.
3. App: `MessageRepository` (wire `companion-messages`), SQS enqueue after `run_agent_turn`.
4. Worker: SQS → extractor → Neo4j upsert → cache write-through. Schema constraints applied.
5. Read APIs: `GET /api/profile/me` (Lambda reads cache), `GET /api/leads` (admin → graph-service).
6. **Exit criteria:** chat a few turns → facts/interests/leads appear in Neo4j browser and
   in the DynamoDB cache; messages table populated; no added user-visible latency.

### Phase 2 — Personalization (the "it remembers me" magic)
1. Profile-read hook in `run_agent_turn`; inject known facts into prompts.
2. Address auto-resolution + confirmation ("deliver to your Sector 13 office?").
3. Interest-aware discovery (bias suggestions to known cuisines/categories).
4. Identity unification (link WhatsApp phone ↔ web user).
5. **Exit criteria:** ordering "to office" without restating the address; interest-aware replies.

### Phase 3 — Monetization (later)
1. `lead-cache` table + GSI; lead scoring (intent/engagement/freshness), dedup, expiry.
2. Consent opt-in UI + per-merchant sharing controls; export gated on `granted`.
3. Admin lead dashboard; export/webhook routing to merchants/affiliate networks (e.g. Cuelinks).

---

## 14. Testing & Verification

- **Unit:** repositories (mock DynamoDB via moto), extractor (golden conversation →
  expected facts/leads), Cypher upserts (ephemeral Neo4j test container), address
  normalization/dedup, temporal retire-on-change.
- **Integration:** post a turn → assert message persisted, SQS message sent, worker writes
  graph + cache; profile-read hook injects facts.
- **Manual (`/verify`):** run the app, hold a food convo mentioning office address, confirm
  next order auto-suggests it; mention "researching an AC", confirm a lead node appears.
- **Pre-deploy:** `pytest`; `cdk diff` reviewed before `cdk deploy --profile companion`.

---

## 15. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Single Neo4j instance (no HA) | Truth rebuildable from `messages` log; EBS snapshots + nightly dump to S3. |
| EC2 is a server to patch/run (vs. serverless) | Minimal surface (only 443/Caddy public, SSM for admin); SSM Patch Manager; restart-safe systemd. |
| graph-service exposed on internet | TLS + bearer token + least-priv endpoints; Bolt never exposed. |
| LLM extraction errors/hallucination | Structured output + confidence gate + dedup; idempotent upserts; validate against tool results. |
| Duplicate "office" nodes | `normalized_key` + `MERGE`; canonical Place node. |
| Identity fragmentation (web vs WhatsApp) | canonical_user_id + phone/member_id properties + resolution step. |
| SQS at-least-once double-processing | `turn_id` idempotency keys. |
| DPDP/consent for selling leads | consent fields + export gate + withdrawal/purge + audit log. |
| Cost creep at scale | top-N bounded cache; move heavy traversals to graph; revisit instance size. |

---

## 16. Pre-flight Verification Checklist (run before building)

Read-only discovery I had queued when we paused — confirm against live infra:

```bash
P="aws --profile companion"
$P lambda list-functions --query 'Functions[].{Name:FunctionName,Vpc:VpcConfig.VpcId,Mem:MemorySize,Timeout:Timeout}' --output table
$P dynamodb list-tables
$P ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[].VpcId'
$P ec2 describe-subnets --filters Name=default-for-az,Values=true --query 'Subnets[].{S:SubnetId,AZ:AvailabilityZone,Pub:MapPublicIpOnLaunch}'
$P route53 list-hosted-zones --query 'HostedZones[].Name'
$P ec2 describe-key-pairs --query 'KeyPairs[].KeyName'
$P ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 --query 'Parameter.Value' --output text
$P secretsmanager list-secrets --query 'SecretList[].Name'
```
Confirm: API Lambda has **no VpcConfig**; exact table physical names; a default VPC +
public subnet exist; `houzzat.in.` zone present; an SSH key pair (or use SSM); latest
arm64 AL2023 AMI id.

---

## 17. Open Questions / Decisions Still Needed

1. **Instance size:** t4g.medium (recommended) vs t4g.small (budget)?
2. **Graph engine confirm:** Neo4j Community (recommended) vs FalkorDB (lighter) — any pref?
3. **EC2 admin access:** open SSH from your IP, or SSM Session Manager only (recommended)?
4. **Separate CDK stack** for the EC2/graph (`graph_stack.py`) vs. add to `companion-serverless`?
5. **"Free-for-all"**: are we keeping Cognito login (needed for stable per-user identity),
   or moving to anonymous/device identity? This affects the canonical user key.
6. **Lead consumers:** own sales team vs affiliate (Cuelinks) vs direct merchants — shapes
   the export format in Phase 3.

---

## 18. Appendix — File/Function Touch-List

| Area | File | Change |
|---|---|---|
| Persist turns | `companion_api/messages_repo.py` (new) | `MessageRepository` for `companion-messages` |
| Profile cache | `companion_api/profile_cache.py` (new) | `ProfileCacheRepository.get/put` |
| Enqueue | `companion_api/leadgen_queue.py` (new) | `send_turn_event()` to SQS |
| Settings | `companion_api/settings.py` | `PROFILE_CACHE_TABLE`, `LEADGEN_QUEUE_URL`, `GRAPH_SERVICE_URL`, secrets |
| Persist+enqueue hook | `companion_api/main.py` (`/api/agent/query/`, `/api/food/query/`) | after `run_agent_turn`, persist + enqueue (try/except) |
| Profile-read hook | `app/services/agents/pipeline.py` (`run_agent_turn`, ~after `new_turn()`) | read cache, inject facts, address auto-resolve |
| Extraction (shared) | `app/services/leadgen/{schema,extractor,graph_client}.py` (new) | LLM extract + Neo4j upsert |
| EC2 worker/service | `deploy/graph-ec2/{worker,graph_service,cloud-init.yaml,Caddyfile}` (new) | SQS poller + HTTPS graph API |
| Read APIs | `companion_api/main.py` | `GET /api/profile/me`, `GET /api/leads` (admin) |
| Infra | `infra/companion_stack.py` (or new `infra/graph_stack.py`) | cache table, SQS, EC2, EIP, DNS, secrets, grants, env |
| Frontend (Phase 2/3) | `frontend/src/SettingsModal.tsx`, `ChatLog.tsx` | profile/consent UI; lead dashboard |
```
