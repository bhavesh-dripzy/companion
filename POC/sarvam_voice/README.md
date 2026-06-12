# Sarvam Half-Cascade Voice PoC

Standalone proof-of-concept for replacing Gemini Live voice with a Sarvam-native
cascade: **Saaras v3 STT → LLM (Sarvam-M / DeepSeek) → Bulbul TTS**. Touches
nothing in prod — single self-contained script, run with `uv`.

## Why (June 2026 research)

Gemini Live re-bills the entire session context every generation → ₹100 for a
15-min voice session. The Sarvam cascade has no re-billing concept and prices at
roughly **₹0.7–0.9/min all-in** (STT ₹30/hr, Bulbul v2 ₹15/10k chars, LLM
pennies), with Hinglish as a first-class feature. See memory note
`voice-stack-cost-research`.

## Setup

```bash
# 1. Key: sign up at https://dashboard.sarvam.ai (free credits on signup)
export SARVAM_API_KEY=...

# 2. THE WAY TO TEST IT: the web console — talk in the browser, hear Bulbul,
#    watch result cards render (the Gemini voice experience, Sarvam-powered)
cd ~/dev/companion
.venv/bin/python poc/sarvam_voice/server.py
open http://localhost:8765        # tap the orb, allow mic, talk Hinglish

# or the CLI variant of the same pipeline
.venv/bin/python poc/sarvam_voice/sarvam_voice_poc.py
```

The web console shows live captions, tool-call chips, generic result cards
(restaurants/menus/movies/events + attachment images + raw `tool_execution`
JSON), per-turn latency, and a fare meter comparing your real ₹ spend against
the ~₹6.7/min Gemini Live burn. Both run REAL tools by default. Browser mic
needs permission; real-tools mode needs live AWS creds for the `companion`
profile (`aws sso login` if expired). `server.py` takes the same flags as the
CLI plus `--port`.

## What it exercises

The same conversational pattern as prod voice mode:

- VAD-segmented mic capture (16kHz mono, ~900ms silence ends a turn)
- STT in `translit` mode → romanized Hinglish transcript (what prod's
  `voice_romanize_endpoint` does today as a separate LLM call — free here)
- Streaming LLM with the prod Hinglish persona, **sentence-boundary TTS
  pipelining** (first sentence speaks while the rest still generates)
- The tool-call pattern: LLM emits `TOOL: <intent>` → instant canned status cue
  ("Ek second, dekh raha hoon...") → **the real core pipeline runs in-process**
  (`run_core_turn_sync`, same call `voice_tool_execute_endpoint` makes for
  Gemini voice — real Zomato/serp/MCP tools, MCP tokens restored from Secrets
  Manager for `--user`) → the pipeline's Hinglish narration is spoken directly.
  Session state (cart, restaurants, menus, reservations...) carries across
  turns exactly like prod, so multi-turn food ordering works. `--tools mock`
  falls back to a canned result (no AWS needed).
- Per-turn latency report (`stt`, `llm_first`, `time-to-first-audio`) and a
  running ₹ cost meter with ₹/min extrapolation

## Modes & flags

| Flag | Purpose |
|---|---|
| `--text "..."` | one typed turn (no mic needed), still speaks |
| `--wav clip.wav` | transcribe a file as turn 1, then continue on mic |
| `--tool-demo` | scripted actionable turn (pizza order) end-to-end |
| `--no-audio` | skip playback — safe anywhere, still synthesizes + meters |
| `--tts-model bulbul:v3` | A/B the newer (2x price, beta) voices vs v2 |
| `--speaker <name>` | v2: abhilash/karun/hitesh/anushka/manisha/vidya/arya; v3: shubh + 30 more |
| `--no-tts-translit` | disable the Roman→Devanagari transliteration before TTS (on by default — Bulbul mispronounces romanized Hindi; live finding 2026-06-12; costs ₹20/10k chars, ~+300ms TTFA) |
| `--tts-script deva` | LLM outputs Devanagari instead of Roman Hinglish — A/B which Bulbul pronounces better |
| `--stt-mode codemix` | English words in Latin + Hindi in Devanagari (vs default `translit` = all-Roman) |
| `--llm deepseek` | swap the brain to DeepSeek (`DEEPSEEK_API_KEY`) — proves the LLM slot is pluggable |
| `--llm-model <id>` | e.g. `sarvam-30b` / `sarvam-105b` (on 4xx the script prints the endpoint's model list) |
| `--tools mock` | canned tool result instead of the real core pipeline (no AWS needed) |
| `--user <id>` | core-pipeline user with vaulted MCP tokens (default `admin_bhavesh`) |
| `--geo "28.52,77.21"` | lat,lng for location-aware tools (stands in for browser GPS) |
| `--tool-delay 10` | mock-mode backend latency (real mode measures the actual core turn) |

## What to judge (the PoC questions)

1. **STT**: does `translit` mode nail your real Hinglish speech (food orders,
   place names, code-mixed sentences)? Try noisy room + AirPods.
2. **TTS**: ~~does Bulbul speak Roman-script Hinglish?~~ **Answered: no** —
   it reads romanized Hindi as English. The pipeline now transliterates to
   Devanagari before TTS by default (captions/history stay Roman). Still
   judge: is Bulbul v3 worth 2x? Which speaker fits the persona?
3. **Latency**: time-to-first-audio per turn. Gemini Live feels ~1–2s; the
   cascade adds STT (~300–800ms) — is the result acceptable?
4. **Cost**: the meter prints ₹/min live. Research predicts ₹0.7–0.9/min vs
   ₹6.7/min current Gemini burn.

## Known limits (deliberate PoC cuts)

- No barge-in: playback blocks the mic (prod parity would need echo
  cancellation or half-duplex gating — LiveKit/Pipecat territory).
- Sarvam has no streaming STT endpoint (REST, <30s clips), so STT cost is
  billed per utterance-second — silence is never billed, unlike Gemini Live.
- LLM token costs are estimated (chars/4), labelled `est` in the meter; the
  meter covers the voice layer only (core-pipeline LLM/tool costs are prod's).
- Ordering still stops at the core pipeline's confirmation gate — read-only
  flows (search, menus, status) are safe to demo end-to-end.
