# Manual Deploy — Prod Lambda + APK

The steps I follow to deploy the latest `main` to the production Lambda and to
rebuild + publish the Android APK, **without** GitHub Actions (used when CI/CD is
down, e.g. Actions billing failure).

- AWS account: `616551057455` / `ap-south-1`
- ECR image tag convention: `manual-<short-sha>-<epoch>`
- ECR repo: **the CDK container-assets repo** `cdk-hnb659fds-container-assets-616551057455-ap-south-1`
  (the API image is a CDK Docker asset — there is **no** standalone `companion` repo)
- Lambda function: CDK-generated name `companion-serverless-ApiFunction*`
  (e.g. `companion-serverless-ApiFunctionCE271BD4-8ZNytOKC6UXy`) — **not** `companion-api`.
  Discover the current name with:
  `aws lambda list-functions --region ap-south-1 --query "Functions[?starts_with(FunctionName,\`companion-serverless-ApiFunction\`)].FunctionName" --output text`
- Lambda smoke test (TLS-cert-safe): API Gateway endpoint
  `https://dy8rmz0pql.execute-api.ap-south-1.amazonaws.com/api/`
- Public APK URL: `https://app.jatayulabs.com/companion.apk`
- APK S3 bucket: `companion-serverless-appdownloads326c8a2d-lbwlmzrzvmhj`

> Only deploy after the user asks. Confirm `main` is at the intended commit first.
> CI/CD note: `deploy.yml` has been failing (~4s, GitHub Actions billing) — that is
> why this manual path is in use. Pushing to `main` will still trigger (and fail) it;
> the live Lambda/APK come from the manual steps below, not from CI.

---

## 0. Pre-flight

```bash
git checkout main && git pull
git rev-parse --short HEAD          # note the SHA — goes into the image tag
aws sts get-caller-identity         # confirm account 616551057455
```

---

## 1. Deploy the prod Lambda (backend)

The prod Lambda runs the FastAPI app as a Docker image pulled from ECR. Build →
push → point the function at the new image.

```bash
ACCOUNT=616551057455
REGION=ap-south-1
SHA=$(git rev-parse --short HEAD)
TAG="manual-${SHA}-$(date +%s)"
ECR="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
# The API image is a CDK Docker asset — push it to the CDK container-assets repo,
# NOT a standalone "companion" repo (which does not exist).
REPO="cdk-hnb659fds-container-assets-${ACCOUNT}-${REGION}"
# CDK-generated function name — discover it (don't hardcode the random suffix):
FUNCTION=$(aws lambda list-functions --region "$REGION" \
  --query "Functions[?starts_with(FunctionName,\`companion-serverless-ApiFunction\`)].FunctionName" \
  --output text)
echo "FUNCTION=$FUNCTION"           # sanity-check it resolved to exactly one name

# 1a. Auth Docker to ECR
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR"

# 1b. Build the image (repo root has the Lambda Dockerfile).
# NOTE: the Dockerfile runs `npm run build` (tsc -b) inside the build, so a
# TypeScript error in frontend/ will FAIL the image build. Fix it and re-commit
# before retrying. (Run `cd frontend && npx tsc --noEmit` first to catch it.)
docker build -t "${ECR}/${REPO}:${TAG}" .

# 1c. Push to ECR
docker push "${ECR}/${REPO}:${TAG}"

# 1d. Point the Lambda at the new image
aws lambda update-function-code \
  --function-name "$FUNCTION" \
  --image-uri "${ECR}/${REPO}:${TAG}" \
  --region "$REGION"

# 1e. Wait until the update finishes
aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"

# 1f. Confirm the live image + status
aws lambda get-function --function-name "$FUNCTION" --region "$REGION" \
  --query 'Configuration.[LastUpdateStatus]' --output text
aws lambda get-function --function-name "$FUNCTION" --region "$REGION" \
  --query 'Code.ImageUri' --output text   # should end in :<TAG>
```

### Smoke test

```bash
# Use the API Gateway endpoint directly — the custom domain can trip the local
# sandbox's TLS cert bundle. Expect {"status":"ok"}.
curl -sk https://dy8rmz0pql.execute-api.ap-south-1.amazonaws.com/api/
```

If it returns `{"status":"ok"}`, the backend deploy is good.

---

## 2. Build + publish the APK (frontend)

The APK must be signed with the **CI debug keystore** (`ci/companion-debug.keystore`)
so installs upgrade in place. Back up the local keystore first, restore it after.

```bash
JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ANDROID_HOME=/home/nishkarsh/Android/Sdk
BUCKET=companion-serverless-appdownloads326c8a2d-lbwlmzrzvmhj

# 2a. Swap in the CI keystore (back up the local one)
cp ~/.android/debug.keystore ~/.android/debug.keystore.bak
cp ci/companion-debug.keystore ~/.android/debug.keystore

# 2b. Build a FRESH web bundle, then sync it into the Android project.
# `cap sync` only COPIES frontend/dist — it does not build it. Skipping the build
# ships a stale dist (an APK without your latest changes). Build with an empty
# .env so the native API base falls back to the prod default
# (https://app.jatayulabs.com — see frontend/src/api.ts).
cd frontend
[ -f .env ] && cp .env .env.predeploybak    # preserve any local .env
echo "" > .env
JAVA_HOME=$JAVA_HOME ANDROID_HOME=$ANDROID_HOME npm run build
[ -f .env.predeploybak ] && mv .env.predeploybak .env || rm -f .env   # restore
JAVA_HOME=$JAVA_HOME ANDROID_HOME=$ANDROID_HOME npx cap sync android
cd ..

# 2b'. Bump the Android version so the install is recognized as an UPGRADE.
# `frontend/android` is gitignored + regenerated by `cap add` with Capacitor's
# default versionCode 1, so without this every APK is "version 1" and devices
# keep the old bundle (this is exactly the "changes didn't deploy" bug). Derive a
# monotonic versionCode from the git commit count; run AFTER `cap sync`.
VC=$(git rev-list --count HEAD)
sed -i -E "s/versionCode [0-9]+/versionCode ${VC}/; s/versionName \"[^\"]*\"/versionName \"1.0.${VC}\"/" \
  frontend/android/app/build.gradle
grep -E "versionCode|versionName" frontend/android/app/build.gradle   # sanity-check
cd frontend

# 2c. Build the debug APK
cd android
JAVA_HOME=$JAVA_HOME ANDROID_HOME=$ANDROID_HOME ./gradlew clean assembleDebug
cd ../..

# 2d. Upload to S3 as companion.apk
aws s3 cp \
  frontend/android/app/build/outputs/apk/debug/app-debug.apk \
  "s3://${BUCKET}/companion.apk" --region ap-south-1

# 2e. Restore the local keystore
cp ~/.android/debug.keystore.bak ~/.android/debug.keystore
```

### Verify the APK URL

```bash
# A GET returns HTTP 302 -> a signed S3 URL. That redirect IS the success case
# (HEAD returns 405 because the route only allows GET).
curl -sI -X GET https://app.jatayulabs.com/companion.apk | head -3
```

A `HTTP/2 302` with a `location:` pointing at the S3 bucket = APK is live.

---

## 3. Done — report back

- Backend: Lambda `LastUpdateStatus: Successful`, image `:<TAG>`, smoke test `{"status":"ok"}`.
- APK: uploaded, `https://app.jatayulabs.com/companion.apk` → 302 to signed S3 URL.
- Local keystore restored from backup.

## Notes / gotchas

- **TLS cert in sandbox**: smoke-testing `https://app.jatayulabs.com/api/` can fail
  with `unable to get local issuer certificate` (local cert bundle, not the
  server). Use the API Gateway endpoint or `curl -k`.
- **Keystore**: always restore `~/.android/debug.keystore` from the `.bak` after
  building, or local dev builds will be signed with the CI key. On this machine
  `~/.android/debug.keystore`, its `.bak`, and `ci/companion-debug.keystore` are
  already byte-identical (the CI key), so the swap/restore is currently a no-op —
  verify with `md5sum` if in doubt before clobbering a real local keystore.
- **Java/Node**: APK build needs Java 21 and Node ≥22 (Capacitor 8).
- **Prefer CI**: when GitHub Actions is healthy, push to `main` and let
  `deploy.yml` do all of this. This manual path is the fallback.
