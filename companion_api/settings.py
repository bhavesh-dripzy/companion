from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


AWS_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION", "ap-south-1")

FAMILIES_TABLE = os.getenv("FAMILIES_TABLE", "companion-families")
MEMBERS_TABLE = os.getenv("MEMBERS_TABLE", "companion-members")
MESSAGES_TABLE = os.getenv("MESSAGES_TABLE", "companion-messages")
REMINDERS_TABLE = os.getenv("REMINDERS_TABLE", "companion-reminders")
PROFILE_CACHE_TABLE = os.getenv("PROFILE_CACHE_TABLE", "companion-profile-cache")
LEAD_CACHE_TABLE = os.getenv("LEAD_CACHE_TABLE", "companion-lead-cache")
LEADGEN_QUEUE_URL = os.getenv("LEADGEN_QUEUE_URL", "")
GRAPH_SERVICE_URL = os.getenv("GRAPH_SERVICE_URL", "https://graph.houzzat.in")
GRAPH_SERVICE_TOKEN_SECRET = os.getenv("GRAPH_SERVICE_TOKEN_SECRET", "companion/graph-service-token")

REMINDER_TARGET_ARN = os.getenv("REMINDER_TARGET_ARN", "")
SCHEDULER_ROLE_ARN = os.getenv("SCHEDULER_ROLE_ARN", "")
SCHEDULER_GROUP_NAME = os.getenv("SCHEDULER_GROUP_NAME", "companion-reminders")
REMINDER_EMAIL_FROM = os.getenv("REMINDER_EMAIL_FROM", "")
REMINDER_DEFAULT_TIMEZONE = os.getenv("REMINDER_DEFAULT_TIMEZONE", "Asia/Kolkata")

COMPANION_WHATSAPP_CC_NUMBER = os.getenv("COMPANION_WHATSAPP_CC_NUMBER", "919773687877")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_WABA_ID = os.getenv("WHATSAPP_WABA_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")

ZOMATO_MCP_URL = os.getenv("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
NODE_BINARY = os.getenv("NODE_BINARY", "node")
EXECUTE_TOOLS_BY_DEFAULT = env_bool("EXECUTE_TOOLS_BY_DEFAULT", False)
EXECUTE_ZOMATO_BY_DEFAULT = env_bool("EXECUTE_ZOMATO_BY_DEFAULT", EXECUTE_TOOLS_BY_DEFAULT)

GOOGLE_MAPS_ROUTES_URL = os.getenv(
    "GOOGLE_MAPS_ROUTES_URL",
    "https://routes.googleapis.com/directions/v2:computeRoutes",
)
GOOGLE_MAPS_GEOCODE_URL = os.getenv(
    "GOOGLE_MAPS_GEOCODE_URL",
    "https://maps.googleapis.com/maps/api/geocode/json",
)
GOOGLE_MAPS_DEFAULT_TRAVEL_MODE = os.getenv("GOOGLE_MAPS_DEFAULT_TRAVEL_MODE", "DRIVE")
GOOGLE_MAPS_REQUEST_TIMEOUT_SECONDS = int(
    os.getenv("GOOGLE_MAPS_REQUEST_TIMEOUT_SECONDS", "10")
)
GOOGLE_MAPS_DEFAULT_LANGUAGE = os.getenv("GOOGLE_MAPS_DEFAULT_LANGUAGE", "en-IN")
GOOGLE_MAPS_DEFAULT_REGION = os.getenv("GOOGLE_MAPS_DEFAULT_REGION", "IN")

# ── Twilio Voice Calls ────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
TWILIO_VOICE_BRIDGE_URL = os.getenv(
    "TWILIO_VOICE_BRIDGE_URL", "https://voice.houzzat.in"
)
