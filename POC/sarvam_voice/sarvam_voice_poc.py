# /// script
# requires-python = ">=3.12"
# dependencies = ["requests", "sounddevice", "numpy"]
# ///
"""Standalone PoC for the Sarvam half-cascade voice pipeline (STT -> LLM -> TTS).

Answers four questions against our Gemini Live voice mode, without touching prod:
  1. Hinglish STT quality   - saaras:v3 with romanized (translit) output
  2. Bulbul TTS quality     - does it speak Roman-script Hinglish naturally?
  3. Cascade latency        - time-to-first-audio per turn (Gemini Live ~1-2s bar)
  4. Real cost              - per-turn + per-minute meter in rupees

Usage:
  export SARVAM_API_KEY=...           # dashboard.sarvam.ai (free credits on signup)
  uv run sarvam_voice_poc.py                       # mic conversation loop
  uv run sarvam_voice_poc.py --text "kya haal hai" # typed turn, still speaks
  uv run sarvam_voice_poc.py --wav clip.wav        # transcribe a file as turn 1
  uv run sarvam_voice_poc.py --tool-demo           # scripted actionable turn

Tool-calling is exercised with the same pattern prod uses: the LLM emits a
one-line intent, we play a canned Hinglish status cue instantly (zero tokens,
like the planned prod fix), a mock backend "runs" for --tool-delay seconds,
then the result is spoken.
"""

import argparse
import base64
import io
import json
import os
import queue
import re
import sys
import threading
import time
import wave
from pathlib import Path

import requests

SARVAM_BASE = "https://api.sarvam.ai"
STT_RATE = 16000
TTS_RATE = 24000

PRICES = {
    "stt_per_hour": 30.0,
    "tts_per_10k": {"bulbul:v2": 15.0, "bulbul:v3": 30.0},
    "translit_per_10k": 20.0,
    "llm_per_1m": {"in": 2.5, "out": 10.0},
    "llm_per_1m_deepseek": {"in": 23.0, "out": 93.0},
}

PERSONA = (
    "You are Companion, a warm, funny voice assistant for users in India. "
    "Reply ONLY in Hinglish — conversational Hindi written in the Roman/Latin alphabet "
    "(e.g. 'Theek hai, ho jayega'). NEVER use Devanagari script. English words are fine "
    "where natural. Keep replies SHORT — one to three sentences, like a dost talking. "
    "Your reply is spoken aloud by a TTS engine: no lists, no markdown, no emojis. "
    "You can actually DO things through the companion_execute function — order food, book "
    "cabs and tables, find restaurants, movies and events, fetch live data (weather, prices, "
    "news), manage reminders. Call it for ANY action or live-data request; never explain how "
    "to do it manually and never invent live data. When you call it, first say ONE short "
    "status line like 'Ek second, dekh raha hoon...'."
)

PERSONA_F = PERSONA.replace("dekh raha hoon", "dekh rahi hoon") + (
    " You are FEMALE — always use feminine Hindi verb forms for yourself "
    "(karti hoon, dekh rahi hoon, bolti hoon)."
)

STATUS_CUES_F = [
    "Ek second, dekh rahi hoon...",
    "Haan, abhi check karti hoon...",
    "Ruko zara, pata karti hoon...",
]

TOOLS_SPEC = [{
    "type": "function",
    "function": {
        "name": "companion_execute",
        "description": (
            "Execute actions for the user: order food, book cabs/tables, find "
            "restaurants/movies/events, live data (weather, gold rate, news), reminders. "
            "Call this for ANY action or live-data request."
        ),
        "parameters": {
            "type": "object",
            "properties": {"intent": {"type": "string",
                "description": "Full natural-language intent with all relevant details."}},
            "required": ["intent"],
        },
    },
}]

PERSONA_DEVA = PERSONA.replace(
    "written in the Roman/Latin alphabet (e.g. 'Theek hai, ho jayega'). NEVER use Devanagari script.",
    "written in Devanagari script (e.g. 'ठीक है, हो जाएगा').",
)

STATUS_CUES = [
    "Ek second, dekh raha hoon...",
    "Haan, abhi check karta hoon...",
    "Ruko zara, pata karta hoon...",
]

MOCK_TOOL_RESULT = (
    "3 achhe options mile: Domino's se Margherita ₹199 mein, 25 minute delivery. "
    "Pizza Hut ₹249, 30 minute. La Pino'z ₹179, 35 minute. Domino's pe 20% off bhi hai."
)


class CoreToolExecutor:
    """In-process bridge to the prod core pipeline — the same call
    voice_tool_execute_endpoint makes for Gemini voice, with session state
    (cart, restaurants, menus, ...) carried across turns."""

    SESSION_KEYS = (
        "session_state", "session_restaurants", "session_addresses", "session_cart",
        "session_menu", "session_movies", "session_theatres", "session_events",
        "session_venues", "session_artists", "session_reservation",
    )

    def __init__(self, user: str, geo: dict | None):
        repo = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(repo))
        os.environ.setdefault("AWS_PROFILE", "companion")
        os.environ.setdefault("AWS_REGION", "ap-south-1")
        os.environ.setdefault("APP_SECRET_ID", "companion/app")
        os.environ.pop("LEGACY_PIPELINE", None)
        from core.providers.mcp.token_store import restore_mcp_auth_tokens

        restore_mcp_auth_tokens(user_id=user)
        from companion_api.core_shadow import run_core_turn_sync

        self._run = run_core_turn_sync
        self.user = user
        self.geo = geo
        self.session: dict = {}
        self.chat_id: str | None = None

    def execute(self, intent: str, history: list[dict]) -> dict:
        """Run one core turn; returns the full envelope (narration, session_*, tool_execution, attachments)."""
        payload = {
            "message": intent,
            "history": history[-8:],
            "chat_id": self.chat_id,
            "execute_tools": True,
            "user_geo": self.geo,
            **self.session,
        }
        try:
            result = self._run(payload, self.user, reply_language="hinglish")
        except Exception as e:
            return {"assistant_message": f"Tool chalane mein dikkat aa gayi: {e}", "status": "error"}
        for k in self.SESSION_KEYS:
            if result.get(k):
                self.session[k] = result[k]
        self.chat_id = result.get("chat_id") or self.chat_id
        return result

    def __call__(self, intent: str, history: list[dict]) -> str:
        result = self.execute(intent, history)
        return result.get("assistant_message") or result.get("error") or "Kuch result nahi mila."


class CostMeter:
    """Accumulates session spend in rupees across STT seconds, TTS chars, LLM tokens."""

    def __init__(self, tts_model: str, llm_rates: dict | None = None):
        self.tts_model = tts_model
        self.llm_rates = llm_rates or PRICES["llm_per_1m"]
        self.stt_secs = 0.0
        self.tts_chars = 0
        self.tts_cost = 0.0
        self.translit_chars = 0
        self.llm_in = 0
        self.llm_out = 0
        self.started = time.monotonic()

    def total(self) -> float:
        # tts_cost accrues per call at that call's model rate (voices are switchable live)
        return (
            self.stt_secs * PRICES["stt_per_hour"] / 3600
            + self.tts_cost
            + self.translit_chars * PRICES["translit_per_10k"] / 10_000
            + self.llm_in * self.llm_rates["in"] / 1e6
            + self.llm_out * self.llm_rates["out"] / 1e6
        )

    def summary(self) -> str:
        mins = max((time.monotonic() - self.started) / 60, 1 / 60)
        return (
            f"session ₹{self.total():.3f} (~₹{self.total() / mins:.2f}/min) | "
            f"stt {self.stt_secs:.0f}s, tts {self.tts_chars} chars, "
            f"translit {self.translit_chars} chars, "
            f"llm ~{self.llm_in}+{self.llm_out} tok (est)"
        )


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def windowed(messages: list[dict], n: int = 8) -> list[dict]:
    """System prompt + last n turns — caps LLM input growth across a session."""
    head = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    return head + rest[-n:]


def spoken_form(narration: str, cap: int = 260) -> str:
    """Trim a long tool narration to a speakable brief — the cards carry the detail.

    Speech is the expensive surface (translit ₹20 + TTS ₹15-30 per 10k chars);
    cutting at sentence boundaries keeps it natural."""
    if len(narration) <= cap:
        return narration
    out = ""
    for part in re.split(r"(?<=[.!?।])\s+", narration):
        if out and len(out) + len(part) > cap:
            break
        out = f"{out} {part}".strip()
    return (out or narration[:cap]) + " Baaki details cards mein dikh rahe hain."


def sarvam_stt(key: str, wav_bytes: bytes, mode: str, meter: CostMeter) -> tuple[str, float]:
    """Transcribe one utterance; returns (transcript, api_ms)."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        meter.stt_secs += w.getnframes() / w.getframerate()
    t0 = time.monotonic()
    resp = requests.post(
        f"{SARVAM_BASE}/speech-to-text",
        headers={"api-subscription-key": key},
        files={"file": ("utterance.wav", io.BytesIO(wav_bytes), "audio/wav")},
        data={"model": "saaras:v3", "mode": mode},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("transcript", "").strip(), (time.monotonic() - t0) * 1000


def sarvam_transliterate(key: str, text: str, meter: CostMeter) -> str:
    """Roman Hinglish -> Devanagari (English words phonetic) for correct TTS pronunciation."""
    meter.translit_chars += len(text)
    resp = requests.post(
        f"{SARVAM_BASE}/transliterate",
        headers={"api-subscription-key": key, "Content-Type": "application/json"},
        json={"input": text, "source_language_code": "en-IN", "target_language_code": "hi-IN"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("transliterated_text") or text


_TTS_CACHE: dict[tuple, bytes] = {}


def sarvam_tts(key: str, text: str, model: str, speaker: str, meter: CostMeter,
               to_deva: bool = True, pace: float = 1.0,
               temperature: float | None = None) -> bytes:
    """Synthesize one chunk; returns raw WAV bytes.

    Bulbul mispronounces romanized Hindi (live PoC finding), so Roman text is
    transliterated to Devanagari first; captions/history keep the Roman form.
    `temperature` is bulbul:v3's expressiveness knob (ignored for v2).
    Repeated lines (status cues, audition) are served from an in-memory cache
    at zero cost."""
    cache_key = (text, model, speaker, round(pace, 2), temperature, to_deva)
    cached = _TTS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if to_deva and re.search(r"[A-Za-z]", text):
        try:
            text = sarvam_transliterate(key, text, meter)
        except requests.RequestException:
            pass
    meter.tts_chars += len(text)
    meter.tts_cost += len(text) * PRICES["tts_per_10k"][model] / 10_000
    body = {
        "text": text,
        "model": model,
        "speaker": speaker,
        "target_language_code": "hi-IN",
        "speech_sample_rate": TTS_RATE,
        "output_audio_codec": "wav",
        "pace": pace,
    }
    if temperature is not None and model == "bulbul:v3":
        body["temperature"] = temperature
    resp = requests.post(
        f"{SARVAM_BASE}/text-to-speech",
        headers={"api-subscription-key": key, "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    wav = base64.b64decode(resp.json()["audios"][0])
    if len(_TTS_CACHE) > 128:
        _TTS_CACHE.pop(next(iter(_TTS_CACHE)))
    _TTS_CACHE[cache_key] = wav
    return wav


def llm_stream(cfg: dict, messages: list[dict], meter: CostMeter,
               tools: list | None = None, tool_out: dict | None = None):
    """Yield content deltas from an OpenAI-compatible streaming chat endpoint.

    With `tools`, a model-initiated function call is accumulated from the
    stream and surfaced as tool_out['intent'] once iteration completes.
    Reasoning deltas from thinking models (sarvam-30b) are skipped."""
    meter.llm_in += sum(est_tokens(m["content"]) for m in messages)
    body = {
        "model": cfg["model"],
        "messages": messages,
        "stream": True,
        "temperature": 0.6,
        # sarvam-30b spends reasoning tokens before content; budget both.
        "max_tokens": 1200,
    }
    if tools:
        body["tools"] = tools
    resp = requests.post(
        f"{cfg['base']}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['key']}"},
        json=body,
        stream=True,
        timeout=60,
    )
    if resp.status_code >= 400:
        body = resp.text[:500]
        models = requests.get(
            f"{cfg['base']}/models", headers={"Authorization": f"Bearer {cfg['key']}"}, timeout=15
        )
        available = (
            [m.get("id") for m in models.json().get("data", [])] if models.ok else "<unavailable>"
        )
        raise RuntimeError(
            f"chat {resp.status_code}: {body}\nAvailable models on this endpoint: {available}\n"
            f"Retry with --llm-model <id>."
        )
    args_buf = ""
    for line in resp.iter_lines():
        if not line or not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload == b"[DONE]":
            break
        try:
            delta = json.loads(payload)["choices"][0]["delta"]
        except (KeyError, IndexError, json.JSONDecodeError):
            continue
        for tc in delta.get("tool_calls") or []:
            args_buf += (tc.get("function") or {}).get("arguments") or ""
        text = delta.get("content") or ""
        if text:
            meter.llm_out += est_tokens(text)
            yield text
    if args_buf and tool_out is not None:
        try:
            tool_out["intent"] = json.loads(args_buf).get("intent") or args_buf
        except json.JSONDecodeError:
            tool_out["intent"] = args_buf


def sentences(deltas, min_chars: int = 0) -> "tuple":
    """Re-chunk a delta stream at sentence boundaries; returns (generator, full_text_holder).

    min_chars batches consecutive sentences into one chunk — per-sentence TTS
    resets Bulbul's prosody on every call (the 'robotic' live feel vs the
    one-shot audition), so bigger chunks speak naturally at a small
    time-to-first-audio cost."""
    holder = {"text": ""}

    def gen():
        buf, pending = "", ""
        for d in deltas:
            holder["text"] += d
            buf += d
            while True:
                m = re.search(r"(?<=[.!?।])\s+", buf)
                if m and m.start() >= 18:
                    pending = f"{pending} {buf[: m.start()].strip()}".strip()
                    buf = buf[m.end():]
                    if len(pending) >= min_chars:
                        yield pending
                        pending = ""
                elif len(buf) > 350:
                    cut = buf.rfind(",", 100)
                    cut = cut if cut != -1 else 300
                    yield f"{pending} {buf[:cut].strip()}".strip()
                    pending = ""
                    buf = buf[cut + 1:]
                else:
                    break
        tail = f"{pending} {buf.strip()}".strip()
        if tail:
            yield tail

    return gen(), holder


def play_wav(wav_bytes: bytes):
    import numpy as np
    import sounddevice as sd

    with wave.open(io.BytesIO(wav_bytes)) as w:
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        sd.play(frames, w.getframerate())
        sd.wait()


def speak(args, key: str, meter: CostMeter, sentence_gen, t_turn0: float | None) -> float | None:
    """Pipeline TTS + playback over a sentence stream; returns time-to-first-audio ms."""
    audio_q: queue.Queue = queue.Queue(maxsize=4)
    first_audio = {}

    def producer():
        try:
            for sent in sentence_gen:
                audio_q.put(sarvam_tts(key, sent, args.tts_model, args.speaker, meter,
                                       to_deva=args.tts_translit))
        except Exception as e:
            print(f"  [tts error] {e}", file=sys.stderr)
        finally:
            audio_q.put(None)

    threading.Thread(target=producer, daemon=True).start()
    while (chunk := audio_q.get()) is not None:
        if "t" not in first_audio:
            first_audio["t"] = time.monotonic()
        if not args.no_audio:
            play_wav(chunk)
    if t_turn0 is not None and "t" in first_audio:
        return (first_audio["t"] - t_turn0) * 1000
    return None


def record_utterance(max_secs: float = 25.0) -> bytes | None:
    """Capture one VAD-segmented utterance from the mic as 16kHz mono WAV bytes."""
    import numpy as np
    import sounddevice as sd

    frame = int(STT_RATE * 0.03)
    pre_roll: list = []
    voiced: list = []
    state = {"ambient": None, "calib": [], "run": 0, "quiet": 0, "talking": False, "done": False}

    def rms(block) -> float:
        return float(np.sqrt(np.mean(np.square(block))))

    print("  🎙️  listening... (speak, pause to send; Ctrl+C to quit)")
    with sd.InputStream(samplerate=STT_RATE, channels=1, dtype="float32", blocksize=frame) as stream:
        start = time.monotonic()
        while not state["done"]:
            block, _ = stream.read(frame)
            level = rms(block)
            if state["ambient"] is None:
                state["calib"].append(level)
                if len(state["calib"]) >= 20:
                    state["ambient"] = max(float(np.median(state["calib"])), 1e-4)
                continue
            threshold = max(state["ambient"] * 4, 0.01)
            if not state["talking"]:
                pre_roll.append(block.copy())
                if len(pre_roll) > 10:
                    pre_roll.pop(0)
                state["run"] = state["run"] + 1 if level > threshold else 0
                if state["run"] >= 5:
                    state["talking"] = True
                    voiced.extend(pre_roll)
            else:
                voiced.append(block.copy())
                state["quiet"] = state["quiet"] + 1 if level < threshold else 0
                if state["quiet"] >= 30 or time.monotonic() - start > max_secs:
                    state["done"] = True

    if not voiced:
        return None
    pcm = (np.concatenate(voiced).flatten() * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(STT_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def run_turn(args, key: str, llm_cfg: dict, meter: CostMeter, messages: list[dict],
             user_text: str, t0: float, executor: "CoreToolExecutor | None" = None):
    """One conversational turn: LLM (native tool call) -> core pipeline | mock -> spoken reply."""
    messages.append({"role": "user", "content": user_text})
    tool_out: dict = {}
    deltas = llm_stream(llm_cfg, windowed(messages), meter, tools=TOOLS_SPEC, tool_out=tool_out)
    gen, holder = sentences(deltas, min_chars=args.tts_chunk)
    ttfa = speak(args, key, meter, gen, t0)
    spoke = holder["text"].strip()
    if spoke:
        print(f"  🤖 {spoke}")

    intent = tool_out.get("intent")
    if not intent:
        messages.append({"role": "assistant", "content": spoke})
        print(f"  ⏱  time-to-first-audio {ttfa or 0:.0f}ms")
        return

    print(f"  ⚙️  tool call → {intent!r}")
    if not spoke:
        cue = STATUS_CUES[len(messages) % len(STATUS_CUES)]
        speak(args, key, meter, iter([cue]), None)
    t_tool0 = time.monotonic()
    if executor is not None:
        hist = [m for m in messages if m["role"] in ("user", "assistant")]
        narration = executor(intent, hist)
    else:
        time.sleep(args.tool_delay)
        narration = MOCK_TOOL_RESULT
    tool_secs = time.monotonic() - t_tool0
    speech = spoken_form(narration) if args.narration_style == "brief" else narration
    t_r0 = time.monotonic()
    ttfa2 = speak(args, key, meter, sentences(iter([speech]), args.tts_chunk)[0], t_r0)
    messages.append({"role": "assistant",
                     "content": f"{spoke} [action: {intent}] {narration[:240]}".strip()})
    print(f"  🤖 {speech}")
    print(f"  ⏱  ttfa {ttfa or 0:.0f}ms | tool {tool_secs:.1f}s | result ttfa {ttfa2 or 0:.0f}ms")


def main():
    ap = argparse.ArgumentParser(description="Sarvam half-cascade voice PoC")
    ap.add_argument("--text", help="run one typed turn instead of mic")
    ap.add_argument("--wav", help="transcribe this WAV file as the first turn")
    ap.add_argument("--tool-demo", action="store_true", help="scripted actionable turn (pizza order)")
    ap.add_argument("--no-audio", action="store_true", help="skip playback (still synthesizes + meters)")
    ap.add_argument("--stt-mode", default="translit", choices=["translit", "codemix", "transcribe", "verbatim"])
    ap.add_argument("--tts-model", default="bulbul:v2", choices=["bulbul:v2", "bulbul:v3"])
    ap.add_argument("--speaker", default=None, help="bulbul voice (v2: abhilash/anushka/..., v3: shubh/...)")
    ap.add_argument("--tts-script", default="roman", choices=["roman", "deva"],
                    help="LLM output script — A/B which Bulbul speaks better")
    ap.add_argument("--tts-translit", action=argparse.BooleanOptionalAction, default=True,
                    help="transliterate Roman Hinglish to Devanagari before TTS (₹20/10k chars)")
    ap.add_argument("--llm", default="deepseek", choices=["deepseek", "sarvam"])
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--tools", default="real", choices=["real", "mock"],
                    help="real = in-process core pipeline (Zomato/serp/... like Gemini voice); mock = canned result")
    ap.add_argument("--user", default="admin_bhavesh", help="core pipeline user with vaulted MCP tokens")
    ap.add_argument("--geo", default=None, help="lat,lng for location-aware tools (like browser GPS)")
    ap.add_argument("--tool-delay", type=float, default=2.5, help="mock backend seconds (mock mode only)")
    ap.add_argument("--tts-chunk", type=int, default=220,
                    help="min chars per TTS chunk — bigger = natural prosody, smaller = faster first audio")
    ap.add_argument("--narration-style", default="brief", choices=["brief", "full"],
                    help="brief = speak a short summary, cards carry detail (cheaper)")
    args = ap.parse_args()
    args.speaker = args.speaker or ("abhilash" if args.tts_model == "bulbul:v2" else "priya")

    key = os.environ.get("SARVAM_API_KEY", "")
    if not key:
        sys.exit("SARVAM_API_KEY not set — get one at https://dashboard.sarvam.ai (free credits on signup)")
    if args.llm == "deepseek":
        ds = os.environ.get("DEEPSEEK_API_KEY", "")
        if not ds:
            sys.exit("DEEPSEEK_API_KEY not set")
        llm_cfg = {"base": "https://api.deepseek.com/v1", "key": ds, "model": args.llm_model or "deepseek-chat"}
    else:
        llm_cfg = {"base": f"{SARVAM_BASE}/v1", "key": key, "model": args.llm_model or "sarvam-30b"}

    executor = None
    if args.tools == "real":
        geo = None
        if args.geo:
            lat, lng = (float(x) for x in args.geo.split(","))
            geo = {"lat": lat, "lng": lng}
        print(f"bootstrapping core pipeline (user={args.user}, restoring MCP tokens)...")
        executor = CoreToolExecutor(args.user, geo)

    llm_rates = PRICES["llm_per_1m_deepseek"] if args.llm == "deepseek" else PRICES["llm_per_1m"]
    meter = CostMeter(args.tts_model, llm_rates)
    persona = PERSONA_DEVA if args.tts_script == "deva" else PERSONA
    messages = [{"role": "system", "content": persona}]
    print(f"sarvam-voice-poc | llm={llm_cfg['model']} tts={args.tts_model}/{args.speaker} "
          f"stt=saaras:v3/{args.stt_mode} tools={args.tools}\n")

    try:
        if args.tool_demo:
            run_turn(args, key, llm_cfg, meter, messages,
                     "yaar mujhe ek margherita pizza order karna hai, sasta wala dekho",
                     time.monotonic(), executor)
        elif args.text:
            run_turn(args, key, llm_cfg, meter, messages, args.text, time.monotonic(), executor)
        else:
            if args.wav:
                wav_bytes = open(args.wav, "rb").read()
                transcript, stt_ms = sarvam_stt(key, wav_bytes, args.stt_mode, meter)
                print(f"  🧑 {transcript}   (stt {stt_ms:.0f}ms)")
                run_turn(args, key, llm_cfg, meter, messages, transcript, time.monotonic(), executor)
            while True:
                wav_bytes = record_utterance()
                if not wav_bytes:
                    continue
                t0 = time.monotonic()
                transcript, stt_ms = sarvam_stt(key, wav_bytes, args.stt_mode, meter)
                if not transcript:
                    print("  (empty transcript, try again)")
                    continue
                print(f"  🧑 {transcript}   (stt {stt_ms:.0f}ms)")
                run_turn(args, key, llm_cfg, meter, messages, transcript, t0, executor)
                print(f"  💰 {meter.summary()}\n")
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n💰 {meter.summary()}")


if __name__ == "__main__":
    main()
