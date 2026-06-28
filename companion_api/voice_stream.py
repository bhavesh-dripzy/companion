"""In-app voice loop — Sarvam STT + TTS, both plain REST (no WebSocket, no EC2).

The browser does the listening (mic + client-side VAD); when the user stops talking it POSTs
the utterance here. We transcribe it with Sarvam, romanize to Hinglish, and hand back the text.
The browser then runs that text through the SAME `/api/agent/turn/stream` the text box uses, and
calls `POST /api/voice/tts` to speak the reply. So voice is just a front-end on the existing text
agent: same cards, same persistence, same everything — and it runs entirely on Lambda.

The `SARVAM_API_KEY` never leaves the server.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from companion_api.auth import get_current_user
from companion_api.sarvam_voice import stt_rest, tts_rest
from companion_api.transliterate import romanize_hinglish

router = APIRouter()


@router.post("/api/voice/stt")
async def voice_stt(file: UploadFile, user: str = Depends(get_current_user)) -> dict[str, str]:
    """Transcribe one spoken utterance (a WAV upload) → romanized Hinglish text."""
    audio = await file.read()
    if not audio:
        return {"text": ""}
    raw = await stt_rest(audio, filename=file.filename or "utterance.wav")
    return {"text": await romanize_hinglish(raw) if raw else ""}


class TtsRequest(BaseModel):
    text: str
    speaker: str | None = None
    pace: float = 1.0


@router.post("/api/voice/tts")
async def voice_tts(payload: TtsRequest, user: str = Depends(get_current_user)) -> Response:
    """Speak one assistant reply. Returns a WAV blob the browser plays directly."""
    text = (payload.text or "").strip()
    if not text:
        return Response(status_code=204)
    kwargs: dict = {"pace": payload.pace}
    if payload.speaker:
        kwargs["speaker"] = payload.speaker
    wav = await tts_rest(text, **kwargs)
    return Response(content=wav, media_type="audio/wav")
