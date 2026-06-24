from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from mangum import Mangum
from starlette.responses import Response as StarletteResponse

import companion_api.settings  # loads .env before auth reads LEGACY_ADMIN_*

from companion_api.auth import (
    get_current_user,
)
from companion_api.dynamodb import MemberRepository
from companion_api.models import (
    AgentQueryRequest,
    FoodQueryRequest,
    ReminderEvent,
)
from companion_api.reminders import ReminderScheduler
from companion_api.voice_routes import (
    voice_gemini_token_endpoint,
    voice_log_turn_endpoint,
    voice_romanize_endpoint,
    voice_token_endpoint,
    voice_tool_execute_endpoint,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Companion API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# Cache-control headers for the SPA shell (`index.html`). MUST be
# no-cache because this file is the *only* place that references the
# current build's hashed asset filenames (e.g. /assets/index-Cv8krTmH.js).
# Without this, browsers (and any intermediate proxy) heuristically
# cache `index.html` for hours, keep loading the *old* hashed asset
# names, and the user never sees UI changes — even after we redeploy.
# `must-revalidate` ensures even stale caches phone home before serving.
_SPA_SHELL_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


class ImmutableStaticFiles(StaticFiles):
    """``StaticFiles`` that tags every successful response with
    ``Cache-Control: public, max-age=31536000, immutable``. Suitable
    *only* for content-hashed asset directories (e.g. Vite's
    ``dist/assets/``) where the filename itself changes whenever the
    bytes change — caching forever is safe and a year-long edge cache
    is the modern best practice for SPA bundles.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if isinstance(response, StarletteResponse) and response.status_code < 400:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


@app.get("/", response_class=HTMLResponse)
@app.get("/food/", response_class=HTMLResponse)
def food_ui():
    """Serve the new React frontend SPA shell.

    Always returned with no-cache headers — see ``_SPA_SHELL_HEADERS``
    above for the rationale. The cost is a single revalidation request
    per page load (the assets it pulls in are immutable and cached
    forever).
    """
    index_path = _FRONTEND_DIST / "index.html"
    if not index_path.exists():
        return HTMLResponse(
            "Frontend not found. Run 'npm run build' in the frontend directory.",
            status_code=404,
            headers=_SPA_SHELL_HEADERS,
        )
    return HTMLResponse(index_path.read_text(), headers=_SPA_SHELL_HEADERS)


@app.get("/favicon.svg")
def favicon():
    fav_path = _FRONTEND_DIST / "favicon.svg"
    if fav_path.exists():
        return HTMLResponse(fav_path.read_text(), media_type="image/svg+xml")
    return HTMLResponse("", status_code=404)


@app.get("/companion.apk")
def companion_apk():
    """Serve the Android app for sideloading from a stable, shareable URL.

    CI rebuilds the APK on every deploy and uploads it to the app-downloads
    bucket; we 302 to a short-lived presigned URL. Falls back to a bundled copy
    for local/dev runs without the bucket.
    """
    bucket = os.getenv("APP_DOWNLOADS_BUCKET", "").strip()
    if bucket:
        try:
            # Presign against the bucket's regional endpoint — the global
            # s3.amazonaws.com host with a regionally-scoped signature gets a
            # SignatureDoesNotMatch from S3.
            region = os.getenv("AWS_REGION") or "ap-south-1"
            s3 = boto3.client(
                "s3",
                region_name=region,
                endpoint_url=f"https://s3.{region}.amazonaws.com",
                config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
            )
            url = s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket,
                    "Key": "companion.apk",
                    "ResponseContentType": "application/vnd.android.package-archive",
                    "ResponseContentDisposition": "attachment; filename=companion.apk",
                },
                ExpiresIn=3600,
            )
            return RedirectResponse(url, status_code=302)
        except Exception:
            logger.warning("Could not presign companion.apk from %s", bucket, exc_info=True)
    apk_path = _FRONTEND_DIST / "companion.apk"
    if apk_path.is_file():
        return FileResponse(
            apk_path,
            media_type="application/vnd.android.package-archive",
            filename="companion.apk",
        )
    raise HTTPException(status_code=404, detail="APK is being built; try again shortly.")


# Mount static assets (JS, CSS, images) from the React build with
# year-long immutable caching — the filenames are content-hashed by
# Vite so a new build produces new filenames and old ones can be
# safely cached forever by browsers + CDNs.
if (_FRONTEND_DIST / "assets").exists():
    app.mount(
        "/assets",
        ImmutableStaticFiles(directory=str(_FRONTEND_DIST / "assets")),
        name="assets",
    )
# Mount public folder files (favicon, etc.). These are NOT hashed, so
# we keep them on the default (no explicit cache headers — browser
# heuristics, which is fine for static brand assets).
if _FRONTEND_DIST.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIST)), name="static")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# MCP status
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


def _companion_agent_turn(
    payload: FoodQueryRequest | AgentQueryRequest,
    user: str,
) -> dict[str, Any]:
    """STUBBED during the backend rebuild.

    The orchestrator + per-provider agents that powered this turn were removed
    so the routing/tool-calling architecture can be rebuilt from scratch. The
    OAuth/MCP layer is untouched. Until the new pipeline lands, this returns
    HTTP 503 so the frontend shows a clean "temporarily unavailable" state
    instead of erroring on a half-built backend."""
    raise HTTPException(
        status_code=503,
        detail=(
            "The assistant backend is being rebuilt and is temporarily "
            "unavailable. OAuth/account connections are unaffected."
        ),
    )


@app.get("/api/hero/greeting/")
def hero_greeting(user: str = Depends(get_current_user)) -> dict[str, Any]:
    """A short, catchy, personalized welcome line for the empty/hero state.

    Built from the user's profile (name/city/top interest), current weather at
    their saved address (Open-Meteo, keyless), and the time of day, then phrased
    by an LLM (Sarvam with fallback). Best-effort: always returns a usable
    greeting — degrades to a static time-of-day line if anything is missing.
    """
    from companion_api.hero_greeting import build_hero_greeting

    try:
        return build_hero_greeting(user)
    except Exception:
        logger.debug("hero greeting failed", exc_info=True)
        return {"greeting": "", "source": "error", "time_of_day": None, "weather": None}


def _message_requires_delivery_address(message: str) -> bool:
    text = f" {(message or '').strip().lower()} "
    delivery_words = {
        " order ",
        " buy ",
        " deliver ",
        " delivery ",
        " restaurant ",
        " restaurants ",
        " menu ",
        " cart ",
        " pizza ",
        " biryani ",
        " burger ",
        " dosa ",
        " milk ",
        " bread ",
        " eggs ",
        " atta ",
        " groceries ",
        " grocery ",
        " instamart ",
        " zepto ",
        " swiggy ",
        " zomato ",
    }
    return any(word in text for word in delivery_words)


@app.post("/api/agent/query/")
def agent_query_endpoint(
    payload: AgentQueryRequest,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    return _companion_agent_turn(payload, user)


@app.post("/api/food/query/")
def food_agent_full(
    payload: FoodQueryRequest,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    if not payload.address_id.strip() and _message_requires_delivery_address(payload.message):
        raise HTTPException(status_code=422, detail="address_id is required for delivery requests")
    return _companion_agent_turn(payload, user)


def scheduled_reminder_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    payload = ReminderEvent.model_validate(event)
    reminder = ReminderScheduler().mark_triggered(
        payload.member_id,
        payload.reminder_id,
    )

    if reminder:
        # Fetch member to get phone number for WhatsApp notification
        member = MemberRepository().get(payload.member_id)
        if member and member.get("phone_number"):
            from companion_api.whatsapp_client import WhatsAppClient

            title = reminder.get("title", "Reminder")
            description = reminder.get("description", "")
            msg = f"🔔 *{title}*"
            if description:
                msg += f"\n{description}"

            # If it's a confirmation-required reminder, add quick reply buttons/instructions
            if reminder.get("requires_confirmation"):
                msg += "\n\nReply 'yes' to complete or 'no' to snooze."

            WhatsAppClient().send_text_message(member["phone_number"], msg)

    return {
        "status": "ok",
        "triggered": bool(reminder),
        "triggered_at": datetime.utcnow().isoformat() + "Z",
    }


app.post("/api/voice/tool-execute/")(voice_tool_execute_endpoint)
app.post("/api/voice/token/")(voice_token_endpoint)
app.post("/api/voice/gemini-token/")(voice_gemini_token_endpoint)
app.post("/api/voice/log-turn/")(voice_log_turn_endpoint)
app.post("/api/voice/romanize/")(voice_romanize_endpoint)

# Routers extracted from this module (#29 — main.py decomposition).
from companion_api.routers.agent import router as agent_router  # noqa: E402
from companion_api.routers.auth import router as auth_router  # noqa: E402
from companion_api.routers.calendar import router as calendar_router  # noqa: E402
from companion_api.routers.chats import router as chats_router  # noqa: E402
from companion_api.routers.maps import router as maps_router  # noqa: E402
from companion_api.routers.mcp import router as mcp_router  # noqa: E402
from companion_api.routers.ondc import router as ondc_router  # noqa: E402
from companion_api.routers.profile import router as profile_router  # noqa: E402
from companion_api.routers.reminders import router as reminders_router  # noqa: E402
from companion_api.routers.tools import router as tools_router  # noqa: E402
from companion_api.routers.whatsapp import router as whatsapp_router  # noqa: E402

app.include_router(chats_router)
app.include_router(reminders_router)
app.include_router(profile_router)
app.include_router(maps_router)
app.include_router(whatsapp_router)
app.include_router(calendar_router)
app.include_router(tools_router)
app.include_router(auth_router)
app.include_router(mcp_router)
app.include_router(agent_router)
app.include_router(ondc_router)


@app.get("/{full_path:path}", response_class=HTMLResponse)
def spa_fallback(full_path: str):
    """SPA deep-link fallback: serve the shell for client-routed paths (`/c/<id>`).

    Registered last so concrete routes, `/api/*` routers and the `/assets`
    `/static` mounts always win. Unmatched `/api/*` stays a real 404 (never the
    HTML shell); every other GET returns `index.html` so a refresh or shared link
    to a client route boots the app instead of 404ing.
    """
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    return food_ui()


_http_handler = Mangum(app, lifespan="off")


def handler(event: Any, context: Any) -> Any:
    """Lambda entry: API Gateway requests through Mangum.

    The old self-invoked async-turn worker was removed with the legacy
    execution system; the new backend will reintroduce its own execution
    transport here if it needs one."""
    return _http_handler(event, context)
