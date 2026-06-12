"""Gemini Live PoC web console — the OPTIMIZED counterpart to poc/sarvam_voice.

Serves the same single-page UI and browser protocol as the Sarvam console
(port 8766 vs 8765, run both for an apples-to-apples test), but the engine is
Gemini Live with every fix from the June 2026 cost diagnosis applied:

  1. context_window_compression (sliding window) — caps per-turn re-billing,
     removes the 15-min session cap
  2. no silence streaming — automatic VAD off; the browser's client-side VAD
     sends one utterance per turn wrapped in activityStart/activityEnd
  3. no nudge generation — the persona speaks its own one-line status cue in
     the same generation as the tool call (NON_BLOCKING + INTERRUPT result)
  4. real metering — the fare meter prices Gemini's own usageMetadata per
     modality instead of estimating

Run from repo root:
  GEMINI_API_KEY=... .venv/bin/python poc/gemini_voice/server.py
  open http://localhost:8766
"""

import argparse
import asyncio
import base64
import io
import json
import os
import sys
import time
import wave
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sarvam_voice"))
from sarvam_voice_poc import CoreToolExecutor, MOCK_TOOL_RESULT

MOCK_CARDS = [
    {"name": "Domino's Pizza", "rating": 4.2, "price": "₹199", "eta": "25 min"},
    {"name": "Pizza Hut", "rating": 4.0, "price": "₹249", "eta": "30 min"},
]
CARD_SOURCES = (
    ("restaurants", "session_restaurants"), ("menu", "session_menu"),
    ("movies", "session_movies"), ("theatres", "session_theatres"),
    ("events", "session_events"), ("venues", "session_venues"),
    ("artists", "session_artists"),
)

USD_INR = 85.0
RATES_PER_1M_USD = {
    ("in", "TEXT"): 0.75, ("in", "AUDIO"): 3.00,
    ("out", "TEXT"): 4.50, ("out", "AUDIO"): 12.00,
}

PERSONA = (
    "You are Companion, a warm, funny voice assistant for users in India. "
    "Speak ONLY in Hinglish — conversational Hindi in the Roman/Latin alphabet, English "
    "words where natural. Keep replies SHORT — one to three sentences, like a dost. "
    "You can actually DO things through the companion_execute function — order food, book "
    "cabs and tables, find restaurants, movies and events, fetch live data, reminders. "
    "Call it for ANY action or live-data request; never explain how to do it manually and "
    "never invent live data. The moment you call it, FIRST say ONE short status line like "
    "'Ek second, dekh raha hoon...' so the user is never left in silence. When the result "
    "arrives, speak a brief summary — details show on screen as cards."
)

app = FastAPI(title="gemini-voice-poc")
ARGS: argparse.Namespace
KEY: str
EXECUTOR: CoreToolExecutor | None = None


class GeminiMeter:
    """Rupee meter fed by the Live API's own usageMetadata, per modality."""

    def __init__(self):
        self.tokens: dict[tuple, int] = {k: 0 for k in RATES_PER_1M_USD}
        self.started = time.monotonic()

    def add(self, usage) -> None:
        for direction, details in (("in", usage.prompt_tokens_details),
                                   ("out", usage.response_tokens_details)):
            for d in details or []:
                key = (direction, str(d.modality or "").upper().replace("MODALITY.", ""))
                if key in self.tokens and d.token_count:
                    self.tokens[key] += d.token_count

    def total(self) -> float:
        return sum(n * RATES_PER_1M_USD[k] / 1e6 * USD_INR for k, n in self.tokens.items())

    def summary(self) -> str:
        mins = max((time.monotonic() - self.started) / 60, 1 / 60)
        t = self.tokens
        return (
            f"session ₹{self.total():.3f} (~₹{self.total() / mins:.2f}/min) | "
            f"in audio {t[('in', 'AUDIO')]} + text {t[('in', 'TEXT')]} tok, "
            f"out audio {t[('out', 'AUDIO')]} + text {t[('out', 'TEXT')]} tok (real usageMetadata)"
        )


def pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return w.readframes(w.getnframes())


def live_config(types):
    """The optimized LiveConnectConfig — compression + manual VAD + transcripts."""
    tool = types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="companion_execute",
            behavior=types.Behavior.NON_BLOCKING,
            description=(
                "Execute actions for the user: order food, book cabs/tables, find "
                "restaurants/movies/events, live data, reminders. Call this for ANY "
                "action or live-data request."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={"intent": types.Schema(
                    type="STRING",
                    description="Full natural-language intent with all relevant details.")},
                required=["intent"],
            ),
        )
    ])
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=types.Content(parts=[types.Part(text=PERSONA)]),
        tools=[tool],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=ARGS.voice))),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=ARGS.trigger_tokens,
            sliding_window=types.SlidingWindow(target_tokens=ARGS.target_tokens),
        ),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        ),
    )


@app.get("/")
def index():
    return FileResponse(Path(__file__).resolve().parent.parent / "sarvam_voice" / "index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=KEY, http_options={"api_version": "v1alpha"})
    meter = GeminiMeter()
    state = {"t0": time.monotonic(), "spoke_first": False,
             "user_cap": "", "asst_cap": "", "asst_turn": "", "history": []}

    async def send(ev: dict):
        await ws.send_text(json.dumps(ev))

    await send({"type": "hello", "config": {
        "llm": ARGS.model, "tts": f"native-audio/{ARGS.voice}", "stt": "native",
        "tools": ARGS.tools, "user": ARGS.user, "engine": "gemini"}})

    try:
        await run_live(ws, client, types, meter, state, send)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await send({"type": "error", "message": f"Gemini Live failed: {e}"[:400]})
            await ws.close()
        except Exception:
            pass


async def run_live(ws, client, types, meter, state, send):
    async with client.aio.live.connect(model=ARGS.model, config=live_config(types)) as session:

        async def flush_audio(buf: bytearray):
            if not buf:
                return
            if not state["spoke_first"]:
                state["spoke_first"] = True
                await send({"type": "phase", "phase": "speaking"})
                await send({"type": "metrics",
                            "ttfa_ms": (time.monotonic() - state["t0"]) * 1000})
            await send({"type": "audio",
                        "wav_b64": base64.b64encode(pcm_to_wav(bytes(buf))).decode(),
                        "text": state["asst_cap"], "label": "reply"})
            state["asst_cap"] = ""
            buf.clear()

        async def handle_tool(fcs):
            intents = [(fc.id, fc.name, (fc.args or {}).get("intent", "")) for fc in fcs]
            for _, _, intent in intents:
                await send({"type": "tool", "intent": intent})
            t_tool = time.monotonic()
            if EXECUTOR is not None:
                hist = state["history"][-8:]
                result = await asyncio.to_thread(EXECUTOR.execute, intents[0][2], hist)
                narration = (result.get("assistant_message") or result.get("error")
                             or "Kuch result nahi mila.")
            else:
                await asyncio.sleep(ARGS.tool_delay)
                result = {"session_restaurants": MOCK_CARDS, "tool_execution": {"mock": True}}
                narration = MOCK_TOOL_RESULT
            cards = [{"kind": kind, "items": result[key][:8]}
                     for kind, key in CARD_SOURCES
                     if isinstance(result.get(key), list) and result[key]]
            await send({"type": "results", "cards": cards,
                        "images": result.get("attachments") or [],
                        "raw": result.get("tool_execution"),
                        "provider": result.get("routed_provider")})
            await send({"type": "metrics", "core_s": time.monotonic() - t_tool})
            await session.send_tool_response(function_responses=[
                types.FunctionResponse(
                    id=fid, name=name, response={"result": narration},
                    scheduling=types.FunctionResponseScheduling.INTERRUPT)
                for fid, name, _ in intents])

        async def gemini_pump():
            audio_buf = bytearray()
            while True:
                async for msg in session.receive():
                    if msg.usage_metadata:
                        meter.add(msg.usage_metadata)
                    if msg.tool_call and msg.tool_call.function_calls:
                        asyncio.create_task(handle_tool(msg.tool_call.function_calls))
                        continue
                    sc = msg.server_content
                    if not sc:
                        continue
                    if sc.input_transcription and sc.input_transcription.text:
                        state["user_cap"] += sc.input_transcription.text
                    if sc.output_transcription and sc.output_transcription.text:
                        state["asst_cap"] += sc.output_transcription.text
                        state["asst_turn"] += sc.output_transcription.text
                    if sc.model_turn:
                        for part in sc.model_turn.parts or []:
                            if part.inline_data and part.inline_data.data:
                                audio_buf.extend(part.inline_data.data)
                                if len(audio_buf) >= 48000:
                                    await flush_audio(audio_buf)
                    if sc.interrupted or sc.turn_complete:
                        await flush_audio(audio_buf)
                        if state["user_cap"].strip():
                            await send({"type": "user_transcript",
                                        "text": state["user_cap"].strip()})
                            state["history"].append(
                                {"role": "user", "content": state["user_cap"].strip()})
                            state["user_cap"] = ""
                        if sc.turn_complete:
                            if state["asst_turn"].strip():
                                state["history"].append(
                                    {"role": "assistant",
                                     "content": state["asst_turn"].strip()[:240]})
                                state["asst_turn"] = ""
                            state["spoke_first"] = False
                            await send({"type": "phase", "phase": "listening"})
                            await send({"type": "done", "cost": {
                                "total": round(meter.total(), 3),
                                "summary": meter.summary()}})

        pump = asyncio.create_task(gemini_pump())
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                state["t0"] = time.monotonic()
                state["spoke_first"] = False
                if msg["type"] == "utterance":
                    pcm = wav_to_pcm(base64.b64decode(msg["wav_b64"]))
                    await send({"type": "phase", "phase": "thinking"})
                    await session.send_realtime_input(activity_start=types.ActivityStart())
                    await session.send_realtime_input(
                        audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000"))
                    await session.send_realtime_input(activity_end=types.ActivityEnd())
                elif msg["type"] == "text":
                    await send({"type": "user_transcript", "text": msg["text"]})
                    state["history"].append({"role": "user", "content": msg["text"]})
                    await send({"type": "phase", "phase": "thinking"})
                    await session.send_client_content(
                        turns=types.Content(role="user", parts=[types.Part(text=msg["text"])]),
                        turn_complete=True)
                elif msg["type"] == "audition":
                    await send({"type": "error",
                                "message": "audition is a Sarvam-console feature; just talk"})
        except WebSocketDisconnect:
            pass
        finally:
            pump.cancel()


def main():
    global ARGS, KEY, EXECUTOR
    ap = argparse.ArgumentParser(description="Optimized Gemini Live PoC web console")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--model", default=os.environ.get("GEMINI_LIVE_MODEL",
                                                      "gemini-3.1-flash-live-preview"))
    ap.add_argument("--voice", default="Charon")
    ap.add_argument("--trigger-tokens", type=int, default=4096)
    ap.add_argument("--target-tokens", type=int, default=2048)
    ap.add_argument("--tools", default="real", choices=["real", "mock"])
    ap.add_argument("--user", default="admin_bhavesh")
    ap.add_argument("--geo", default=None)
    ap.add_argument("--tool-delay", type=float, default=2.5)
    ARGS = ap.parse_args()

    KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    if not KEY:
        sys.exit("GEMINI_API_KEY not set")

    if ARGS.tools == "real":
        geo = None
        if ARGS.geo:
            lat, lng = (float(x) for x in ARGS.geo.split(","))
            geo = {"lat": lat, "lng": lng}
        print(f"bootstrapping core pipeline (user={ARGS.user}, restoring MCP tokens)...")
        EXECUTOR = CoreToolExecutor(ARGS.user, geo)

    import uvicorn

    print(f"\n  🎙  http://localhost:{ARGS.port}  (OPTIMIZED gemini live: {ARGS.model}, "
          f"voice {ARGS.voice}, window {ARGS.trigger_tokens}/{ARGS.target_tokens})\n")
    uvicorn.run(app, host="127.0.0.1", port=ARGS.port, log_level="warning")


if __name__ == "__main__":
    main()
