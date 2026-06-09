# Contributing

## Branch & environment flow

Three long-lived branches, promotion is one-directional:

```
feature/* ──PR──> dev ──promote──> staging ──promote──> main
                  (integration)    (pre-prod mirror)    (prod)
```

- **`main`** — production. Protected. Deploys to prod (`deploy.yml`) on push.
- **`staging`** — kept identical to `main`; the deployable pre-prod mirror used to validate a
  change end-to-end before it reaches prod. Deploys to the staging environment (`deploy-staging.yml`).
- **`dev`** — integration target for all in-flight work (the architecture rewrite, new providers,
  fixes). Nothing reaches prod until it has been promoted `dev → staging → main`.

**Workflow:** branch off `dev` (`feature/...`, `chore/...`, `fix/...`), open a PR into `dev`, get CI
green, merge. Periodically promote `dev → staging`, validate, then `staging → main`.

This protects already-working features while the rewrite is in flight.

## Conventions

- **One responsibility per file, ≤250 lines.** Enforced by `scripts/check_file_size.py` (CI gate on
  `core/`). Split modules rather than growing them.
- **Lint + format:** `ruff check core scripts/check_file_size.py` and `ruff format core`.
- **Tests are per-component.** Mark them: `unit` (fast, no I/O), `integration` (mocked external),
  `component` (per-provider against recorded fixtures), `live` (real services — never in CI).
  Run `pytest -m unit`, `pytest -m "component and not live"`, etc.
- **A component is not done until** its tests pass and CI is green on its PR into `dev`.

## Adding a provider (the pluggable path)

A new provider is a plugin, not a pipeline edit:

1. Create `core/providers/plugins/<id>/provider.py` (subclass `McpProvider` or `HttpProvider`),
   declaring its tools (`Tool` models) and, for MCP, a `prompts/*.md` behavioural preamble.
2. Register it with one line in `core/registry/bootstrap.py`.
3. Add `core/providers/plugins/<id>/tests/` with at least a `component` test.

No edits to the dispatcher, validator, orchestrator, or pipeline stages are required.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn companion_api.main:app --reload --port 8000   # run the API
pytest -m unit                                         # fast tests
ruff check core && python scripts/check_file_size.py core
```
