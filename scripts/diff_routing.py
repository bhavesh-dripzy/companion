#!/usr/bin/env python3
"""Diff the golden routing table against the live orchestrator for the same inputs.

Run this after changing core/pipeline/orchestration.py or
core/pipeline/stages/prompts/orchestrator.system.md to see which golden
cases regress (fail) and which new cases appear (pass golden but unexpected).

Usage:
    python scripts/diff_routing.py [--update] [--key SARVAM_API_KEY]

    --update   Rewrite golden_cases.yaml with the live results (use after
               intentionally changing routing behaviour — this is a one-liner
               to update the golden file, not automatic).
    --key      Override the LLM API key from the environment.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/routing/golden_cases.yaml"
CASES_FILE = ROOT / "tests/core/test_routing_regression.py"


def load_golden_cases() -> list[dict]:
    if not FIXTURE.exists():
        print(f"Error: fixture not found: {FIXTURE}")
        sys.exit(1)
    with open(FIXTURE) as f:
        return yaml.safe_load(f)


def _classify(message: str, previous: str | None, api_key: str):
    from core.llm.client import complete_json_object
    from core.llm.config import resolve_llm_from_env
    from core.pipeline.orchestration import frame_message, parse_routing
    from core.pipeline.stages.route import _orchestrator_prompt

    # resolve_llm_from_env reads SARVAM_API_KEY from env; set it from our arg.
    old_key = os.environ.get("SARVAM_API_KEY")
    os.environ["SARVAM_API_KEY"] = api_key
    try:
        llm, err = resolve_llm_from_env(force="sarvam")
    finally:
        if old_key is None:
            del os.environ["SARVAM_API_KEY"]
        else:
            os.environ["SARVAM_API_KEY"] = old_key
    if llm is None:
        raise RuntimeError(f"resolve_llm_from_env failed: {err}")
    framed = frame_message(message, previous)
    raw = asyncio.run(
        complete_json_object(user_message=framed, system_prompt=_orchestrator_prompt(), llm=llm)
    )
    return parse_routing(raw, previous)


def main():
    parser = argparse.ArgumentParser(description="Diff golden routing cases against live LLM")
    parser.add_argument("--update", action="store_true", help="Update golden_cases.yaml with live results")
    parser.add_argument("--key", dest="api_key", default=None, help="Override LLM API key")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("SARVAM_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: set SARVAM_API_KEY (or --key)")
        sys.exit(1)

    cases = load_golden_cases()
    print(f"Comparing {len(cases)} golden cases against live orchestrator...\n")

    regressions = []
    updated_cases = []

    for case in cases:
        message = case["message"]
        previous = case.get("previous")
        expected_task = case["expected_task_type"]
        expected_providers = case.get("expected_providers")

        try:
            result = _classify(message, previous, api_key)
        except Exception as exc:
            print(f"  [ERROR] {message!r}: {exc}")
            regressions.append((case, None, str(exc)))
            updated_cases.append({**case, "_error": str(exc)})
            continue

        task_ok = result.task_type == expected_task
        providers_ok = (
            expected_providers is None
            or set(result.providers) == set(expected_providers)
        )
        ok = task_ok and providers_ok

        status = "✅" if ok else "❌"
        if not ok:
            regressions.append((case, result, None))
            delta = {
                **case,
                "_live_task": result.task_type,
                "_live_providers": result.providers,
                "_live_reasoning": result.reasoning,
            }
            updated_cases.append(delta)
        else:
            updated_cases.append(case)

        prov_str = expected_providers if expected_providers is not None else "any"
        print(f"  {status} {message!r}")
        if not task_ok:
            print(f"       task_type: got {result.task_type!r}, expected {expected_task!r}")
        if not providers_ok:
            print(f"       providers: got {result.providers}, expected {prov_str}")

    print(f"\n{'='*60}")
    if regressions:
        print(f"❌ {len(regressions)} regression(s) out of {len(cases)} golden cases")
        print("\nTo update the golden file with these live results, run:")
        print(f"  python scripts/diff_routing.py --update --key YOUR_KEY")
        sys.exit(1)
    else:
        print(f"✅ All {len(cases)} golden cases passed — no regressions")

    if args.update:
        with open(FIXTURE, "w") as f:
            yaml.dump(updated_cases, f, allow_unicode=True, sort_keys=False)
        print(f"\nUpdated {FIXTURE}")


if __name__ == "__main__":
    main()
