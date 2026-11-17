"""Verify the exact public release, security headers, canonical host, and routes."""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PUBLIC = Path(__file__).resolve().parents[1] / "landing"
BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://jatayulabs.com").rstrip("/")


def fetch(path: str):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "JatayuSiteReleaseVerifier/1.0", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return response.read(), response.headers


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def redirect(url: str, target: str) -> None:
    try:
        urllib.request.build_opener(NoRedirect).open(url, timeout=25)
    except urllib.error.HTTPError as error:
        assert error.code in (301, 308), error.code
        assert error.headers["Location"] == target, error.headers["Location"]
        return
    raise AssertionError(f"No redirect from {url}")


def main() -> None:
    expected = json.loads((PUBLIC / "release-manifest.json").read_text())
    for attempt in range(40):
        try:
            body, headers = fetch("/")
            assert hashlib.sha256(body).digest() == hashlib.sha256((PUBLIC / "index.html").read_bytes()).digest(), "Homepage is not the reviewed release"
            assert headers.get("Strict-Transport-Security")
            assert headers.get("X-Content-Type-Options") == "nosniff"
            assert "script-src 'self'" in headers.get("Content-Security-Policy", "")
            assert "frame-ancestors 'self'" in headers.get("Content-Security-Policy", "")
            assert headers.get("Permissions-Policy")
            break
        except (OSError, AssertionError) as error:
            if attempt == 39:
                raise
            print(f"Waiting for DNS/CDN propagation ({attempt + 1}/40): {error}", flush=True)
            time.sleep(20)
    remote_manifest, _ = fetch("/release-manifest.json")
    assert json.loads(remote_manifest) == expected

    def verify_file(item):
        content, _ = fetch("/" + item["path"])
        assert hashlib.sha256(content).hexdigest() == item["sha256"], item["path"]
        return item["path"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        verified = list(pool.map(verify_file, expected["files"]))
    demo, _ = fetch("/product/")
    assert b"Interactive demo with synthetic data" in demo
    assert b"noindex,nofollow" in demo
    try:
        fetch("/missing-page-release-verification")
    except urllib.error.HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError("Missing pages must return 404")
    if BASE == "https://jatayulabs.com":
        redirect("http://jatayulabs.com/", "https://jatayulabs.com/")
        redirect("https://www.jatayulabs.com/", "https://jatayulabs.com/")
    print(f"Public release verified: {len(verified)} files, HTTPS, security headers, demo routing, canonical redirect, and 404.")


if __name__ == "__main__":
    main()
