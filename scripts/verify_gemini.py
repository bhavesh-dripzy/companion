#!/usr/bin/env python3

from pathlib import Path
import os

from dotenv import load_dotenv
from google import genai

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
if not api_key:
    kf = (os.getenv("GEMINI_API_KEY_FILE") or "").strip()
    if kf:
        try:
            api_key = Path(kf).read_text(encoding="utf-8").splitlines()[0].strip()
        except OSError:
            pass
if not api_key and (ROOT / ".gemini_local").is_file():
    api_key = (ROOT / ".gemini_local").read_text(encoding="utf-8").splitlines()[
        0
    ].strip()

if not api_key:
    raise SystemExit(
        "No Gemini key: set GEMINI_API_KEY or GOOGLE_API_KEY, GEMINI_API_KEY_FILE, "
        "or create .gemini_local (first line = key)."
    )

model = os.getenv("GEMINI_FOOD_ROUTER_MODEL", "gemini-2.5-flash-lite")

client = genai.Client(api_key=api_key)

response = client.models.generate_content(
    model=model,
    contents="Reply with exactly: OK — Gemini is reachable.",
)

print(response.text)