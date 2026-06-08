# ADR-0005: Branching, CI gates, and environment parameterization

- Status: accepted
- Date: 2026-06-08

## Context

There were no `dev`/`staging` branches, one prod environment, no lint, no per-component tests, and
the 250-line convention was unenforced (issue #24, #29). Feature branches merged straight to `main`,
so cleanup work kept breaking working features.

## Decision

- **Branches:** `main` (prod) ← `staging` (identical pre-prod mirror) ← `dev` (integration). All work
  flows `feature → dev → staging → main`. Documented in `CONTRIBUTING.md`.
- **CI** on PRs into `dev`/`staging`/`main`: ruff lint + format check, the `check_file_size.py`
  250-line gate, pytest with markers (`unit`/`integration`/`component`/`live`), frontend build. Lint
  and the file-size gate are scoped to `core/` first and widened as legacy is ported.
- **Environments:** a CDK `environmentName` context (`dev`/`staging`/`prod`) drives stack names,
  domain, secret id, and table names. `prod` preserves today's names so the live stack is unchanged;
  `dev`/`staging` get isolated resources. `deploy.yml` → prod on `main`; `deploy-staging.yml` →
  staging on `staging`.

## Consequences

- Prod is insulated from in-flight rewrite work.
- CI quality gates ratchet up as code moves into `core/`; the 250-line gate becomes hard-fail
  repo-wide at cutover (Phase 8).
- The same CDK app can stand up an isolated dev/staging environment for end-to-end validation.
