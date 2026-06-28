"""Sarvam voice clients — STT + TTS, both REST (no streaming, no WebSocket).

The engine behind the in-app voice loop that REPLACED Gemini Live:
    mic ─▶ client-side VAD ─▶ utterance WAV ─▶ Sarvam STT ─▶ text ─▶ existing run_turn
        ─▶ reply ─▶ Sarvam TTS ─▶ playback ─▶ listen again

Both calls are plain REST so the whole feature runs on the existing Lambda (no EC2, no
persistent socket). The `SARVAM_API_KEY` stays server-side — the browser only ever talks to
our own `/api/voice/stt` and `/api/voice/tts`.
"""

from __future__ import annotations

import base64
import io

import httpx

from companion_api.secrets import secret_or_env

SARVAM_BASE = "https://api.sarvam.ai"

STT_MODEL = "saaras:v3"          # latest ASR; saarika is being deprecated
STT_LANGUAGE = "hi-IN"           # Hinglish: the Hindi model handles mixed Hindi/English

TTS_MODEL = "bulbul:v3"
TTS_SPEAKER = "priya"            # warm female Hinglish voice (valid bulbul:v3 speaker)
TTS_RATE = 24000
TTS_LANGUAGE = "hi-IN"


def sarvam_key() -> str:
    """The shared Sarvam subscription key (same one the LLM uses)."""
    key = secret_or_env("SARVAM_API_KEY")
    if not key:
        raise RuntimeError("SARVAM_API_KEY is not configured")
    return key


async def stt_rest(audio: bytes, *, filename: str = "utterance.wav") -> str:
    """Transcribe one utterance (a WAV blob) → text. The Hindi model returns Devanagari;
    the caller romanizes it for the rest of the (Roman-Hinglish) product."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{SARVAM_BASE}/speech-to-text",
            headers={"api-subscription-key": sarvam_key()},
            files={"file": (filename, io.BytesIO(audio), "audio/wav")},
            data={"model": STT_MODEL, "language_code": STT_LANGUAGE, "mode": "transcribe"},
        )
        resp.raise_for_status()
        return (resp.json().get("transcript") or "").strip()


async def tts_rest(
    text: str,
    *,
    model: str = TTS_MODEL,
    speaker: str = TTS_SPEAKER,
    language: str = TTS_LANGUAGE,
    pace: float = 1.0,
) -> bytes:
    """Synthesize one reply to a single WAV blob. bulbul:v3 handles mixed/roman Hindi input
    well, so the Roman-Hinglish reply is sent as-is with the Hindi target language."""
    body = {
        "text": text[:2500],  # bulbul:v3 hard cap
        "model": model,
        "speaker": speaker,
        "target_language_code": language,
        "speech_sample_rate": TTS_RATE,
        "output_audio_codec": "wav",
        "pace": pace,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{SARVAM_BASE}/text-to-speech",
            headers={"api-subscription-key": sarvam_key(), "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        audios = resp.json().get("audios") or []
    if not audios:
        raise RuntimeError("Sarvam TTS returned no audio")
    return base64.b64decode(audios[0])
