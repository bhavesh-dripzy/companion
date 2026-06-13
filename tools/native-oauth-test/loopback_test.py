#!/usr/bin/env python3
"""Laptop loopback test for the native-shell onboarding flow — no phone, no Expo.

Runs the exact OAuth + completion chain the native app will: start a paste-mode
flow (localhost:3334 redirect), open the provider login in your normal browser,
catch the localhost redirect with a tiny local server, complete the token
exchange, vault the tokens, and verify them with a live tools/list.

The ONLY thing this swaps vs. the real phone app is the *carrier* of the auth
code: here a real localhost server catches the redirect (works on a laptop); in
the app a WebView navigation event catches it (works on a phone). Everything
downstream of "we have the redirect URL" is byte-for-byte identical.

Usage:
    AWS_PROFILE=companion AWS_REGION=ap-south-1 PYTHONPATH=. \\
        .venv/bin/python tools/native-oauth-test/loopback_test.py [--provider zepto] [--user admin_bhavesh]

    # boot-check only (start + bind + print URL, don't wait for login):
    ... loopback_test.py --dry
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

from companion_api import mcp_probe
from core.providers.mcp.oauth import PASTE_REDIRECT_URI, start_web_oauth
from core.providers.mcp.oauth_complete import complete_web_oauth_from_text

PROVIDERS = {
    "zepto": ("Zepto MCP", "https://mcp.zepto.co.in/mcp"),
    "district": ("District MCP", "https://mcp-server.district.in/mcp"),
    "groww": ("Groww MCP", "https://mcp.groww.in/mcp"),
    "zomato": ("Zomato MCP", "https://mcp-server.zomato.com/mcp"),
    "swiggy_food": ("Swiggy Food MCP", "https://mcp.swiggy.com/food"),
}

_HOST, _PORT, _PATH = "127.0.0.1", 3334, "/oauth/callback"


def _page(ok: bool, headline: str, detail: str) -> bytes:
    color = "#047857" if ok else "#b91c1c"
    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<body style="font:17px -apple-system,Segoe UI,Arial;display:grid;place-items:center;min-height:100vh;margin:0;background:#f6f7f9">
<main style="max-width:460px;padding:28px;background:#fff;border:1px solid #e5e7eb;border-radius:14px;text-align:center">
<div style="font-size:40px">{"✅" if ok else "⚠️"}</div>
<h1 style="color:{color};font-size:22px;margin:10px 0">{headline}</h1>
<p style="color:#475569;line-height:1.5">{detail}</p>
<p style="color:#94a3b8;font-size:13px">You can close this tab and return to the terminal.</p>
</main></body>""".encode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="zepto", choices=sorted(PROVIDERS))
    ap.add_argument("--user", default="admin_bhavesh")
    ap.add_argument("--dry", action="store_true", help="start + bind + print URL, then exit")
    args = ap.parse_args()

    label, mcp_url = PROVIDERS[args.provider]
    started = start_web_oauth(
        mcp_url=mcp_url,
        callback_url="https://app.jatayulabs.com/api/mcp/oauth/callback",
        user_id=args.user,
        provider_label=label,
        provider_id=args.provider,
    )
    if started["completion"] != "paste":
        print(
            f"\n{label} negotiated '{started['completion']}' (its callback is our hosted "
            "domain, not localhost), so the loopback catcher does not apply — it completes "
            "on the server automatically. Try --provider zepto or district.\n"
        )
        return 1

    redirect_host = urlsplit(PASTE_REDIRECT_URI).netloc
    auth_url = started["authorization_url"]
    print(f"\n  Provider : {label}  (completion=paste, redirect→{redirect_host})")
    print(f"  User     : {args.user}")
    print(f"\n  Authorize URL:\n  {auth_url}\n")

    httpd = HTTPServer((_HOST, _PORT), _make_handler(args.user, label))
    print(f"  Listening for the redirect on http://{_HOST}:{_PORT}{_PATH}")
    if args.dry:
        httpd.server_close()
        print("  --dry: server bound OK, exiting before login.\n")
        return 0

    print("  Opening your browser — log in, and watch this terminal.\n")
    webbrowser.open(auth_url)
    httpd._result = None  # type: ignore[attr-defined]
    while httpd._result is None:  # type: ignore[attr-defined]
        httpd.handle_request()
    httpd.server_close()

    ok, payload = httpd._result  # type: ignore[attr-defined]
    if not ok:
        print(f"\n  ✗ Completion failed: {payload}\n")
        return 1

    print("\n  ✓ Token exchange complete. Vaulted files:")
    for f in payload.get("persisted_files", []):
        print(f"      {f}")

    print("\n  Verifying the vaulted tokens with a live tools/list …")
    probe = mcp_probe.list_remote_tools(mcp_url, user_id=args.user)
    if probe.get("ok"):
        tools = probe.get("tools", [])
        names = ", ".join(t.get("name", "?") for t in tools[:6])
        print(f"  ✓ {label} returned {len(tools)} tools: {names}{' …' if len(tools) > 6 else ''}")
        print("\n  PASS — the full native onboarding chain works end to end.\n")
        return 0
    print(f"  ✗ tools/list failed: {probe.get('error_detail')}\n")
    return 1


def _make_handler(user_id: str, label: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.startswith(_PATH):
                self.send_response(404)
                self.end_headers()
                return
            full_url = f"http://{_HOST}:{_PORT}{self.path}"
            print(f"  ← caught redirect: {full_url}")
            try:
                result = complete_web_oauth_from_text(text=full_url, user_id=user_id)
                body = _page(True, f"{label} connected", "Tokens saved and being verified.")
                self.server._result = (True, result)  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001
                body = _page(False, "Could not finish", f"{type(e).__name__}: {e}")
                self.server._result = (False, f"{type(e).__name__}: {e}")  # type: ignore[attr-defined]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):  # silence default stderr logging
            pass

    return Handler


if __name__ == "__main__":
    sys.exit(main())
