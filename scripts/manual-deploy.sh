#!/usr/bin/env bash
# =============================================================================
# Companion — Manual Full-Stack Deploy Script
# =============================================================================
# Replicates what the GitHub Actions deploy.yml / deploy-staging.yml do,
# locally on a machine with AWS credentials / the deploy role.
#
# Usage:
#   ./scripts/manual-deploy.sh              # deploys to production
#   ./scripts/manual-deploy.sh --staging    # deploys to staging
#   ./scripts/manual-deploy.sh --env prod   # explicit prod
#
# Required env vars (set in shell or .env file in the repo root):
#   AWS_ROLE_ARN          ARN of the deploy role (default: arn:aws:iam::616551057455:role/GitHubActionsDeployRole)
#   AWS_REGION            AWS region (default: ap-south-1)
#   DEPLOY_EC2            Set to "true" for prod, "false" for staging
#   COMPANION_ENV         "prod" or "staging"
#   PUBLIC_BASE_URL       Public base URL (https://houzzat.in for prod)
#   GH_TOKEN              GitHub PAT for creating PRs
#
# Individual secret env vars (all optional — APP_SECRETS_JSON takes precedence):
#   OPENAI_API_KEY, DEEPSEEK_API_KEY, GEMINI_API_KEY, GOOGLE_API_KEY,
#   FOOD_ROUTER_PROVIDER, ADMIN_USERNAME, ADMIN_PASSWORD,
#   GOOGLE_CALENDAR_CLIENT_ID, GOOGLE_CALENDAR_CLIENT_SECRET
#
# The script loads .env from the repo root if present.
# =============================================================================

set -euo pipefail

# ── helpers ──────────────────────────────────────────────────────────────────

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
warn() { echo "[$(date '+%H:%M:%S')] WARNING: $*" >&2; }
die()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

load_env() {
  local repo_root
  repo_root="$(cd "$(dirname "$0")/.." && pwd)"
  if [[ -f "$repo_root/.env" ]]; then
    log "Loading .env from $repo_root"
    set -a; source "$repo_root/.env"; set +a
  fi
}

check_command() {
  command -v "$1" &>/dev/null && return 0
  die "Required command '$1' not found. Install it and retry."
}

# ── args ─────────────────────────────────────────────────────────────────────

ENV_NAME="prod"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --staging) ENV_NAME="staging"; shift ;;
    --env)     ENV_NAME="$2"; shift 2 ;;
    --skip-apk) SKIP_APK=1; shift ;;
    *)         die "Unknown argument: $1" ;;
  esac
done

SKIP_APK="${SKIP_APK:-0}"

# ── env / constants ─────────────────────────────────────────────────────────

load_env

AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-616551057455}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
AWS_ROLE_ARN="${AWS_ROLE_ARN:-arn:aws:iam::${AWS_ACCOUNT_ID}:role/GitHubActionsDeployRole}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
DEPLOY_EC2="${DEPLOY_EC2:-}"
COMPANION_ENV="${COMPANION_ENV:-}"

# Derive environment settings
if [[ "$ENV_NAME" == "staging" ]]; then
  STACK_PREFIX="companion-staging"
  COMPANION_ENV="${COMPANION_ENV:-staging}"
  DEPLOY_EC2="${DEPLOY_EC2:-false}"
  PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-staging.houzzat.in}"
  CDK_ENV_NAME="staging"
  log "Deploying to STAGING (companion-staging-*)"
else
  STACK_PREFIX="companion"
  COMPANION_ENV="${COMPANION_ENV:-prod}"
  DEPLOY_EC2="${DEPLOY_EC2:-true}"
  PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://houzzat.in}"
  CDK_ENV_NAME="${ENV_NAME:-prod}"
  log "Deploying to PRODUCTION (companion-*)"
fi

# ── prereqs ─────────────────────────────────────────────────────────────────

log "Checking prerequisites…"
check_command aws
check_command python3
check_command node

# Verify aws CLI is configured (will fail below if not)
AWS_IDENTITY=$(aws sts get-caller-identity 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('Account','ERROR'))") || true
if [[ "$AWS_IDENTITY" == "ERROR" ]]; then
  die "AWS CLI is not configured. Run 'aws configure' or ensure credentials are set."
fi
log "Current AWS identity: $AWS_IDENTITY"

# ── AWS role assumption ──────────────────────────────────────────────────────

log "Assuming deploy role: $AWS_ROLE_ARN"
ASSUME_OUTPUT=$(aws sts assume-role \
  --role-arn "$AWS_ROLE_ARN" \
  --role-session-name "companion-manual-deploy-$(date +%s)" \
  --duration-seconds 3600 \
  --output json)

AWS_ACCESS_KEY_ID=$(echo "$ASSUME_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['AccessKeyId'])")
AWS_SECRET_ACCESS_KEY=$(echo "$ASSUME_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['SecretAccessKey'])")
AWS_SESSION_TOKEN=$(echo "$ASSUME_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['SessionToken'])")

export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_REGION

# Verify target account
VERIFIED_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
if [[ "$VERIFIED_ACCOUNT" != "$AWS_ACCOUNT_ID" ]]; then
  die "Role assumption returned account $VERIFIED_ACCOUNT, expected $AWS_ACCOUNT_ID. Aborting."
fi
log "AWS identity confirmed — account: $VERIFIED_ACCOUNT"

# ── Python setup ─────────────────────────────────────────────────────────────

log "Setting up Python virtual environment…"
python3 -m venv "$INFRA_DIR/.venv"
# shellcheck disable=SC1091
source "$INFRA_DIR/.venv/bin/activate"

pip install --upgrade pip
pip install -r "$REPO_ROOT/requirements.txt"
pip install -r "$INFRA_DIR/requirements.txt"

# Install CDK if not present
if ! command -v cdk &>/dev/null; then
  log "Installing aws-cdk…"
  npm install -g aws-cdk
fi

# Install Node deps (needed for mcp-remote in the Lambda runtime)
log "Installing Node dependencies…"
cd "$REPO_ROOT" && npm ci

# ── CDK deploy ───────────────────────────────────────────────────────────────

log "Cleaning stale CDK output…"
rm -rf "$INFRA_DIR/cdk.out"

log "Synthesising CDK stacks…"
# shellcheck disable=SC2154
cd "$INFRA_DIR"
CDK_DEFAULT_ACCOUNT="$AWS_ACCOUNT_ID" \
CDK_DEFAULT_REGION="$AWS_REGION" \
DEPLOY_EC2="$DEPLOY_EC2" \
  cdk synth --all \
    -c "environmentName=${CDK_ENV_NAME}" \
    -c "projectName=${STACK_PREFIX}" \
    2>&1 | tail -5

log "Deploying CDK stacks (this may take 10-20 minutes for first deploy)…"
CDK_DEFAULT_ACCOUNT="$AWS_ACCOUNT_ID" \
CDK_DEFAULT_REGION="$AWS_REGION" \
DEPLOY_EC2="$DEPLOY_EC2" \
OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  cdk deploy --all \
    --require-approval never \
    -c "environmentName=${CDK_ENV_NAME}" \
    -c "projectName=${STACK_PREFIX}" \
  2>&1 | tee /tmp/cdk-deploy.log

STACK_STATUS=$?
if [[ $STACK_STATUS -ne 0 ]]; then
  die "CDK deploy failed. Check /tmp/cdk-deploy.log for details."
fi

# ── Secrets Manager ──────────────────────────────────────────────────────────

SECRET_ID="${STACK_PREFIX}/app"
log "Updating Secrets Manager secret: $SECRET_ID"

# Build secrets JSON — APP_SECRETS_JSON env var takes precedence
if [[ -n "${APP_SECRETS_JSON:-}" ]]; then
  log "Using APP_SECRETS_JSON override."
  echo "$APP_SECRETS_JSON" > /tmp/new-secrets.json
else
  python3 << 'PY' > /tmp/new-secrets.json
import json, os
secrets = {}
for key in (
    "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
    "FOOD_ROUTER_PROVIDER", "ADMIN_USERNAME", "ADMIN_PASSWORD",
    "GOOGLE_CALENDAR_CLIENT_ID", "GOOGLE_CALENDAR_CLIENT_SECRET",
    "PUBLIC_BASE_URL",
):
    val = os.getenv(key, "").strip()
    if val:
        secrets[key] = val.rstrip("/") if key == "PUBLIC_BASE_URL" else val
print(json.dumps(secrets))
PY
  if [[ ! -s /tmp/new-secrets.json ]] || [[ "$(cat /tmp/new-secrets.json)" == "{}" ]]; then
    warn "No secrets configured — skipping Secrets Manager update."
  else
    log "Built secrets from individual env vars."
  fi
fi

if [[ -s /tmp/new-secrets.json ]] && [[ "$(cat /tmp/new-secrets.json)" != "{}" ]]; then
  # Merge with existing secrets (don't clobber unknown keys)
  python3 << PY
import json as _j
try:
    existing_raw = subprocess_check(["aws", "secretsmanager", "get-secret-value",
        "--secret-id", "$SECRET_ID", "--query", "SecretString", "--output", "text",
        "--region", "$AWS_REGION"])
    existing = _j.loads(existing_raw)
except Exception:
    existing = {}
with open("/tmp/new-secrets.json") as f:
    updates = _j.load(f)
for k, v in updates.items():
    if v:
        existing[k] = v
with open("/tmp/merged-secrets.json", "w") as f:
    _j.dump(existing, f)
PY

  aws secretsmanager put-secret-value \
    --secret-id "$SECRET_ID" \
    --secret-string "file:///tmp/merged-secrets.json" \
    --region "$AWS_REGION"
  log "Secrets updated in Secrets Manager."
else
  log "No new secrets to update — skipping."
fi

# ── Cognito admin user ───────────────────────────────────────────────────────

log "Ensuring Cognito admin user…"
python3 << PY
import json, subprocess, sys, os

def get_secret(key):
    raw = subprocess.check_output(
        ["aws", "secretsmanager", "get-secret-value", "--secret-id", key,
         "--query", "SecretString", "--output", "text", "--region", os.environ["AWS_REGION"]],
        text=True,
    )
    return json.loads(raw)

def get_stack_output(key):
    return subprocess.check_output(
        ["aws", "cloudformation", "describe-stacks",
         "--stack-name", "${STACK_PREFIX}-serverless",
         "--query", f"Stacks[0].Outputs[?OutputKey=='{key}'].OutputValue",
         "--output", "text", "--region", os.environ["AWS_REGION"]],
        text=True,
    ).strip()

try:
    secret = get_secret("$SECRET_ID")
except Exception as e:
    print(f"Could not fetch secret: e}")
    sys.exit(0)

username = str(secret.get("ADMIN_USERNAME") or "").strip()
password = str(secret.get("ADMIN_PASSWORD") or "").strip()
if not username or not password:
    print("No ADMIN_USERNAME/ADMIN_PASSWORD in secret — skipping Cognito setup.")
    sys.exit(0)

user_pool_id = get_stack_output("CognitoUserPoolId")
print(f"User pool ID: {user_pool_id}")

# Check if user exists
exists = subprocess.call(
    ["aws", "cognito-idp", "admin-get-user",
     "--user-pool-id", user_pool_id, "--username", username,
     "--region", os.environ["AWS_REGION"]],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
) == 0

if not exists:
    subprocess.check_call(
        ["aws", "cognito-idp", "admin-create-user",
         "--user-pool-id", user_pool_id, "--username", username,
         "--message-action", "SUPPRESS",
         "--region", os.environ["AWS_REGION"]],
        stdout=subprocess.DEVNULL,
    )
    print(f"Created user: {username}")
else:
    print(f"User already exists: {username}")

subprocess.check_call(
    ["aws", "cognito-idp", "admin-set-user-password",
     "--user-pool-id", user_pool_id, "--username", username,
     "--password", password, "--permanent",
     "--region", os.environ["AWS_REGION"]],
    stdout=subprocess.DEVNULL,
)
print(f"Cognito admin user '{username}' is ready.")
PY

# ── Smoke test ───────────────────────────────────────────────────────────────

log "Running smoke test…"
API_URL=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_PREFIX}-serverless" \
  --query "Stacks[0].Outputs[?OutputKey=='HttpApiEndpoint'].OutputValue" \
  --output text \
  --region "$AWS_REGION")

SMOKE_RESPONSE=$(curl -fsS -o /dev/null -w "%{http_code}" "${API_URL}/api/" 2>/dev/null || echo "000")
if [[ "$SMOKE_RESPONSE" == "200" ]]; then
  log "Smoke test PASSED — API responded with HTTP $SMOKE_RESPONSE"
else
  warn "Smoke test returned HTTP $SMOKE_RESPONSE — check the API manually."
fi

# ── APK build (production only, optional) ───────────────────────────────────

if [[ "$ENV_NAME" != "prod" ]] || [[ "$SKIP_APK" == "1" ]]; then
  log "Skipping APK build."
else
  log "Building Android APK…"
  log "NOTE: APK build requires Android SDK (21+), Java 21+, and Capacitor CLI."
  log "Run manually from the frontend directory: npm run build && npx cap sync android && cd android && ./gradlew assembleDebug"
fi

# ── summary ──────────────────────────────────────────────────────────────────

log ""
log "============================================"
log "  Deploy complete — $ENV_NAME"
log "============================================"
log "  API endpoint:  $API_URL"
log "  Secrets:       $SECRET_ID"
log "  Cognito pool:  ${STACK_PREFIX}-serverless outputs"
log "============================================"
