"""Shared web helpers used by main.py and the OAuth routers (#29).

Lifted out of main.py so the OAuth route groups (calendar, mcp) can extract into
their own routers without importing main (which would be circular).
"""

from __future__ import annotations

from html import escape

from fastapi import Request

from companion_api.secrets import secret_or_env


def public_url(request: Request, path: str) -> str:
    """Absolute callback URL: PUBLIC_BASE_URL if set, else the forwarded host."""
    configured_base_url = secret_or_env("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured_base_url:
        return f"{configured_base_url}{path}"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    )
    return f"{proto}://{host}{path}"


def oauth_result_page(title: str, detail: str, *, ok: bool) -> str:
    """The small HTML page shown after an OAuth redirect completes."""
    safe_title = escape(title)
    safe_detail = escape(detail)
    color = "#047857" if ok else "#b91c1c"
    badge = "Connected" if ok else "Needs attention"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#f6f7f9; color:#111827; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; }}
    main {{ width:min(520px, calc(100% - 32px)); background:white; border:1px solid #e5e7eb; border-radius:12px; padding:28px; box-shadow:0 18px 50px rgba(15,23,42,.08); }}
    .badge {{ display:inline-flex; padding:5px 10px; border-radius:999px; color:{color}; background:#f9fafb; border:1px solid #e5e7eb; font-size:12px; font-weight:800; text-transform:uppercase; }}
    h1 {{ margin:16px 0 8px; font-size:24px; }}
    p {{ color:#4b5563; line-height:1.55; }}
    a {{ display:inline-flex; margin-top:14px; padding:10px 14px; border-radius:8px; background:#2563eb; color:white; text-decoration:none; font-weight:700; }}
  </style>
</head>
<body>
  <main>
    <span class="badge">{badge}</span>
    <h1>{safe_title}</h1>
    <p>{safe_detail}</p>
    <a href="/">Back to Companion</a>
  </main>
</body>
</html>"""
