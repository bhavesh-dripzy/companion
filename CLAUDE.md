# CLAUDE.md — CI/CD Runbook

_Companion repo: `bhavesh-dripzy/companion` | AWS: `616551057455` / `ap-south-1`_

---

## Branch & Environment Flow (model A — batched promotion)

```
feature/* ──PR──> dev ──Promote (every ~3 days / manual)──> main ──> Deploy (prod)
                  (integration trunk)                        (prod)
```

| Branch   | CI trigger | Deploys to | Notes |
|----------|-----------|------------|-------|
| `dev`    | PR + push | —          | **Integration trunk — ALL work lands here via PR.** CI only, no deploy |
| `staging`| Push      | `companion-staging-*` stacks | Optional staging environment, no EC2 |
| `main`   | —         | `companion-*` stacks (prod, with EC2) | Protected. Advanced ONLY by the Promote workflow; Deploy runs on trigger |

### THE RULE — never touch `main` directly

- **Every change → PR to `dev`.** Never open a PR to `main`, never push/merge to `main` directly.
- **`main` advances only via `.github/workflows/promote.yml`**, which fast-forwards `main` to `dev` and triggers Deploy. It runs **on a schedule (~every 3 days) or on demand** (`workflow_dispatch`) — so **production ships only when triggered or every 3 days, never on an individual merge**.
- **`deploy.yml` runs only when triggered** (by Promote, or manually) — it no longer fires on a push to `main`.
- Promote only ever **fast-forwards**; if `main` has commits not on `dev` (someone broke the rule), it refuses and you must merge `main` back into `dev` first.
- **Ship now:** run the **Promote** workflow manually (Actions → Promote dev → main → Run workflow), or run **Deploy** manually for a hotfix. Don't wait for the cron.

---

## CI Workflow (`.github/workflows/ci.yml`)

**Runs on:** every PR and every push to `dev`, `staging`, `main`.

### Jobs

#### `changes` (always runs first)
Uses `dorny/paths-filter` to detect what actually changed and sets outputs:
- **`backend` = true** if changed: `core/`, `companion_api/`, `leadgen/`, `deploy/`, `tests/`, `scripts/`, `requirements.txt`, `pyproject.toml`, `package.json`, `package-lock.json`, or `ci.yml`
- **`frontend` = true** if changed: `frontend/`, or `ci.yml`

下游 jobs (`lint`, `backend-tests`, `frontend-build`) all `needs: changes` and gate on the respective flag, so a pure-backend commit skips frontend build and vice versa. This is the biggest CI minutes saver.

#### `lint`
Runs when `backend == true`. Three sequential checks:
1. `ruff check core scripts/check_file_size.py` — lint + import sorting
2. `ruff format --check core scripts/check_file_size.py` — format diff check
3. `python scripts/check_file_size.py core` — **≤250 lines per file gate** for `core/`

If this fails, **do not merge**. Fix lint errors first.

**Common fixes:**
```bash
# Install ruff
python -m venv .venv && source .venv/bin/activate
pip install ruff==0.6.9

# Auto-fix lint
ruff check core scripts/check_file_size.py --fix

# Auto-fix format
ruff format core

# Re-check
ruff check core && ruff format --check core && python scripts/check_file_size.py core
```

#### `backend-tests`
Runs when `backend == true`. Executes:
```bash
pytest -m "not live" --ignore=tests/test_district_mcp_executor.py
```
- **No live tests** (`-m "not live"`) — real-API tests are excluded from CI
- `test_district_mcp_executor.py` is also excluded (requires special MCP executor setup)
- All tests should pass; 351 tests is the baseline count

#### `frontend-build`
Runs when `frontend == true`.
1. `npx tsc --noEmit` — TypeScript type check
2. `npm run build` (Vite) — production build

### CI Failure Diagnosis

**`changes` job fails:**
- Usually a GitHub Actions runner startup issue, not a code problem
- Re-run the workflow from the Actions tab
- If it persists, check GitHub status page: https://www.githubstatus.com

**`lint` fails:**
- Run `ruff check core scripts/check_file_size.py` locally to see errors
- Apply `--fix` for auto-fixable issues (import sorting, etc.)
- Format: `ruff format core`
- File-size gate: files over 250 lines in `core/` must be either split or added to `ALLOWLIST` in `scripts/check_file_size.py` (get code review approval for allowlist additions)

**`backend-tests` fails:**
```bash
pytest -m "not live" --ignore=tests/test_district_mcp_executor.py -v
# run specific test
pytest tests/core/test_mcp_auth_gate.py -v
```

**`frontend-build` fails:**
```bash
cd frontend && npm ci && npx tsc --noEmit && npm run build
```

---

## Deploy Workflows

### `deploy.yml` — Production (`main` branch)

**Trigger:** `workflow_dispatch` only — run by the Promote workflow after it fast-forwards `main`, or manually for a hotfix. It does **not** fire on a push to `main` (that's how prod ships only on trigger or every ~3 days).

**Environment:** `production`

**AWS account:** `616551057455` / `ap-south-1`  
**Deploy role:** `arn:aws:iam::616551057455:role/GitHubActionsDeployRole`

#### Steps

1. **Configure AWS credentials** — assumes the deploy role via OIDC
2. **Verify AWS target account** — guards against wrong-role configuration
3. **Install CDK dependencies** (`infra/`) + upgrade CDK CLI
4. **CDK deploy** — `cdk deploy --all --require-approval never`
   - Uses `OPENAI_API_KEY` secret
   - `DEPLOY_EC2=true` — spins up EC2 worker for leadgen graph
5. **Populate app secrets** — merges GitHub Secrets into `companion/app` AWS Secrets Manager
   - `APP_SECRETS_JSON` (override JSON) OR individual keys: `SARVAM_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `FOOD_ROUTER_PROVIDER`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `PUBLIC_BASE_URL`
6. **Ensure Cognito admin user** — creates/sets password for `ADMIN_USERNAME` in Cognito user pool
7. **Smoke test** — `GET /api/` against the deployed API URL
8. **Build APK** — **opt-in only.** Rebuilds the Android APK + publishes to the S3 app-downloads bucket, but **only when you dispatch Deploy with `build_apk=true`** *and* app files (`frontend/`, `ci/`) changed. Routine deploys and Promote leave it off (it was the slowest step and fired even for landing/CSS-only changes). Gradle + npm are cached, so it's ~2× faster when it does run. Turn it on when the mobile app actually changed.

> **Landing-page change? Don't use this workflow.** Use **`deploy-landing.yml`** (below) — it ships in seconds instead of ~10 min and skips CDK + the APK entirely.

#### Secrets required (GitHub repo settings → Secrets and variables → Actions)

| Secret | Description |
|--------|-------------|
| `OPENAI_API_KEY` | OpenAI API key |
| `GEMINI_API_KEY` | Gemini API key |
| `GOOGLE_API_KEY` | Google Maps API key |
| `SARVAM_API_KEY` | Sarvam API key |
| `APP_SECRETS_JSON` | JSON override for all secrets (preferred over individual keys) |
| `FOOD_ROUTER_PROVIDER` | `sarvam` or `deepseek` |
| `ADMIN_USERNAME` | Cognito admin login |
| `ADMIN_PASSWORD` | Cognito admin password |
| `GOOGLE_CALENDAR_CLIENT_ID` | OAuth client ID |
| `GOOGLE_CALENDAR_CLIENT_SECRET` | OAuth client secret |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Twilio WhatsApp sender number |

### `deploy-landing.yml` — Fast landing-only deploy ⚡

**Use this for any change under `landing/`.** The landing site is just static files on S3 + CloudFront, so it does **not** need `cdk deploy --all` or an APK rebuild. This syncs `landing/` straight to the bucket and invalidates CloudFront in **well under a minute** (vs ~10 min for the full Deploy).

**Trigger:** `workflow_dispatch` only — Actions → **Deploy landing (fast)** → Run workflow (any branch/ref), or:
```bash
gh workflow run deploy-landing.yml --ref main
```

**Steps:** assume the deploy role (OIDC) → read `LandingBucketName` + `DistributionId` from the **`companion-landing` CloudFormation outputs** (nothing hardcoded) → `aws s3 sync landing/ s3://<bucket> --delete` → CloudFront invalidation `/*` → smoke-test `https://jatayulabs.com`.

**Notes:**
- Only tracked files under `landing/` are published (CI checkout has no untracked scratch files).
- Still land the change on the trunk the normal way (PR → `dev`), even though you deployed via this fast path — otherwise the next Promote's full Deploy could serve a stale `landing/`.
- Separate concurrency group, so it never queues behind a heavy backend deploy.

### `deploy-staging.yml` — Staging (`staging` branch)

**Trigger:** push to `staging` + `workflow_dispatch`.

**Environment:** `staging`

Same structure as prod deploy, but:
- Stack names prefixed with `companion-staging-*`
- `companion-staging/app` Secrets Manager secret (instead of `companion/app`)
- `DEPLOY_EC2=false` — no EC2 worker in staging
- `DEPLOY_STAGING=true` CDK context flag
- No Cognito admin user step
- No APK build

### `workflow_dispatch` — Manual Deploy

Both deploy workflows have `workflow_dispatch`, meaning you can trigger a deploy from the **GitHub Actions UI** without a code push:

1. Go to **Actions** tab → select **Deploy** or **Deploy (staging)**
2. Click **Run workflow** (right panel)
3. Select the branch and click **Run workflow**

Use this for:
- Re-deploying after a secrets update (no code change needed)
- Force-redeploying a specific SHA
- Emergency hotfix validation

---

## CDK Stacks

| Stack | Description |
|-------|-------------|
| `companion-serverless` | Main API: Lambda (FastAPI/Mangum), API Gateway, Cognito, DynamoDB tables, S3 buckets |
| `companion-graph` | Knowledge graph: Neo4j on EC2, leadgen worker |
| `companion-landing` | Landing page (if applicable) |

CDK app entry: `infra/app.py`  
Main stack: `infra/companion_stack.py`  
Graph stack: `infra/graph_stack.py`

CDK context flags:
- `environmentName=staging` — used by `deploy-staging.yml`
- `DEPLOY_EC2=true|false` — whether to include the graph/EC2 components

---

## Common CI/CD Problems

### CI fails with "recent account payments have failed"
**Cause:** GitHub Actions minute quota exceeded or payment method failed.  
**Fix:** Billing issue — requires someone with GitHub org billing access to resolve:
- Go to **GitHub → Settings → Billing & plans** for the `bhavesh-dripzy` org or personal account
- Add/update payment method **or** purchase more Actions minutes
- Once resolved, re-run CI from the Actions tab

### Deploy fails at CDK deploy step
**Common causes:**
1. **CDK version mismatch** — stale CDK CLI fails on schema changes. The workflow installs `aws-cdk` via `npm install -g aws-cdk` before deploy, which should handle this. If it persists:
   ```bash
   cd infra && npm install -g aws-cdk && cdk --version
   ```
2. **Lack of `bootstrap`** — if the environment was never bootstrapped for CDK:
   ```bash
   aws cloudformation describe-stacks --stack-name CDKToolkit  # check if bootstrapped
   ```
3. **Permission denied** — GitHub Actions deploy role not configured correctly. Check the `AWS_DEPLOY_ROLE_ARN` exists and has the right trust policy for the GitHub OIDC provider.

### Deploy fails at "Populate app secrets"
**Cause:** Secrets Manager `PutSecretValue` failed, usually because the secret doesn't exist yet.  
**Fix:** Create the secret manually first:
```bash
aws secretsmanager create-secret \
  --name companion/app \
  --secret-string '{}' \
  --region ap-south-1
```

### Smoke test fails after deploy
**Cause:** Lambda cold start failure, missing dependency, or incorrect environment variables.  
**Fix:**
1. Check CloudWatch Logs for the Lambda function (`companion-api` or similar)
2. Verify secrets were written to Secrets Manager correctly
3. Check API Gateway stage variables
4. If EC2 graph worker: check EC2 instance status and logs

### APK didn't build / build fails
**First check:** the APK is **opt-in** — a normal deploy or Promote will **not** build it. Re-run Deploy with `-f build_apk=true` (and app files must have changed).  
**Cause of failures:** Usually Node.js version (Capacitor 8 requires Node ≥22), missing Android SDK, or keystore issues.  
**Fix:**
- Verify Node version in `build-apk` job: uses Node 22
- Check `ci/companion-debug.keystore` is committed
- Verify Android SDK packages: `platform-tools`, `platforms;android-36`, `build-tools;36.0.0`

### "Changes" job always fails on PRs from forks
**Cause:** `dorny/paths-filter` on fork PRs may not have access to the full repo tree.  
**Fix:** This is expected GitHub behavior for security. The `changes` job will likely show `backend=true` and `frontend=true` for fork PRs, running all CI jobs. This is safe — it's better to over-run than under-run on external PRs.

---

## Local Development Equivalents

```bash
# Lint + format check (same as CI lint job)
ruff check core scripts/check_file_size.py
ruff format --check core scripts/check_file_size.py
python scripts/check_file_size.py core

# Format fix
ruff format core

# Run tests (same as CI backend-tests job)
pytest -m "not live" --ignore=tests/test_district_mcp_executor.py

# CDK diff (review what would change)
cd infra
pip install -r requirements.txt
npm install -g aws-cdk
cdk diff --all -c environmentName=staging   # staging
cdk diff --all                               # prod (if bootstrapped)

# CDK deploy (LOCAL — not for production use without review)
cdk deploy --all --require-approval never
```

---

## Who Can Do What

| Action | Who |
|--------|-----|
| Merge PR to `dev` | Anyone with write access |
| Merge PR to `staging` | Anyone with write access |
| Merge PR to `main` | `admin`, `admin_bhavesh`, `admin_kshitij`, `local_admin` (protected branch) |
| Re-run CI | Anyone with repo access |
| Manual deploy via `workflow_dispatch` | Anyone with repo access |
| Modify GitHub Secrets | Repo admins |
| Resolve GitHub Actions billing | Billing admins on the GitHub org/account |
| Modify AWS IAM roles / CDK bootstrap | AWS account admins |

---

## Key Contacts

- **Repo admin:** Bhavesh (`bhavesh-dripzy`)
- **AWS account:** `616551057455` — contact account admin for access issues
- **GitHub billing:** resolve via https://github.com/settings/billing

---

## Quick Reference

```bash
# Check latest CI runs
gh run list --workflow=CI --limit 5

# Check deploy status
gh run list --workflow=Deploy --limit 5

# Fast landing-only deploy (static → S3 + CloudFront, ~30s; use for any landing/ change)
gh workflow run deploy-landing.yml --ref main

# Trigger a full manual deploy (all CDK stacks; APK stays OFF)
gh workflow run deploy.yml --ref main
# Full deploy AND rebuild the Android APK (only when the mobile app changed)
gh workflow run deploy.yml --ref main -f build_apk=true

# Cancel a running workflow
gh run cancel <run-id>

# Re-run a failed job
gh run rerun <run-id>
```
