"""Generate the pre-recorded tool-status voice cues for the web voice mode.

useGeminiLive.ts plays /voice-cues/cue-N.mp3 the moment the model calls
companion_execute — replacing the old status-line nudge, which forced an extra
full-context Live generation on every tool call. Rendered with Gemini TTS in
the SAME voice as the live session (GEMINI_LIVE_VOICE, default Charon) so the
cue is indistinguishable from the model speaking.

Run once (and again if the voice changes):
  GEMINI_API_KEY=... .venv/bin/python scripts/generate_voice_cues.py
"""

import os
import struct
import sys
import wave
from pathlib import Path

CUES = [
    "Ek second, dekh raha hoon...",
    "Haan, abhi check karta hoon...",
    "Ruko zara, pata karta hoon...",
]
OUT_DIR = Path(__file__).resolve().parents[1] / "frontend" / "public" / "voice-cues"
TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")


def main() -> None:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY not set")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    voice = os.environ.get("GEMINI_LIVE_VOICE", "Charon")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for i, cue in enumerate(CUES):
        resp = client.models.generate_content(
            model=TTS_MODEL,
            contents=f"Say warmly and naturally in Hinglish: {cue}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                    )
                ),
            ),
        )
        pcm = resp.candidates[0].content.parts[0].inline_data.data
        # Gemini TTS returns 24kHz mono s16le PCM; browsers can't decode raw
        # PCM, so wrap it. (.mp3 extension kept for the frontend contract —
        # WAV decodes fine regardless of extension via the Audio element.)
        path = OUT_DIR / f"cue-{i}.mp3"
        buf = wave.open(str(path), "wb")
        buf.setnchannels(1)
        buf.setsampwidth(2)
        buf.setframerate(24000)
        buf.writeframes(pcm)
        buf.close()
        print(f"wrote {path} ({len(pcm) // 1024}KB pcm) — {cue!r}")

    print(f"\nDone. {len(CUES)} cues in {OUT_DIR} (voice={voice}, model={TTS_MODEL})")


if __name__ == "__main__":
    main()
