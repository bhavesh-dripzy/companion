"""Live prod-HTTP harness — drives the DEPLOYED API over real HTTP with a real
account, exactly as a client does (login → /api/agent/query/ → poll the async
job to the final envelope). Skipped unless creds are configured, so CI stays
green. This is the reusable seam for live-testing every agentic feature.

Assert on STRUCTURED signals (which provider/tool actually ran, which cards came
back, observable_trace events) — never brittle narrator prose.

SAFETY BOUNDARY (binding on all live tests):
  reads + reversible writes only. `ask()` is read-only. There is intentionally
  NO checkout / payment / order-placement helper here, and none may be added.
  A test that mutates real state (add-to-cart, address switch) must be marked
  `@pytest.mark.mutating` and clean up after itself; order/payment is never
  exercised against the real account.

Auth: provide EITHER a ready bearer token, OR username + password to log in.
Env:
  COMPANION_LIVE_BASE_URL   default https://app.jatayulabs.com
  COMPANION_LIVE_TOKEN      a bearer access token (skips login)
  COMPANION_LIVE_USERNAME   prod login (Cognito / legacy admin)
  COMPANION_LIVE_PASSWORD   prod password
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

_BASE_URL = os.getenv("COMPANION_LIVE_BASE_URL", "https://app.jatayulabs.com").rstrip("/")
_TOKEN = os.getenv("COMPANION_LIVE_TOKEN", "").strip()
_USERNAME = os.getenv("COMPANION_LIVE_USERNAME", "").strip()
_PASSWORD = os.getenv("COMPANION_LIVE_PASSWORD", "").strip()

_JOB_POLL_TIMEOUT = 90.0
_JOB_POLL_INTERVAL = 1.5


class LiveClient:
    """Authenticated client for the deployed agent API. `ask` is read-only."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def ask(self, message: str, **payload) -> dict:
        """Run one agent turn over real HTTP and resolve the async-job poll to the
        final response envelope. READ-ONLY — do not add mutating helpers here."""
        body = {"message": message, "execute_tools": True, **payload}
        resp = self._http.post("/api/agent/query/", json=body)
        resp.raise_for_status()
        envelope = resp.json()
        if envelope.get("status") == "accepted" and envelope.get("job_id"):
            envelope = self._poll(str(envelope["job_id"]))
        return envelope

    def _poll(self, job_id: str) -> dict:
        deadline = time.monotonic() + _JOB_POLL_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(_JOB_POLL_INTERVAL)
            resp = self._http.get(f"/api/agent/jobs/{job_id}/")
            resp.raise_for_status()
            job = resp.json()
            if job.get("status") == "done":
                return job.get("result") or {}
            if job.get("status") == "error":
                pytest.fail(f"live turn errored: {job.get('error')}")
        pytest.fail(f"live turn did not finish within {_JOB_POLL_TIMEOUT}s (job={job_id})")


@pytest.fixture(scope="session")
def live_client():
    if not _TOKEN and not (_USERNAME and _PASSWORD):
        pytest.skip("set COMPANION_LIVE_TOKEN, or COMPANION_LIVE_USERNAME + _PASSWORD, to run live")
    with httpx.Client(base_url=_BASE_URL, timeout=120.0) as http:
        token = _TOKEN
        if not token:
            login = http.post(
                "/api/login/",
                json={"username": _USERNAME, "password": _PASSWORD, "remember_me": False},
            )
            login.raise_for_status()
            token = login.json()["access_token"]
        http.headers["Authorization"] = f"Bearer {token}"
        yield LiveClient(http)
