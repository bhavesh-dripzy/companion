"""Sarvam eval: Companion's real JSON workload against Sarvam managed models.

Runs the ACTUAL orchestrator system prompt + JSON parser + routing validator
(core.pipeline) through Sarvam and measures what benchmarks can't for THIS
workload:
  1. first-attempt JSON validity (no re-ask crutch)
  2. routing correctness vs expected task_type (planning-quality proxy)
  3. a Hinglish JSON-shape stress test (Indic + structured-output together)
  4. latency per call

Keys load in-process from the companion/app secret via boto3 (same path prod
uses), so no key touches the shell. sarvam-m was deprecated mid-2026; the
current managed models are sarvam-30b / sarvam-105b.

Run:  AWS_PROFILE=companion AWS_DEFAULT_REGION=ap-south-1 \
      APP_SECRET_ID=companion/app .venv/bin/python scripts/sarvam_pipeline_eval.py
Pick models with AB_SARVAM_MODELS=sarvam-30b,sarvam-105b (comma-separated).
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass

os.environ.setdefault("AWS_PROFILE", "companion")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("APP_SECRET_ID", "companion/app")

from companion_api.secrets import secret_or_env
from core.llm.client import _provider_call
from core.llm.config import SARVAM_BASE_URL, ResolvedLlm
from core.llm.json_mode import parse_json_object
from core.pipeline.orchestration import KNOWN_TASKS, frame_message, parse_routing
from core.pipeline.stages.route import _orchestrator_prompt

# sarvam-m (24B) is deprecated on the managed API (confirmed live 2026-06:
# 400 "Model 'sarvam-m' has been deprecated"). Current managed models only.
SARVAM_MODEL_CANDIDATES = ["sarvam-30b", "sarvam-105b"]

# (message, set-of-acceptable task_types). Acceptable sets are generous where a
# turn is genuinely ambiguous; the point is gross misroutes, not nitpicks.
ROUTING_CASES: list[tuple[str, set[str]]] = [
    ("What's latest in geopolitics?", {"data_lookup"}),
    ("what's happening in indian politics", {"data_lookup"}),
    ("Suggest some nice geopolitics indian videos", {"data_lookup"}),
    ("latest cricket news", {"data_lookup"}),
    ("order biryani from zomato", {"food_delivery"}),
    ("I'm hungry, get me a pizza", {"food_delivery"}),
    ("show me good restaurants near me", {"food_delivery"}),
    ("book a cab to the airport", {"cab_booking"}),
    ("what's 2 + 2", {"normal_query"}),
    ("who was the first president of india", {"normal_query"}),
    ("remind me to call mom at 6pm", {"reminder_calendar"}),
    ("send a whatsapp to rahul saying i'll be late", {"whatsapp_message"}),
    ("show me movies near me this weekend", {"going_out"}),
    ("how do I get to the nearest metro station", {"maps_query", "data_lookup"}),
    # Hinglish / Indic — the consumer reality
    ("mujhe paas mein kuch achhe restaurants dikhao", {"food_delivery"}),
    ("zomato pe paneer butter masala order kar do", {"food_delivery"}),
    ("kal subah 7 baje gym ka reminder laga do", {"reminder_calendar"}),
    ("aaj cricket match ka score kya hai", {"data_lookup"}),
]

# Structured-output stress: a strict-JSON extraction prompt with Hinglish input.
# Scores valid JSON + presence of every required key (schema conformance).
STRESS_SYSTEM = (
    "You are an extraction engine. Read the user's message and reply with ONLY a "
    "JSON object, no prose, with EXACTLY these keys: "
    '"intent" (string), "city" (string or null), "items" (array of strings), '
    '"party_size" (integer or null), "language" (one of "en","hi","hinglish"). '
    "Do not add any other keys."
)
STRESS_REQUIRED = {"intent", "city", "items", "party_size", "language"}
STRESS_CASES: list[str] = [
    "Mumbai mein 4 logo ke liye ek acchi biryani aur 2 coke order karna hai",
    "book a table for two at an italian place in bangalore tonight",
    "kal Delhi ja raha hoon, ek cab chahiye airport se hotel tak",
    "I want to watch the new SRK movie this Friday in Pune with my family",
]


@dataclass
class CallResult:
    ok: bool  # transport succeeded (no exception)
    valid_json: bool  # first attempt parsed as a JSON object
    latency_s: float
    detail: str  # routed task_type / key-check / error
    raw: str


async def _one_call(llm: ResolvedLlm, system_prompt: str, user_message: str) -> CallResult:
    t0 = time.perf_counter()
    try:
        content = await _provider_call(
            llm=llm, system_prompt=system_prompt, user_message=user_message
        )
    except Exception as exc:  # transport / API error is itself a finding
        return CallResult(
            False, False, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}", ""
        )
    dt = time.perf_counter() - t0
    try:
        parse_json_object(content)  # raises ValueError if not a JSON object
        return CallResult(True, True, dt, "", content)
    except ValueError:
        return CallResult(True, False, dt, "INVALID_JSON", content)


def _sarvam_models() -> list[str]:
    """Models to eval: AB_SARVAM_MODELS (comma-sep) overrides the default list."""
    env = os.getenv("AB_SARVAM_MODELS", "").strip()
    return [m.strip() for m in env.split(",") if m.strip()] or SARVAM_MODEL_CANDIDATES


async def run_suite(name: str, llm: ResolvedLlm) -> dict:
    print(f"\n=== {name}  (model={llm.model}) ===")
    orch = _orchestrator_prompt()
    routing_rows = []
    for msg, accept in ROUTING_CASES:
        r = await _one_call(llm, orch, frame_message(msg, None))
        routed = None
        match = False
        if r.valid_json:
            try:
                routed = parse_routing(parse_json_object(r.raw), None).task_type
                match = routed in accept
            except Exception:
                routed = "PARSE_ROUTING_ERR"
        flag = "ok " if (r.valid_json and match) else ("JSON" if not r.valid_json else "MISS")
        print(
            f"  [{flag}] {r.latency_s:5.2f}s  routed={routed!s:<18} "
            f"want={'/'.join(sorted(accept))!s:<22} | {msg[:46]}"
        )
        routing_rows.append(
            {
                "msg": msg,
                "accept": sorted(accept),
                "routed": routed,
                "valid_json": r.valid_json,
                "match": match,
                "ok": r.ok,
                "latency_s": round(r.latency_s, 3),
                "detail": r.detail,
            }
        )

    stress_rows = []
    for msg in STRESS_CASES:
        r = await _one_call(llm, STRESS_SYSTEM, msg)
        keys_ok = False
        keys = []
        if r.valid_json:
            try:
                keys = sorted(parse_json_object(r.raw).keys())
                keys_ok = STRESS_REQUIRED.issubset(set(keys))
            except Exception:
                pass
        flag = "ok " if keys_ok else ("JSON" if not r.valid_json else "KEYS")
        print(f"  [{flag}] {r.latency_s:5.2f}s  keys={keys} | {msg[:42]}")
        stress_rows.append(
            {
                "msg": msg,
                "valid_json": r.valid_json,
                "keys": keys,
                "keys_ok": keys_ok,
                "ok": r.ok,
                "latency_s": round(r.latency_s, 3),
                "detail": r.detail,
                "raw": r.raw[:400],
            }
        )

    return {"model": llm.model, "routing": routing_rows, "stress": stress_rows}


def summarize(name: str, res: dict) -> dict:
    rt = res["routing"]
    st = res["stress"]
    n_rt = len(rt)
    json_ok = sum(1 for r in rt if r["valid_json"])
    route_ok = sum(1 for r in rt if r["match"])
    transport_ok = sum(1 for r in rt if r["ok"])
    lat = [r["latency_s"] for r in rt if r["ok"]]
    stress_json = sum(1 for r in st if r["valid_json"])
    stress_keys = sum(1 for r in st if r["keys_ok"])
    s = {
        "model": res["model"],
        "routing_n": n_rt,
        "transport_ok": transport_ok,
        "json_valid": json_ok,
        "route_match": route_ok,
        "stress_n": len(st),
        "stress_json_valid": stress_json,
        "stress_keys_ok": stress_keys,
        "latency_median": round(statistics.median(lat), 3) if lat else None,
        "latency_p90": round(sorted(lat)[int(len(lat) * 0.9)], 3) if len(lat) > 1 else None,
    }
    print(f"\n--- {name} summary ---")
    print(f"  transport ok      : {transport_ok}/{n_rt}")
    print(f"  first-try JSON ok : {json_ok}/{n_rt}")
    print(f"  routing correct   : {route_ok}/{n_rt}")
    print(f"  stress JSON ok    : {stress_json}/{len(st)}")
    print(f"  stress keys ok    : {stress_keys}/{len(st)}")
    print(f"  latency med/p90   : {s['latency_median']}s / {s['latency_p90']}s")
    return s


async def main() -> None:
    print(f"KNOWN_TASKS = {sorted(KNOWN_TASKS)}")
    sv_key = secret_or_env("SARVAM_API_KEY")
    if not sv_key:
        print("\n!! SARVAM_API_KEY missing from companion/app secret — nothing to eval.")
        return

    models = _sarvam_models()
    print(f"Sarvam models to eval: {models}")
    results: dict = {}
    summaries: dict = {}
    for model in models:
        llm = ResolvedLlm("sarvam", sv_key, model, base_url=SARVAM_BASE_URL)
        res = await run_suite(f"SARVAM ({model})", llm)
        results[model] = res
        summaries[model] = summarize(f"SARVAM ({model})", res)

    out = "/tmp/sarvam_pipeline_eval.json"
    with open(out, "w") as f:
        json.dump({"summaries": summaries, "results": results}, f, indent=2)
    print(f"\nRaw results -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
