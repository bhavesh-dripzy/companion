# Companion — Serverless Food & Reminder Assistant

Companion is a multi-provider food-ordering and reminder-management API running on AWS as a fully serverless application, now integrated with a state-of-the-art **User Knowledge Graph** and **Automated Lead Generation Engine**.

---

## 1. Architecture

```
                                  ┌──────────────────────┐
                                  │   API Gateway (HTTP)  │
                                  └──────────┬───────────┘
                                             │
                                  ┌──────────▼───────────┐
                                  │   Lambda (FastAPI)    │
                                  │   Python 3.12 / Node  │
                                  └──┬───────┬───────┬───┘
                                     │       │       │
                        ┌────────────▼┐  ┌──▼──┐  ┌─▼──────────┐
                        │  DynamoDB    │  │Cognito│ │Secrets Mgr │
                        │  (6 tables)  │  │(auth) │ │(LLM keys)  │
                        └──────┬──────┘  └──────┘  └────────────┘
                               │
                        ┌──────▼───────────────┐
                        │   SQS Event Queue    │
                        └──────┬───────────────┘
                               │
                        ┌──────▼───────────────┐
                        │   EC2 Graph Worker   │
                        └──────┬───────────────┘
                               │
                        ┌──────▼───────────────┐
                        │   Neo4j (EC2 Graph)  │
                        └──────────────────────┘
```

- **API Gateway HTTP API** — Public HTTP endpoint with CORS, routing all traffic to the Lambda function.
- **Lambda (Serverless API)** — Runs the FastAPI app wrapped with Mangum. Handles chat sessions, prompt personalization, reminder generation, and MCP pairings.
- **DynamoDB tables** —
  - `families`, `members`, `messages`, `reminders` — Primary serverless transactional data.
  - `companion-profile-cache` — Stores user knowledge graph projections (facts, interests, active locations) for low-latency context injection.
  - `companion-lead-cache` — Holds commercial leads generated from user conversations, indexed via GSIs for high-throughput queries by merchant systems.
- **EventBridge Scheduler** — Invokes `scheduled_reminder_handler` to trigger reminders.
- **SQS Lead Generation Queue** — Asynchronously ingests conversational turn events containing messages, geo coordinates, and session metadata from WhatsApp and Web client turns.
- **EC2 Graph Stack (`t4g.small`)** — Hosts the **Neo4j graph database** and two key daemon processes:
  - **FastAPI Graph Service** — Serves DNS `graph.houzzat.in` over SSL (via Caddy proxy), exposing secure profile and query endpoints.
  - **SQS Background Worker** — Long-polls SQS, executes LLM extraction on new turns to identify facts/interests/leads, updates Neo4j, and writes projection updates to the DynamoDB caches.

---

## 2. Knowledge Graph & Leadgen Engine

The Knowledge Graph acts as the centralized user memory vault, enabling hyper-personalized assistant behavior.

### A. Graph Schema (Neo4j)
- **Nodes**:
  - `(u:User {id: user_id, name, age, city, language, phone})` — Central identity.
  - `(p:Place {id: place_id, address_line, lat, lng, kind})` — Locations (home/office).
  - `(c:Category {name: category_name})` — Intent classifications (food, electronics, salon, etc.).
  - `(i:Interest {id: interest_id, detail, category})` — Specific user preferences.
  - `(l:Lead {id: lead_id, product, budget_signal, intent_score, confidence, status, consent_status})` — Commercial leads.
- **Relationships**:
  - `(u)-[:LIVES_AT | WORKS_AT]->(p)` (with validity timestamps)
  - `(u)-[:INTERESTED_IN {weight}]->(c)`
  - `(u)-[:LIKES {weight}]->(i)`
  - `(u)-[:GENERATED]->(l)`

### B. Personalization Pipeline
At the start of every chat turn, the Lambda API fetches the user profile cache from `companion-profile-cache`.
- **Prompt Injection**: Injects user facts, preferred items, and active leads into the routing and planning LLM context.
- **Address Seeding**: Merges graph locations stickily into the session state so Zomato/Swiggy execution works out-of-the-box.
- **Home/Office Auto-Resolution**: Intercepts requests matching home/office keywords (e.g. "Order burger to office") and automatically overrides coordinates without manual pin selection.

---

## 3. Quickstart

```bash
# 1. Clone and install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install

# 2. Configure environment
cp .env.example .env
# Edit .env — add at least one LLM key (GEMINI_API_KEY recommended)

# 3. Run locally
uvicorn companion_api.main:app --reload --port 8000

# 4. Run tests
pytest
```

---

## 4. API Reference

All authenticated endpoints require `Authorization: Bearer <token>`.

### Auth & Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/` | No | Health check |
| GET | `/` or `/food/` | No | Food-agent SPA (HTML) |
| POST | `/api/login/` | No | Legacy admin login |
| GET | `/api/auth/status/` | Yes | Returns authenticated user details |

### User Profile & Leads

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/profile/me/` | Yes | Retrieves profile facts/interests projection |
| GET | `/api/leads/` | Yes | Query leads by status or category |

### Admin Dashboard (Database & Graph)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/admin/users/` | Yes (Admin) | List all registered users in the graph directory |
| GET | `/api/admin/profile-graph/{user_id}/` | Yes (Admin) | Fetch nodes and edges representing a user subgraph |

*To access the Admin Dashboard from the Web Client, log in with username `local_admin` (in local mode) and click the **Database (Database)** icon on the left sidebar rail.*

---

## 5. Deployment

Deployment is managed fully through the AWS Cloud Development Kit (CDK).

```bash
cd infra
pip install -r requirements.txt
cdk synth
cdk deploy --all
```

### Stacks
- **`GraphStack`** (`infra/graph_stack.py`) — Provisions SQS queue + DLQ, Neo4j secrets, Route 53 DNS records, the EC2 instance, Caddy proxy configuration, and the DynamoDB cache tables.
- **`CompanionStack`** (`infra/companion_stack.py`) — Provisions the serverless REST API Lambda, Cognito User Pool, DynamoDB transactional tables, and EventBridge Scheduler.

---

## 6. Project Structure

```
├── app/                      # Django business logic
│   ├── models/               # Family, Member, Message, Reminder models
│   ├── services/             # LLM router, response builders, tool runtimes
│   │   └── leadgen/          # Extraction schema, Neo4j client, parser
│   │       ├── extractor.py
│   │       ├── graph_client.py
│   │       └── schema.py
├── companion_api/            # FastAPI REST Endpoint
│   ├── lead_cache_repo.py    # Repository for DynamoDB commercial lead cache
│   ├── messages_repo.py      # Repository for message history logs
│   ├── profile_cache.py      # Repository for DynamoDB profile cache projection
│   ├── main.py               # REST API paths (including admin profile-graph endpoints)
│   ├── settings.py           # Environment config (adds LEAD_CACHE_TABLE)
├── deploy/graph-ec2/         # EC2 deployment scripts
│   ├── graph_service.py      # Secure FastAPI service for Neo4j querying
│   └── worker.py             # SQS turn events consumer (write-through cache loader)
├── frontend/                 # React SPA Workspace
│   ├── src/
│   │   ├── AdminDashboard.tsx # 2D Canvas force graph visualizer & leads table
│   │   ├── App.tsx           # Main shell (integrates SidebarRail toggles)
│   │   ├── SidebarRail.tsx   # Admin dashboard trigger button
├── infra/                    # AWS CDK Code
│   ├── graph_stack.py        # Graph stack definitions (EC2, SQS, caches)
│   └── companion_stack.py    # Primary serverless APIs and permissions
├── tests/                    # Test suites
│   ├── test_leadgen.py       # Fact extraction, caching, and personalization tests
```

---

## 7. Testing

To run the full test suite (covering MCP executors, EventBridge triggers, profile caches, and lead generation GSI repositories):

```bash
pytest
```
