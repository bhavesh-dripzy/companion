# Companion — Serverless Food & Reminder Assistant

Companion is a multi-provider food-ordering and reminder-management API running
on AWS as a fully serverless application.

## Architecture

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
          │  (4 tables)  │  │(auth) │ │(LLM keys)  │
          └─────────────┘  └──────┘  └────────────┘
                               │
                    ┌──────────▼───────────┐
                    │  EventBridge Scheduler│
                    │  (reminder triggers)  │
                    └──────────────────────┘
```

- **API Gateway HTTP API** — public HTTP endpoint with CORS, routes all
  traffic to the Lambda function.
- **Lambda** (Docker, Python 3.12 + Node 20) — runs a FastAPI app wrapped
  with Mangum. Node is bundled for the `mcp-remote` bridge.
- **DynamoDB** — four tables: `families`, `members`, `messages`, `reminders`.
  All PAY_PER_REQUEST with point-in-time recovery.
- **Cognito User Pool** — JWT-based auth (RS256, verified via JWKS).
  Supports self-sign-up, email verification, and hosted UI.
- **EventBridge Scheduler** — one-time reminder triggers. The scheduler
  invokes a dedicated Lambda target function.
- **Secrets Manager** — stores LLM provider API keys at `companion/app`.
- **AWS CDK** (`infra/`) — all infrastructure as code.

## Quickstart

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

## Authentication

### Cognito (production)

When `COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID` are set, all API requests
must include a Cognito-issued JWT (Access or ID token) in the `Authorization:
Bearer <token>` header. The API verifies tokens against the User Pool's JWKS
endpoint — validating issuer, audience, expiry, and RS256 signature.

Clients use the Cognito Hosted UI or an SDK (Amplify,
`amazon-cognito-identity-js`) to obtain tokens:

```bash
# Get SDK config from the API
curl http://localhost:8000/api/auth/cognito/config/
# → {"enabled":true,"region":"ap-south-1","user_pool_id":"...","client_id":"...",...}
```

When Cognito is enabled, `POST /api/login/` returns HTTP 400 — the legacy
admin-login path is disabled.

### Legacy (local dev / fallback)

When Cognito env vars are **not** set, the API falls back to hardcoded admin
credentials and a self-signed HS256 JWT. Use this only for local development:

```bash
curl -X POST http://localhost:8000/api/login/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"local_admin","password":"test_password_123"}'
# → {"access_token":"...","token_type":"bearer","username":"local_admin"}
```

## API Reference

All authenticated endpoints require `Authorization: Bearer <token>`.

### Health & UI

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/` | No | Health check |
| GET | `/` or `/food/` | No | Food-agent SPA (HTML) |

### Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/login/` | No | Legacy admin login (disabled when Cognito on) |
| GET | `/api/auth/status/` | Yes | Returns `{"authenticated":true,"username":"..."}` |
| GET | `/api/auth/cognito/config/` | No | Cognito SDK config (User Pool ID, Client ID, domain) |

### Food Agent

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/send-message/` | Yes | LLM-routed message (food query with optional WhatsApp link) |
| POST | `/api/food/query/` | Yes | Full food query: LLM plan → MCP tool execution |

**`POST /api/food/query/`** accepts:

```json
{
  "message": "best pizza near me",
  "address_id": "<optional saved address>",
  "provider": "gemini|openai|deepseek",
  "tool_providers": ["zomato", "swiggy_food"],
  "execute_tools": true,
  "include_raw_zomato": false
}
```

Returns the LLM routing plan, tool execution results per provider, and a
generated assistant message.

### Tool Providers

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/tools/providers/` | Yes | List all available tool providers |
| POST | `/api/tools/providers/custom/` | Yes | Register a custom MCP provider |
| DELETE | `/api/tools/providers/custom/{id}` | Yes | Remove a custom MCP provider |

**Built-in providers:**

| Provider | ID | MCP URL |
|----------|-----|---------|
| Zomato | `zomato` | `https://mcp-server.zomato.com/mcp` |
| Swiggy Food | `swiggy_food` | `https://mcp.swiggy.com/food` |
| Swiggy Instamart | `swiggy_instamart` | `https://mcp.swiggy.com/im` |
| Zepto | `zepto` | `https://mcp.zepto.co.in/mcp` |
| Blinkit | `blinkit` | Not configured (placeholder) |

### MCP Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/mcp/status/` | Yes | MCP OAuth connection status |
| POST | `/api/mcp/oauth/` | Yes | Start hosted OAuth flow |
| POST | `/api/mcp/pairing/` | Yes | Start pairing flow (returns code + script) |
| GET | `/api/mcp/pairing/script` | Yes | Download pairing script |
| POST | `/api/mcp/pairing/complete` | Yes | Complete pairing with uploaded tokens |
| GET | `/api/mcp/oauth/callback` | Yes | OAuth callback handler (HTML) |
| POST | `/api/mcp/test/` | Yes | Test MCP connection |
| GET | `/api/mcp/tokens/status/` | Yes | Check token validity |
| GET | `/api/mcp/tools/` | Yes | List available MCP tools |

### Reminders

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/reminders/` | Yes | Create a reminder |
| GET | `/api/reminders/pending/` | Yes | List pending reminders for a member |
| POST | `/api/reminders/respond/` | Yes | Respond to a reminder (yes/no) |

**`POST /api/reminders/`** request shape:

```json
{
  "member_id": "user-123",
  "title": "Take medicine",
  "description": "Vitamin D",
  "type": "custom",
  "schedule_type": "interval",
  "next_trigger": "2026-05-11T10:00:00Z",
  "interval_minutes": 480,
  "retry_interval_minutes": 5,
  "requires_confirmation": true
}
```

## LLM Routing

The food agent uses an LLM (Gemini, DeepSeek, or OpenAI) to translate
free-text food requests into structured MCP tool plans. Auto-selection
order: Gemini → DeepSeek → OpenAI.

Set these in `.env`:

```bash
GEMINI_API_KEY=...        # preferred
GEMINI_FOOD_ROUTER_MODEL=gemini-2.5-flash-lite

DEEPSEEK_API_KEY=...      # fallback
DEEPSEEK_FOOD_ROUTER_MODEL=deepseek-chat

OPENAI_API_KEY=...        # last resort
OPENAI_FOOD_ROUTER_MODEL=gpt-4o-mini

# Optional: force a specific provider
# FOOD_ROUTER_PROVIDER=deepseek
```

## MCP Setup

The API connects to MCP providers (Zomato, Swiggy, Zepto, etc.) through
`mcp-remote`, a local bridge that speaks stdio to the MCP server over HTTPS.

OAuth tokens are stored in `.mcp-auth/` (gitignored). Use the **pairing flow**
to provision tokens: `POST /api/mcp/pairing/` → download script → upload
tokens → `POST /api/mcp/pairing/complete`.

## Reminders

Reminders use EventBridge Scheduler for one-time triggers. When a reminder
fires, the scheduler invokes `scheduled_reminder_handler` which marks the
reminder as triggered and creates a follow-up schedule if `interval_minutes`
is set.

Key env vars for the scheduler:

- `REMINDER_TARGET_ARN` — the Lambda function ARN to invoke
- `SCHEDULER_ROLE_ARN` — IAM role allowing EventBridge to invoke Lambda
- `SCHEDULER_GROUP_NAME` — scheduler group (default: `companion-reminders`)

## Deployment

```bash
cd infra
pip install -r requirements.txt
cdk synth
cdk deploy
```

Stack outputs:

- `HttpApiEndpoint` — the public API URL
- `CognitoUserPoolId` — User Pool ID
- `CognitoClientId` — App Client ID
- `CognitoDomain` — Hosted UI domain
- `AppSecretName` — Secrets Manager secret name
- `RemindersTableName` — DynamoDB reminders table

**Important**: Never deploy with root credentials. Use an IAM role or
GitHub OIDC deploy role.

## Project Structure

```
├── app/                  # Django app (models, services, views)
│   ├── models/           # Family, Member, Message, Reminder models
│   ├── services/         # LLM router, food response builder, tool runtime
│   └── views/            # Food agent, reminders, send_message
├── companion_api/        # FastAPI application
│   ├── auth.py           # Auth dependency (Cognito + legacy JWT)
│   ├── cognito_auth.py   # Cognito JWKS verification
│   ├── dynamodb.py       # DynamoDB repository layer
│   ├── main.py           # API routes
│   ├── models.py         # Pydantic request/response models
│   ├── reminders.py      # Reminder scheduler (EventBridge)
│   ├── secrets.py        # Secrets Manager helper
│   └── settings.py       # Environment config
├── config/               # Django settings (legacy, being phased out)
├── infra/                # AWS CDK stack
│   ├── companion_stack.py  # All infrastructure
│   └── app.py            # CDK app entry point
├── scripts/              # Verification scripts for Gemini, OpenAI
├── tests/                # API tests
├── .env.example          # Environment variable template
├── Dockerfile            # Lambda container image
├── requirements.txt      # Python dependencies
└── package.json          # Node dependencies (mcp-remote)
```

## Local Testing

```bash
# Verify LLM providers
python scripts/verify_gemini.py
python scripts/verify_openai.py

# Run test suite
pytest tests/test_api.py -v

# Run with hot reload
uvicorn companion_api.main:app --reload
```

## Troubleshooting

- **`node_modules` missing** — Run `npm install` at the repo root.
- **401 / auth failed** — If Cognito is enabled, use Cognito tokens,
  legacy login. Check `GET /api/auth/cognito/config/`.
- **MCP OAuth expired** — Run the pairing flow to re-authenticate.
- **LLM quota exhausted** — Gemini free tier has tight limits. Switch to
  DeepSeek or OpenAI, or use `gemini-2.5-flash-lite`.
- **OAuth redirect errors** — Zomato only allows certain OAuth redirect URIs;
  you may need their support for approved client configuration.