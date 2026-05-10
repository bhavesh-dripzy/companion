#!/usr/bin/env python3
"""Quick check: OPENAI loads from .env and the API replies (does not echo the key)."""

from pathlib import Path
import os
import sys

from dotenv import load_dotenv
from openai import APIStatusError, OpenAI

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
if not api_key:
    print("ERROR: OPENAI_API_KEY is empty after loading .env", file=sys.stderr)
    sys.exit(1)

model = (os.getenv("OPENAI_FOOD_ROUTER_MODEL") or "gpt-4o-mini").strip()
client = OpenAI(api_key=api_key)

try:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hi"},
        ],
        max_tokens=32,
    )
except APIStatusError as e:
    if e.status_code == 401:
        print("AUTH FAILED: Invalid or revoked API key.", file=sys.stderr)
    elif e.status_code == 429:
        print(
            "KEY OK BUT LIMIT: OpenAI rejected the request (rate limit or insufficient quota).\n"
            "Check billing and usage: https://platform.openai.com/account/billing",
            file=sys.stderr,
        )
    else:
        print(f"API ERROR {e.status_code}: {e}", file=sys.stderr)
    sys.exit(2)

text = (resp.choices[0].message.content or "").strip()
print(f"OK (model={model})")
print("Reply:")
print(text)
print(f"id={resp.id}")
