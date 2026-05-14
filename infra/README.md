# Companion AWS Serverless Infrastructure

This CDK app is the native serverless target for Companion.

## Resources

- API Gateway HTTP API
- Lambda container for API traffic
- Lambda container for individual reminder trigger events
- DynamoDB tables for families, members, messages, and reminders
- EventBridge Scheduler schedule group for per-reminder one-time schedules
- Secrets Manager secret for app configuration such as LLM keys

## Local Commands

```bash
cd infra
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cdk synth
```

Before deploying, bootstrap the target account and region with a non-root deploy
identity:

```bash
cdk bootstrap aws://314359811237/ap-south-1
```

## GitHub Deploy

The deploy workflow expects an Actions secret named `AWS_DEPLOY_ROLE_ARN`.
That role should trust GitHub OIDC for this repository and have permissions to
deploy the CDK stack.

Do not use root credentials for CI/CD.

## MCP (Zomato & Swiggy Food)

The default deploy sets `EXECUTE_ZOMATO_BY_DEFAULT=false`, so LLM routing works
without requiring MCP OAuth tokens in Lambda. When you enable tool execution,
configure URLs via Lambda environment (defaults are set for Zomato and Swiggy
Food) and/or override in **Secrets Manager** `APP_SECRET_ID` JSON (`ZOMATO_MCP_URL`,
`SWIGGY_FOOD_MCP_URL`).

**Connecting (serverless):** use the authenticated HTTP API — same flow as Zomato:

- `POST /api/mcp/pairing/?provider=zomato` or `?provider=swiggy_food` (returns a one-liner to run on a laptop with a browser).
- `GET /api/mcp/oauth/start?provider=…` / callback when hosted OAuth is used.

Tokens are persisted under Secrets Manager key **`companion/mcp-tokens`** (see
`zomato_mcp_executor`) and restored to `/tmp/.mcp-auth` on cold starts.

**Connecting (VM / gunicorn):** use the repo scripts as `www-data` so tokens live
under `/var/www/companion-app/.mcp-auth` — see `deploy/README.md` (`npm run oauth:zomato:prod`
and `npm run oauth:swiggy:prod`).
