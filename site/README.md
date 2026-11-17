# Jatayu Labs company website

The approved blue light design uses Inter, restrained product motion, and actual
Vaayu UI components with fictional Northstar data. Vaayu remains in development.
The company site must not disclose internal assessment infrastructure or claim
that synthetic demonstrations are production security findings.

## Files

- `source/`: reviewed company-page HTML, CSS, and JavaScript.
- `prepare_static.py`: deterministic asset fingerprints and release manifest.
- `demo-overlay/`: offline API, synthetic data, and demo-only UI adaptations.
- `demo-provenance.json`: pinned product source revision.
- `build_demo.py`: reproduce the isolated demo without modifying product source.
- `csp.txt`, `edge-routes.js`: shared security policy and CloudFront routing.
- `../landing/`: the complete public artifact, and the only published directory.

There are no third-party analytics, hosted font dependencies, credentials,
production API connections, or active security tests in the site.

## Get demo

The CTA opens a prefilled email to `founders@jatayulabs.com`. It explicitly tells
the visitor to send the message in their email application and provides the
address as a fallback. The site never claims that a request was delivered. There
is no pretend form, personal-data database, or publicly writable submission API.
A future server-side intake service needs separate delivery, privacy, spam,
rate-limit, and failure-path work before replacing this honest email handoff.

## Rebuild and test

```bash
python site/prepare_static.py
python site/check_static.py
node site/test-edge-routes.cjs
node --check site/source/site.js
python site/serve_preview.py
# Separate terminal; Playwright and Chrome are development-only dependencies.
python site/qa_browser.py
```

The optional axe audit uses a locally downloaded `axe-core@4.10.3/axe.min.js`
passed through `--axe`; it is test instrumentation and is not shipped publicly.

Rebuild the demo only when necessary, from the pinned clean product checkout:

```bash
python site/build_demo.py --source /path/to/cyber-jatayu
python site/prepare_static.py
```

The builder copies UI source into a temporary directory, replaces the API with
local fixtures, typechecks, and compiles with locked dependencies. It never runs
the real API and never changes the product repository. Demo notes are in-memory
and disappear on reload. Fictional asset links do not navigate to targets.

## Deployment boundary

Account `616551057455`, region `ap-south-1`, stack `companion-landing`.
Certificate `489a052c-a6a5-437a-9763-2b50a684a394` is pre-existing in `us-east-1`.
The isolated entry point is **`infra/landing_app.py`**, not `infra/app.py`.
Never use `cdk deploy --all` to deploy this website.

All changes land via PR to `dev`. Only the Promote workflow advances `main`.
Website-only promotions select `deploy-landing.yml`, never the full product or
Android deploy. The deployment workflow requires promoted `main`.

```bash
gh workflow run promote.yml --ref dev
# Run the reviewed dev definition, including on the first promotion when main
# still has the older always-full-deploy definition. It advances main normally.
# Promote selects the website-only workflow for a website-only diff.
# Or redeploy an already promoted revision:
gh workflow run deploy-landing.yml --ref main
```

The previous runbook also permits a landing-only manual fast path. If hosted
runners are unavailable, a locally verified revision that has landed via PR to
`dev` may be deployed explicitly with the isolated entry point below, without
advancing `main`. Do not forge successful CI statuses or bypass branch protection.
Record the source SHA, local checks, change-set review, and public verification.

```bash
export AWS_PROFILE=companion AWS_REGION=ap-south-1
# Confirm the current revision is the reviewed, merged revision before proceeding.
test "$(aws sts get-caller-identity --query Account --output text)" = 616551057455
python site/prepare_static.py && python site/check_static.py
cd infra
# Use aws-cdk-lib 2.253.1, constructs 10.6.0, and CDK CLI 2.1127.0.
cdk --app "python landing_app.py" synth companion-landing --quiet
python ../site/check_infrastructure.py cdk.out/companion-landing.template.json
# Review a change set first. It must contain website resources only.
cdk --app "python landing_app.py" deploy companion-landing --exclusively --no-execute
# Inspect the resulting CloudFormation change set, then execute that exact set.
```

After the reviewed change set completes, invalidate its distribution, run
`python site/cutover_dns.py` to review the DNS plan, then run it with `--apply`.
The script accepts only the old orphaned distribution or this stack's validated
new distribution for the four apex/www A/AAAA aliases. It refuses foreign owners
and confirms that MX, DKIM, SPF, NS, certificate validation, and product records
remain unchanged. It does not touch any other domain or AWS account.

Finally run `python site/verify_live.py https://jatayulabs.com` and browser QA
against the live URL. Do not announce the site as live before these checks pass.

## Security and rollback

- Private, SSL-only, AES256-encrypted S3 with CloudFront origin access control.
- Versioned content with retained bucket, no automatic content pruning, and
  stack termination protection. Cached pages can keep loading old assets.
- HTTPS redirect, TLS 1.2 minimum, CSP without remote scripts or unsafe eval,
  HSTS without forcing unrelated subdomains, nosniff, and restricted permissions.
- Actual 404 responses, directory-index routing, and canonical www redirect.
- Full SHA-256/size inventory in `landing/release-manifest.json`.

Before updates, record the deployed source SHA and release manifest. Roll back
by redeploying a previously reviewed company-site artifact into the same bucket
and invalidating its distribution, then verify the old manifest. Do not destroy
the stack or roll DNS back to the deleted CloudFront distribution. S3 versions
remain available if an individual object must be restored. Infrastructure
rollback should use the prior reviewed website-only template; never deploy the
unrelated product stacks as part of a site rollback.
