"""Local web console for the Sarvam half-cascade voice PoC.

Serves a single-page voice UI (index.html) and a WebSocket that runs the same
turn pipeline as the CLI PoC — Saaras STT -> LLM -> real core pipeline tools ->
Bulbul TTS — streaming captions, audio chunks, result cards, latency metrics,
and a live ₹ meter to the browser. Prod-untouched; run from repo root:

  SARVAM_API_KEY=... .venv/bin/python poc/sarvam_voice/server.py
  open http://localhost:8765
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sarvam_voice_poc import (
    PRICES,
    TOOLS_SPEC,
    MOCK_TOOL_RESULT,
    PERSONA,
    PERSONA_DEVA,
    PERSONA_F,
    STATUS_CUES,
    STATUS_CUES_F,
    CoreToolExecutor,
    CostMeter,
    llm_stream,
    sarvam_stt,
    sarvam_tts,
    sentences,
    spoken_form,
    windowed,
)

MOCK_CARDS = [
    {"name": "Domino's Pizza", "rating": 4.2, "price": "₹199", "eta": "25 min", "offer": "20% off"},
    {"name": "Pizza Hut", "rating": 4.0, "price": "₹249", "eta": "30 min"},
    {"name": "La Pino'z", "rating": 4.3, "price": "₹179", "eta": "35 min"},
]

CARD_SOURCES = (
    ("restaurants", "session_restaurants"), ("menu", "session_menu"),
    ("movies", "session_movies"), ("theatres", "session_theatres"),
    ("events", "session_events"), ("venues", "session_venues"),
    ("artists", "session_artists"),
)

app = FastAPI(title="sarvam-voice-poc")
ARGS: argparse.Namespace
KEY: str
LLM_CFG: dict
EXECUTOR: CoreToolExecutor | None = None


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")


def turn_events(state: dict, user_text: str, emit, t0: float):
    """Run one full conversational turn (blocking), emitting UI events as it goes."""
    meter: CostMeter = state["meter"]
    messages: list = state["messages"]
    messages.append({"role": "user", "content": user_text})
    emit({"type": "phase", "phase": "thinking"})

    voice = state["voice"]

    def speak_stream(sent_iter, label):
        t_first = None
        for sent in sent_iter:
            wav = sarvam_tts(KEY, sent, voice["model"], voice["speaker"], meter,
                             to_deva=ARGS.tts_translit, pace=voice["pace"],
                             temperature=voice["temperature"])
            if t_first is None:
                t_first = time.monotonic()
                emit({"type": "phase", "phase": "speaking"})
            emit({"type": "audio", "wav_b64": base64.b64encode(wav).decode(), "text": sent, "label": label})
        return t_first

    tool_out: dict = {}
    deltas = llm_stream(LLM_CFG, windowed(messages), meter, tools=TOOLS_SPEC, tool_out=tool_out)
    gen, holder = sentences(deltas, min_chars=ARGS.tts_chunk)
    t_first = speak_stream(gen, "reply")
    spoke = holder["text"].strip()

    intent = tool_out.get("intent")
    if not intent:
        messages.append({"role": "assistant", "content": spoke})
        emit({"type": "metrics", "ttfa_ms": (t_first - t0) * 1000 if t_first else None})
        return

    emit({"type": "tool", "intent": intent})
    if not spoke:
        cues = STATUS_CUES_F if ARGS.persona_gender == "f" else STATUS_CUES
        speak_stream(iter([cues[len(messages) % len(cues)]]), "cue")
    emit({"type": "phase", "phase": "thinking"})
    t_tool = time.monotonic()
    if EXECUTOR is not None:
        hist = [m for m in messages if m["role"] in ("user", "assistant")]
        result = EXECUTOR.execute(intent, hist)
        narration = result.get("assistant_message") or result.get("error") or "Kuch result nahi mila."
    else:
        time.sleep(ARGS.tool_delay)
        result = {"session_restaurants": MOCK_CARDS, "tool_execution": {"mock": True}}
        narration = MOCK_TOOL_RESULT
    tool_secs = time.monotonic() - t_tool
    cards = [
        {"kind": kind, "items": result[key][:8]}
        for kind, key in CARD_SOURCES
        if isinstance(result.get(key), list) and result[key]
    ]
    emit({
        "type": "results",
        "cards": cards,
        "images": result.get("attachments") or [],
        "raw": result.get("tool_execution"),
        "provider": result.get("routed_provider"),
    })
    speech = spoken_form(narration) if ARGS.narration_style == "brief" else narration
    t_r = time.monotonic()
    t_first2 = speak_stream(sentences(iter([speech]), ARGS.tts_chunk)[0], "narration")
    messages.append({"role": "assistant",
                     "content": f"{spoke} [action: {intent}] {narration[:240]}".strip()})
    emit({"type": "metrics", "ttfa_ms": (t_first - t0) * 1000 if t_first else None,
          "core_s": tool_secs,
          "result_ttfa_ms": (t_first2 - t_r) * 1000 if t_first2 else None})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_running_loop()
    persona = PERSONA_DEVA if ARGS.tts_script == "deva" else (
        PERSONA_F if ARGS.persona_gender == "f" else PERSONA)
    llm_rates = PRICES["llm_per_1m_deepseek"] if ARGS.llm == "deepseek" else PRICES["llm_per_1m"]
    state = {
        "messages": [{"role": "system", "content": persona}],
        "meter": CostMeter(ARGS.tts_model, llm_rates),
        "voice": {"model": ARGS.tts_model, "speaker": ARGS.speaker,
                  "pace": 1.0, "temperature": 0.8},
    }

    def emit(ev: dict):
        asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps(ev)), loop).result()

    await ws.send_text(json.dumps({
        "type": "hello",
        "config": {"engine": "sarvam", "llm": LLM_CFG["model"], "tts": f"{ARGS.tts_model}/{ARGS.speaker}" + ("+translit" if ARGS.tts_translit else ""),
                   "stt": f"saaras:v3/{ARGS.stt_mode}", "tools": ARGS.tools, "user": ARGS.user},
    }))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t0 = time.monotonic()
            meter: CostMeter = state["meter"]
            try:
                if msg["type"] == "config":
                    v = state["voice"]
                    v["model"] = msg.get("tts_model") or v["model"]
                    v["speaker"] = msg.get("speaker") or v["speaker"]
                    v["pace"] = min(max(float(msg.get("pace") or v["pace"]), 0.5), 2.0)
                    # bulbul:v3 rejects temperature > 1.0 (API-validated 2026-06-12)
                    v["temperature"] = min(max(float(msg.get("temperature") or v["temperature"]), 0.01), 1.0)
                    await ws.send_text(json.dumps({"type": "config_ok", "voice": v}))
                    continue
                if msg["type"] == "audition":
                    v = state["voice"]
                    line = msg.get("text") or (
                        "Arre yaar, scene set hai! Aaj shaam pizza order karein "
                        "ya bahar chalein? Tum bolo, main ready hoon."
                    )
                    wav = await asyncio.to_thread(
                        sarvam_tts, KEY, line, v["model"], v["speaker"], meter,
                        ARGS.tts_translit, v["pace"], v["temperature"])
                    await ws.send_text(json.dumps({
                        "type": "audio", "wav_b64": base64.b64encode(wav).decode(),
                        "text": line, "label": "audition"}))
                elif msg["type"] == "utterance":
                    wav = base64.b64decode(msg["wav_b64"])
                    transcript, stt_ms = await asyncio.to_thread(
                        sarvam_stt, KEY, wav, ARGS.stt_mode, meter)
                    if not transcript:
                        await ws.send_text(json.dumps({"type": "phase", "phase": "listening"}))
                        continue
                    await ws.send_text(json.dumps(
                        {"type": "user_transcript", "text": transcript, "stt_ms": stt_ms}))
                    await asyncio.to_thread(turn_events, state, transcript, emit, t0)
                elif msg["type"] == "text":
                    await ws.send_text(json.dumps({"type": "user_transcript", "text": msg["text"]}))
                    await asyncio.to_thread(turn_events, state, msg["text"], emit, t0)
            except Exception as e:
                await ws.send_text(json.dumps({"type": "error", "message": str(e)[:500]}))
            await ws.send_text(json.dumps({
                "type": "done",
                "cost": {"total": round(meter.total(), 3), "summary": meter.summary()},
            }))
    except WebSocketDisconnect:
        pass


def main():
    global ARGS, KEY, LLM_CFG, EXECUTOR
    ap = argparse.ArgumentParser(description="Sarvam voice PoC web console")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--stt-mode", default="translit", choices=["translit", "codemix", "transcribe", "verbatim"])
    ap.add_argument("--tts-model", default="bulbul:v3", choices=["bulbul:v2", "bulbul:v3"])
    ap.add_argument("--speaker", default=None)
    ap.add_argument("--tts-script", default="roman", choices=["roman", "deva"])
    ap.add_argument("--tts-translit", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--llm", default="deepseek", choices=["deepseek", "sarvam"])
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--tools", default="real", choices=["real", "mock"])
    ap.add_argument("--user", default="admin_bhavesh")
    ap.add_argument("--geo", default=None)
    ap.add_argument("--tool-delay", type=float, default=2.5)
    ap.add_argument("--narration-style", default="brief", choices=["brief", "full"])
    ap.add_argument("--tts-chunk", type=int, default=220)
    ap.add_argument("--persona-gender", default="f", choices=["f", "m"],
                    help="Hindi verb forms for the assistant (default f — matches priya)")
    ARGS = ap.parse_args()
    ARGS.speaker = ARGS.speaker or ("abhilash" if ARGS.tts_model == "bulbul:v2" else "priya")

    KEY = os.environ.get("SARVAM_API_KEY", "")
    if not KEY:
        sys.exit("SARVAM_API_KEY not set — get one at https://dashboard.sarvam.ai")
    if ARGS.llm == "deepseek":
        ds = os.environ.get("DEEPSEEK_API_KEY", "")
        if not ds:
            sys.exit("DEEPSEEK_API_KEY not set")
        LLM_CFG = {"base": "https://api.deepseek.com/v1", "key": ds, "model": ARGS.llm_model or "deepseek-chat"}
    else:
        LLM_CFG = {"base": "https://api.sarvam.ai/v1", "key": KEY, "model": ARGS.llm_model or "sarvam-30b"}

    if ARGS.tools == "real":
        geo = None
        if ARGS.geo:
            lat, lng = (float(x) for x in ARGS.geo.split(","))
            geo = {"lat": lat, "lng": lng}
        print(f"bootstrapping core pipeline (user={ARGS.user}, restoring MCP tokens)...")
        EXECUTOR = CoreToolExecutor(ARGS.user, geo)

    import uvicorn

    print(f"\n  🎙  http://localhost:{ARGS.port}  (tools={ARGS.tools}, llm={LLM_CFG['model']}, "
          f"tts={ARGS.tts_model}/{ARGS.speaker})\n")
    uvicorn.run(app, host="127.0.0.1", port=ARGS.port, log_level="warning")


if __name__ == "__main__":
    main()
