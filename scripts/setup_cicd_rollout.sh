#!/usr/bin/env bash
# One-time rollout for the batched-deploy CI/CD model (promote dev → main 3×/day).
# Run ONCE, by a repo admin, AFTER the promote-workflow PR is merged to main.
# Requires: gh CLI authenticated as a repo admin. Idempotent — safe to re-run.
set -euo pipefail

REPO="bhavesh-dripzy/companion"

echo "==> 1/3  Fast-forward dev up to main (dev becomes the trunk tip)"
MAIN_SHA="$(gh api "repos/$REPO/git/ref/heads/main" --jq '.object.sha')"
# force=false → fails unless this is a real fast-forward (nothing on dev is lost).
gh api -X PATCH "repos/$REPO/git/refs/heads/dev" -f sha="$MAIN_SHA" -F force=false \
  && echo "    dev → $MAIN_SHA"

echo "==> 2/3  Protect dev (every change goes via PR; ci-gate must pass)"
gh api -X PUT "repos/$REPO/branches/dev/protection" --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["ci-gate"] },
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

echo "==> 3/3  Protect main (CI-green + linear history; promotion is the only writer)"
gh api -X PUT "repos/$REPO/branches/main/protection" --input - <<'JSON'
{
  "required_status_checks": { "strict": false, "contexts": ["ci-gate"] },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

echo "Done. dev is the integration trunk; main is promotion-only and deploys 3×/day."
echo "Tune the schedule in .github/workflows/promote.yml (cron is UTC)."
