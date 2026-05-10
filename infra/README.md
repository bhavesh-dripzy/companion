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

## Zomato MCP

The default deploy sets `EXECUTE_ZOMATO_BY_DEFAULT=false`, so LLM routing works
without requiring the Zomato OAuth token store in Lambda. The next production
step is to replace `mcp-remote`'s local file token dependency with a managed
token store, or mount a minimal EFS access point only for the food-ordering
Lambda if that OAuth flow cannot be changed.
