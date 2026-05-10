#!/usr/bin/env python3

from pathlib import Path
import os

from dotenv import load_dotenv
from google import genai

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

api_key = os.getenv("GEMINI_API_KEY", "").strip()

client = genai.Client(api_key=api_key)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Reply with exactly: OK — Gemini is reachable."
)

print(response.text)