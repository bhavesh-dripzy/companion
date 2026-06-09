# ADR-0001: Rewrite as a fresh `core/` package using the strangler pattern

- Status: accepted
- Date: 2026-06-08

## Context

The live code works but is a tangle: `app/services/agents/pipeline.py` is 4,169 lines
(`run_agent_turn` alone is 1,330), `tool_runtime.py` dispatches with a hardcoded if/elif chain, and
provider executors are copy-paste with no base class. Refactoring in place is slow and regression-
prone; a single big-bang rewrite on a long-lived branch is an all-or-nothing review risk.

## Decision

Build a fresh, pluggable `core/` package alongside the existing code on `dev`. Port one component at
a time behind a `CORE_PIPELINE` flag, prove parity against the legacy output (golden/shadow tests),
then delete the old code at cutover. `main` and `staging` stay shippable throughout.

## Consequences

- Each increment is small, testable, and independently mergeable into `dev`.
- Both pipelines coexist briefly (shadow mode) — a temporary cost paid for safety.
- The legacy `app/services/agents/` and `tool_runtime.py` are deleted only in the final phase.
