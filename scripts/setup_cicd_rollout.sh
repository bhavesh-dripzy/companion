#!/usr/bin/env bash
# One-time rollout for the batched-deploy CI/CD model (Promote dev → main, which
# ships ~every 3 days or on manual dispatch). Run ONCE, by a REPO ADMIN, AFTER the
# promote-workflow PR is merged to main. Requires: gh CLI authed as a repo admin.
# Idempotent — safe to re-run.
set -euo pipefail

REPO="bhavesh-dripzy/companion"

echo "==> 1/3  Fast-forward dev up to main (dev becomes the trunk tip)"
MAIN_SHA="$(gh api "repos/$REPO/git/ref/heads/main" --jq '.object.sha')"
# force=false → fails unless this is a real fast-forward (nothing on dev is lost).
# If dev has diverged (its commits already squash-merged into main), reset it:
#   gh api -X PATCH repos/$REPO/git/refs/heads/dev -f sha=$MAIN_SHA -F force=true
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

echo "==> 3/3  Protect main (CI-green + linear history; Promote is the only writer)"
# required_pull_request_reviews: null → no PR required, so the Promote workflow's
# fast-forward push (GITHUB_TOKEN) is allowed. The ci-gate check + linear history
# mean ONLY commits that passed CI on dev can land, and only by fast-forward — so
# no untested commit and no human merge can reach main.
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

echo "Done. dev is the integration trunk; main is promotion-only and deploys"
echo "~every 3 days (or on manual 'Promote dev → main' dispatch)."
echo "Tune the schedule in .github/workflows/promote.yml (cron is UTC)."
