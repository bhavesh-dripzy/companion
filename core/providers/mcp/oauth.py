"""Hosted MCP OAuth handshake (PKCE + dynamic client registration).

Ported from `app/services/zomato/mcp_executor`; the Companion-hosted callback
replaces mcp-remote's localhost callback (unusable on Lambda) and persists
mcp-remote-compatible token files via `token_store`. Metadata discovery is
shared with `token_vault`.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from core.providers.mcp.remote import config_dir, remote_version, repo_root
from core.providers.mcp.token_store import (
    _root_secret,
    mcp_token_secret,
    merge_mcp_token_secret,
    normalize_files_to_current_version,
    token_relative_paths,
)
from core.providers.mcp.token_vault import _discover_oauth_metadata

_OAUTH_PENDING_TTL_SECONDS = 15 * 60
_DEFAULT_MCP_REMOTE_VERSION = "0.1.38"


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _oauth_scope(metadata: dict[str, Any], static_scope: str | None = None) -> str:
    if static_scope and static_scope.strip():
        return static_scope.strip()
    protected = metadata.get("protected_resource") or {}
    protected_scopes = protected.get("scopes_supported")
    if isinstance(protected_scopes, list) and protected_scopes:
        return " ".join(str(s) for s in protected_scopes)
    auth = metadata.get("authorization_server") or {}
    auth_scopes = auth.get("scopes_supported")
    if isinstance(auth_scopes, list) and auth_scopes:
        return " ".join(str(s) for s in auth_scopes)
    return "openid email profile"


def start_web_oauth(
    *,
    repo_root_path: Path | None = None,
    mcp_url: str | None = None,
    callback_url: str,
    user_id: str | None = None,
    provider_label: str | None = None,
) -> dict[str, Any]:
    """Start a hosted OAuth flow and return the provider authorization URL."""
    root = repo_root_path or repo_root()
    url = mcp_url or os.getenv("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    metadata = _discover_oauth_metadata(url)
    auth_server = metadata["authorization_server"]
    registration_endpoint = auth_server.get("registration_endpoint")
    if not registration_endpoint:
        raise RuntimeError(
            "This MCP OAuth server does not advertise dynamic client registration. "
            "A hosted callback cannot be created automatically for this account."
        )

    scope = _oauth_scope(metadata)
    client_metadata = {
        "redirect_uris": [callback_url],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": "Companion Food Agent",
        "client_uri": callback_url.rsplit("/api/", 1)[0],
        "software_id": "2e6dc280-f3c3-4e01-99a7-8181dbd1d23d",
        "software_version": remote_version(root) or _DEFAULT_MCP_REMOTE_VERSION,
        "scope": scope,
    }
    reg_resp = requests.post(registration_endpoint, json=client_metadata, timeout=12)
    if not reg_resp.ok:
        raise RuntimeError(
            f"MCP OAuth client registration failed with HTTP {reg_resp.status_code}: "
            f"{reg_resp.text[:300]}"
        )
    client_info = reg_resp.json()
    if not isinstance(client_info, dict) or not client_info.get("client_id"):
        raise RuntimeError("MCP OAuth client registration did not return a client_id.")

    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    pending = {
        "state": state,
        "created_at": int(time.time()),
        "mcp_url": url,
        "callback_url": callback_url,
        "code_verifier": verifier,
        "client_info": client_info,
        "metadata": metadata,
        "user_id": user_id,
        "provider_label": provider_label,
    }
    secret_payload = mcp_token_secret(user_id)
    if not isinstance(secret_payload, dict):
        secret_payload = {}
    pending_map = secret_payload.get("oauth_pending")
    if not isinstance(pending_map, dict):
        pending_map = {}
    now = int(time.time())
    pending_map = {
        k: v
        for k, v in pending_map.items()
        if isinstance(v, dict) and now - int(v.get("created_at", 0)) < _OAUTH_PENDING_TTL_SECONDS
    }
    pending_map[state] = pending
    merge_mcp_token_secret(user_id, oauth_pending=pending_map)

    params = {
        "response_type": "code",
        "client_id": client_info["client_id"],
        "redirect_uri": callback_url,
        "resource": url,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
        "scope": scope,
    }
    authorization_url = f"{auth_server['authorization_endpoint']}?{urlencode(params)}"
    return {
        "status": "oauth_required",
        "authorization_url": authorization_url,
        "detail": (
            f"Continue to {provider_label or 'the provider'} and approve access. "
            "Companion will save the MCP session automatically."
        ),
        "mcp_url": url,
    }


def _find_oauth_pending_session(
    state: str,
) -> tuple[str | None, dict[str, Any], dict[str, Any], dict[str, Any]]:
    secret_payload = mcp_token_secret()
    pending_map = secret_payload.get("oauth_pending") if isinstance(secret_payload, dict) else None
    if isinstance(pending_map, dict):
        pending = pending_map.get(state)
        if isinstance(pending, dict):
            return None, secret_payload, pending_map, pending

    root = _root_secret()
    users = root.get("users") if isinstance(root, dict) else None
    if isinstance(users, dict):
        for user_id in users:
            user_payload = mcp_token_secret(str(user_id))
            user_pending_map = (
                user_payload.get("oauth_pending") if isinstance(user_payload, dict) else None
            )
            if not isinstance(user_pending_map, dict):
                continue
            pending = user_pending_map.get(state)
            if isinstance(pending, dict):
                return str(user_id), user_payload, user_pending_map, pending

    raise RuntimeError("No pending OAuth session was found. Start authorization again.")


def complete_web_oauth(
    *,
    repo_root_path: Path | None = None,
    state: str,
    code: str,
    provider: str = "zomato",
) -> dict[str, Any]:
    """Complete hosted OAuth and persist mcp-remote-compatible auth files."""
    root = repo_root_path or repo_root()
    user_id, secret_payload, pending_map, pending = _find_oauth_pending_session(state)
    user_id = pending.get("user_id") or user_id
    if int(time.time()) - int(pending.get("created_at", 0)) > _OAUTH_PENDING_TTL_SECONDS:
        pending_map.pop(state, None)
        merge_mcp_token_secret(user_id, oauth_pending=pending_map)
        raise RuntimeError("OAuth session expired. Start authorization again.")

    provider_label = pending.get("provider_label") or provider.replace("_", " ").title()
    metadata = pending.get("metadata") or {}
    auth_server = metadata.get("authorization_server") or {}
    client_info = pending.get("client_info") or {}
    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending["callback_url"],
        "client_id": client_info["client_id"],
        "code_verifier": pending["code_verifier"],
        "resource": pending["mcp_url"],
    }
    if client_info.get("client_secret"):
        token_payload["client_secret"] = client_info["client_secret"]
    token_resp = requests.post(
        auth_server["token_endpoint"],
        data=token_payload,
        headers={"Accept": "application/json"},
        timeout=12,
    )
    if not token_resp.ok:
        raise RuntimeError(
            f"{provider_label} token exchange failed with HTTP {token_resp.status_code}: "
            f"{token_resp.text[:300]}"
        )
    tokens = token_resp.json()
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise RuntimeError(f"{provider_label} token exchange did not return an access_token.")

    mcp_url = pending["mcp_url"]
    rel_paths = token_relative_paths(root, mcp_url)
    cfg_dir = config_dir(user_id, root)
    files = {
        rel_paths["client_info"]: json.dumps(client_info, indent=2),
        rel_paths["tokens"]: json.dumps(tokens, indent=2),
        rel_paths["code_verifier"]: str(pending["code_verifier"]),
    }
    for rel, content in files.items():
        dest = cfg_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    existing_files = secret_payload.get("files") if isinstance(secret_payload, dict) else None
    if not isinstance(existing_files, dict):
        existing_files = {}
    # Prune stale ``mcp-remote-<oldver>/...`` keys, then add the freshly-issued
    # files (already at the current-version prefix via token_relative_paths).
    merged_files = normalize_files_to_current_version(root, existing_files)
    merged_files.update(files)
    pending_map.pop(state, None)
    merge_mcp_token_secret(user_id, files=merged_files, oauth_pending=pending_map)
    return {
        "status": "connected_pending_test",
        "detail": f"{provider_label} authorization completed. Run Test Connection to verify access.",
        "provider_label": provider_label,
        "persisted_files": list(files.keys()),
        "mcp_url": mcp_url,
    }
