"""Gemini Live ↔ Twilio bidirectional voice bridge.

Deployed on Companion EC2 behind Caddy (wss://voice.jatayulabs.com/media-stream).
Receives Twilio Media Streams audio, transcodes mulaw↔PCM, and streams to/from
Gemini Live API for natural bidirectional conversation.

Adapted from: gemini-live-api-twilio-phone (Google Cloud Run reference)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import audioop
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, WebSocket, Request, Query
from fastapi.responses import Response, PlainTextResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("twilio_voice")

MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_VOICE = "Puck"
DEFAULT_SYSTEM_PROMPT = (
    "You are Companion, a warm and helpful AI voice assistant for users in India. "
    "Speak naturally in Hinglish — conversational Hindi in Roman script. "
    "Be warm, friendly, and concise. Keep responses brief."
)


# ---------------------------------------------------------------------------
# Secrets loading (must be defined before _load_secrets() is called below)
# ---------------------------------------------------------------------------

def _load_secrets() -> None:
    """Load Gemini API key from AWS Secrets Manager."""
    try:
        import boto3

        project = os.getenv("PROJECT_NAME", "companion")
        region = os.getenv("AWS_REGION", "ap-south-1")
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=f"{project}/app")
        secrets = json.loads(resp["SecretString"])
        for k, v in secrets.items():
            if k not in os.environ:
                os.environ[k] = str(v)
        logger.info("Secrets loaded from AWS Secrets Manager")
    except Exception as exc:
        logger.warning("Could not load secrets: %s", exc)


def _gemini_api_key() -> str:
    """Resolve Gemini API key from env."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        _load_secrets()
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    return key


# Pre-load secrets at startup so GEMINI_API_KEY is available for WebSocket sessions.
# The systemd unit does NOT set this env var; it only loads /etc/companion/env which
# lacks it. _load_secrets() reads {project_name}/app from Secrets Manager — the same
# secret the Lambda and leadgen-worker load explicitly.
_load_secrets()

app = FastAPI(title="Companion Voice Bridge")


# ---------------------------------------------------------------------------
# Audio transcoding: Twilio mulaw 8kHz ↔ Gemini PCM 24kHz
# ---------------------------------------------------------------------------


def _mulaw_to_pcm_16k(chunk: bytes, state: Any) -> tuple[bytes, Any]:
    """Twilio G.711 mulaw 8kHz → PCM 16kHz for Gemini."""
    pcm_8k = audioop.ulaw2lin(chunk, 2)
    pcm_16k, new_state = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, state)
    return pcm_16k, new_state


def _pcm_24k_to_mulaw(audio_data: bytes, state: Any) -> tuple[str, Any]:
    """Gemini PCM 24kHz → G.711 mulaw 8kHz for Twilio."""
    pcm_8k, new_state = audioop.ratecv(audio_data, 2, 1, 24000, 8000, state)
    mulaw = audioop.lin2ulaw(pcm_8k, 2)
    b64 = base64.b64encode(mulaw).decode("utf-8")
    return b64, new_state


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> PlainTextResponse:
    return PlainTextResponse("voice-bridge ok")


@app.api_route("/outbound-call", methods=["GET", "POST"])
async def outbound_call(
    request: Request,
    message: str = Query(default=""),
    system_prompt: str = Query(default=""),
) -> Response:
    """Return TwiML that connects the call to Gemini Live via WebSocket.

    Called by the Companion Lambda when initiating an outbound call.
    The ``message`` and ``system_prompt`` query params customize Gemini's
    behavior for this specific call.
    """
    host = request.headers.get("host", "voice.houzzat.in")
    params = []
    if message:
        params.append(f"message={_url_quote(message)}")
    if system_prompt:
        params.append(f"sp={_url_quote(system_prompt)}")
    qs = "&".join(params)
    ws_url = f"wss://{host}/media-stream"
    if qs:
        ws_url = f"{ws_url}?{qs}"

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f'<Stream url="{ws_url}" />'
        "</Connect>"
        "</Response>"
    )
    return Response(content=twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# WebSocket: Gemini Live ↔ Twilio bidirectional audio
# ---------------------------------------------------------------------------


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket) -> None:
    """Handle bidirectional audio streaming between Twilio and Gemini Live."""
    await websocket.accept()
    logger.info("WebSocket connected")

    query_params = parse_qs(str(websocket.url.query or ""))
    message_hint = _first_query(query_params, "message")
    system_prompt = _first_query(query_params, "sp") or DEFAULT_SYSTEM_PROMPT

    if message_hint:
        system_prompt = (
            f"{system_prompt}\n\n"
            f"The user wants you to say this to the person on the phone: "
            f'"{message_hint}". Say it naturally when the call connects, '
            f"then continue the conversation naturally if they respond."
        )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=_gemini_api_key(),
            http_options={"api_version": "v1alpha"},
        )

        # Sliding-window compression caps the context the Live API re-bills on
        # every generation — phone calls stream continuous audio, so without it
        # per-turn input cost grows for the whole call (and audio sessions are
        # hard-capped at 15 min).
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(
                parts=[types.Part(text=system_prompt)]
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=DEFAULT_VOICE,
                    )
                )
            ),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=int(os.environ.get("GEMINI_LIVE_TRIGGER_TOKENS", "4096")),
                sliding_window=types.SlidingWindow(
                    target_tokens=int(os.environ.get("GEMINI_LIVE_TARGET_TOKENS", "2048"))
                ),
            ),
        )

        logger.info("Connecting to Gemini Live (model=%s)", MODEL)
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            logger.info("Gemini Live connected")

            stream_info: dict[str, str | None] = {"sid": None}
            receive_task = asyncio.create_task(
                _gemini_receiver(session, websocket, stream_info)
            )

            # If there's a message to deliver, send it as the first turn
            if message_hint:
                await session.send(
                    input=(
                        f"Please say this to the person: '{message_hint}'. "
                        "Say it naturally in Hinglish, then wait for their response."
                    ),
                    end_of_turn=True,
                )
            else:
                # Start conversation naturally
                await session.send(
                    input="Start the conversation naturally. Greet the person in "
                    "warm Hinglish and ask how you can help them.",
                    end_of_turn=True,
                )

            upsample_state = None
            audio_buffer = bytearray()

            async for raw_message in websocket.iter_text():
                data = json.loads(raw_message)

                if data.get("event") == "start":
                    stream_info["sid"] = data["start"]["streamSid"]
                    logger.info("Stream started: %s", stream_info["sid"])

                elif data.get("event") == "media":
                    if stream_info["sid"]:
                        payload = data["media"]["payload"]
                        chunk = base64.b64decode(payload)
                        pcm_chunk, upsample_state = _mulaw_to_pcm_16k(
                            chunk, upsample_state
                        )
                        audio_buffer.extend(pcm_chunk)

                        # Send to Gemini every ~300ms (9600 bytes at 16kHz)
                        if len(audio_buffer) >= 9600:
                            await session.send(
                                input={
                                    "data": bytes(audio_buffer),
                                    "mime_type": "audio/pcm;rate=16000",
                                },
                                end_of_turn=False,
                            )
                            audio_buffer.clear()

                elif data.get("event") == "stop":
                    logger.info("Stream stopped by Twilio")
                    break

    except Exception as exc:
        logger.exception("WebSocket error: %s", exc)
    finally:
        if "receive_task" in dir():
            receive_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("WebSocket closed")


# ---------------------------------------------------------------------------
# Background task: Gemini → Twilio audio relay
# ---------------------------------------------------------------------------


async def _gemini_receiver(
    session: Any,
    websocket: WebSocket,
    stream_info: dict[str, str | None],
) -> None:
    """Continuously receive audio from Gemini and relay to Twilio."""
    downsample_state = None
    try:
        while True:
            async for response in session.receive():
                sc = response.server_content
                if sc is None:
                    continue
                mt = sc.model_turn
                if mt is None:
                    continue
                for part in mt.parts or []:
                    if part.inline_data is not None and part.inline_data.data:
                        b64_audio, downsample_state = _pcm_24k_to_mulaw(
                            part.inline_data.data, downsample_state
                        )
                        sid = stream_info.get("sid")
                        if sid and b64_audio:
                            await websocket.send_json(
                                {
                                    "event": "media",
                                    "streamSid": sid,
                                    "media": {"payload": b64_audio},
                                }
                            )
                if sc.turn_complete:
                    logger.debug("Gemini turn complete")
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("Gemini receiver error: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _first_query(params: dict[str, list[str]], key: str) -> str:
    vals = params.get(key, [])
    return vals[0] if vals else ""


def _url_quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")
