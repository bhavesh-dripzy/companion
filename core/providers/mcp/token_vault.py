"""ensure_mcp_tokens_on_disk — the precondition every MCP entry point enforces.

Idempotent token hydration + proactive PKCE refresh, run before mcp-remote spawns.
Ported from app/services/zomato/mcp_executor; see token_store for the storage layer.

Why the proactive refresh: some MCP auth servers (Zomato as of 2026-06) require the
PKCE code_verifier on grant_type=refresh_token. mcp-remote correctly omits it, so its
internal refresh fails and it then deletes tokens.json and falls into a full OAuth
handshake that cannot complete on Lambda. We refresh here — with the verifier on disk —
and write the rotated tokens back to /tmp + Secrets Manager.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from core.providers.mcp.remote import config_dir, effective_config_dir, repo_root
from core.providers.mcp.token_store import (
    current_version_token_alias,
    get_mcp_token_status,
    mcp_token_secret,
    merge_mcp_token_secret,
    normalize_files_to_current_version,
    restore_mcp_auth_tokens,
    server_url_hash,
    token_relative_paths,
)

logger = logging.getLogger(__name__)

_PROACTIVE_REFRESH_MIN_AGE_S = int(os.getenv("MCP_PROACTIVE_REFRESH_MIN_AGE_SECONDS", "300"))


def _json_get(url: str) -> dict[str, Any] | None:
    try:
        resp = requests.get(url, headers={"Accept": "application/json"}, timeout=8)
    except requests.RequestException:
        return None
    if not resp.ok:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _well_known_candidates(base_url: str, suffix: str) -> list[str]:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    candidates = [f"{origin}/.well-known/{suffix}"]
    if path:
        candidates.append(f"{origin}/.well-known/{suffix}{path}")
        candidates.append(f"{origin}{path}/.well-known/{suffix}")
    seen: set[str] = set()
    return [u for u in candidates if not (u in seen or seen.add(u))]


def _discover_oauth_metadata(mcp_url: str) -> dict[str, Any]:
    protected_resource = None
    for url in _well_known_candidates(mcp_url, "oauth-protected-resource"):
        protected_resource = _json_get(url)
        if protected_resource:
            break
    auth_server_url = None
    if protected_resource:
        servers = protected_resource.get("authorization_servers")
        if isinstance(servers, list) and servers:
            auth_server_url = str(servers[0])
    auth_server_url = auth_server_url or mcp_url
    authorization_server = None
    for suffix in ("oauth-authorization-server", "openid-configuration"):
        for url in _well_known_candidates(auth_server_url, suffix):
            authorization_server = _json_get(url)
            if authorization_server:
                break
        if authorization_server:
            break
    if not authorization_server:
        raise RuntimeError("Could not discover MCP OAuth metadata from the server.")
    return {
        "authorization_server_url": auth_server_url,
        "protected_resource": protected_resource or {},
        "authorization_server": authorization_server,
    }


def _proactive_refresh(root: Path, mcp_url: str, user_id: str | None) -> None:
    cfg = effective_config_dir(user_id=user_id, root=root)
    h = server_url_hash(mcp_url)
    tokens_path = cfg / f"{h}_tokens.json"
    if not tokens_path.is_file():
        return
    tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
    client_info = json.loads((cfg / f"{h}_client_info.json").read_text(encoding="utf-8"))
    code_verifier = (cfg / f"{h}_code_verifier.txt").read_text(encoding="utf-8").strip()
    if not (tokens.get("refresh_token") and client_info.get("client_id") and code_verifier):
        return
    token_endpoint = (_discover_oauth_metadata(mcp_url).get("authorization_server") or {}).get(
        "token_endpoint"
    )
    if not token_endpoint:
        return
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client_info["client_id"],
        "code_verifier": code_verifier,
    }
    if client_info.get("client_secret"):
        payload["client_secret"] = client_info["client_secret"]
    resp = requests.post(
        token_endpoint, data=payload, headers={"Accept": "application/json"}, timeout=10
    )
    if resp.status_code in (400, 401):
        logger.warning("Refresh rejected (HTTP %s) for %s; re-restoring", resp.status_code, mcp_url)
        restore_mcp_auth_tokens(root, user_id=user_id)
        return
    if not resp.ok:
        return
    new_tokens = resp.json()
    if not (isinstance(new_tokens, dict) and new_tokens.get("access_token")):
        return
    if not new_tokens.get("refresh_token"):
        new_tokens["refresh_token"] = tokens["refresh_token"]
    rel = token_relative_paths(root, mcp_url)["tokens"]
    serialized = json.dumps(new_tokens, indent=2)
    base_cfg = config_dir(user_id, root)
    for target in (Path(rel), current_version_token_alias(root, Path(rel))):
        if target is None:
            continue
        dest = base_cfg / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(serialized, encoding="utf-8")
    secret = mcp_token_secret(user_id)
    existing = secret.get("files") if isinstance(secret, dict) else None
    existing = normalize_files_to_current_version(
        root, existing if isinstance(existing, dict) else {}
    )
    existing[rel] = serialized
    merge_mcp_token_secret(user_id, files=existing)
    logger.warning("Refreshed MCP tokens proactively for %s", mcp_url)


def ensure_mcp_tokens_on_disk(
    repo_root_path: Path, *, mcp_url: str, user_id: str | None = None
) -> dict[str, Any]:
    """Guarantee mcp-remote tokens are hydrated in the active config dir.

    Idempotent: a no-op when current-version tokens already exist on disk. On a cold
    container it pulls the user's tokens from Secrets Manager; then proactively refreshes
    (with the PKCE verifier) unless the on-disk tokens were rotated very recently.
    """
    root = repo_root_path or repo_root()
    status = get_mcp_token_status(root, mcp_url=mcp_url, user_id=user_id)
    needs_restore = not status["has_local_tokens"]
    if needs_restore:
        if not status["has_secret_tokens"]:
            return status
        try:
            restore_mcp_auth_tokens(root, user_id=user_id)
        except Exception:
            logger.exception("ensure_mcp_tokens_on_disk: restore failed for %s", mcp_url)
            return status
    try:
        cfg = effective_config_dir(user_id=user_id, root=root)
        tokens_path = cfg / f"{server_url_hash(mcp_url)}_tokens.json"
        if not tokens_path.is_file():
            return get_mcp_token_status(root, mcp_url=mcp_url, user_id=user_id)
        if not needs_restore:
            try:
                age = time.time() - tokens_path.stat().st_mtime
            except OSError:
                age = float("inf")
            if age < _PROACTIVE_REFRESH_MIN_AGE_S:
                return status
        _proactive_refresh(root, mcp_url, user_id)
    except Exception:
        logger.warning(
            "Proactive token refresh failed for %s; using existing tokens", mcp_url, exc_info=True
        )
    return get_mcp_token_status(root, mcp_url=mcp_url, user_id=user_id)
