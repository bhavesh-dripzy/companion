"""Hosted MCP OAuth completion — token exchange + mcp-remote-compatible persistence.

Finishes both completion modes started by `oauth.start_web_oauth`: the hosted
callback redirect, and the paste-back flow where the user pastes the localhost
URL the provider redirected to after login.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests

from core.providers.mcp.oauth import OAUTH_PENDING_TTL_SECONDS
from core.providers.mcp.remote import config_dir, repo_root
from core.providers.mcp.token_store import (
    _root_secret,
    mcp_token_secret,
    merge_mcp_token_secret,
    normalize_files_to_current_version,
    token_relative_paths,
)


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


def extract_state_and_code(text: str) -> tuple[str, str]:
    """Pull state + code out of a pasted post-login URL or bare query string."""
    raw = (text or "").strip().strip("\"'")
    query = ""
    if "://" in raw:
        split = urlsplit(raw)
        query = split.query or split.fragment
    elif "=" in raw:
        query = raw.split("?", 1)[-1]
    params = parse_qs(query)
    state = (params.get("state") or [""])[0].strip()
    code = (params.get("code") or [""])[0].strip()
    if not state or not code:
        raise RuntimeError(
            "The pasted text is missing the state/code values. Copy the FULL address "
            "of the page you landed on after logging in (it starts with http://localhost)."
        )
    return state, code


def complete_web_oauth(
    *,
    repo_root_path: Path | None = None,
    state: str,
    code: str,
    provider: str = "zomato",
    expected_user_id: str | None = None,
) -> dict[str, Any]:
    """Complete hosted OAuth and persist mcp-remote-compatible auth files."""
    root = repo_root_path or repo_root()
    user_id, secret_payload, pending_map, pending = _find_oauth_pending_session(state)
    user_id = pending.get("user_id") or user_id
    if expected_user_id and user_id and str(user_id) != str(expected_user_id):
        raise RuntimeError("This authorization was started by a different Companion user.")
    if int(time.time()) - int(pending.get("created_at", 0)) > OAUTH_PENDING_TTL_SECONDS:
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
        "provider_id": pending.get("provider_id"),
        "persisted_files": list(files.keys()),
        "mcp_url": mcp_url,
    }


def complete_web_oauth_from_text(*, text: str, user_id: str | None = None) -> dict[str, Any]:
    """Complete a paste-back OAuth flow from the URL the user pasted."""
    state, code = extract_state_and_code(text)
    return complete_web_oauth(state=state, code=code, expected_user_id=user_id)
