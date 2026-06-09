"""One-time pairing-code sessions for browser-local mcp-remote auth sync.

Ported from `app/services/zomato/mcp_executor`. The user runs a short
`curl ... | python3` script on a laptop whose browser can complete the provider
sign-in; mcp-remote writes its token files there and the script POSTs them back,
which `complete_pairing_session` validates (by code + TTL) and persists into the
config dir + Secrets Manager. Sessions live in the per-user MCP token secret.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Any

from core.providers.mcp.remote import config_dir, remote_version, repo_root
from core.providers.mcp.token_store import (
    _root_secret,
    mcp_token_secret,
    merge_mcp_token_secret,
    normalize_files_to_current_version,
    safe_relative_token_path,
)

_PAIRING_TTL_SECONDS = 15 * 60
_DEFAULT_MCP_REMOTE_VERSION = "0.1.38"
_DEFAULT_ZOMATO_MCP_URL = "https://mcp-server.zomato.com/mcp"


def create_pairing_session(
    *,
    repo_root_path: Path | None = None,
    mcp_url: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root_path or repo_root()
    url = mcp_url or os.getenv("ZOMATO_MCP_URL", _DEFAULT_ZOMATO_MCP_URL)
    code = secrets.token_urlsafe(18)
    secret_payload = mcp_token_secret(user_id)
    if not isinstance(secret_payload, dict):
        secret_payload = {}
    sessions = secret_payload.get("pairing_sessions")
    if not isinstance(sessions, dict):
        sessions = {}
    now = int(time.time())
    sessions = {
        k: v
        for k, v in sessions.items()
        if isinstance(v, dict) and now - int(v.get("created_at", 0)) < _PAIRING_TTL_SECONDS
    }
    sessions[code] = {
        "created_at": now,
        "mcp_url": url,
        "mcp_remote_version": remote_version(root) or _DEFAULT_MCP_REMOTE_VERSION,
        "user_id": user_id,
    }
    merge_mcp_token_secret(user_id, pairing_sessions=sessions)
    return {
        "code": code,
        "mcp_url": url,
        "mcp_remote_version": sessions[code]["mcp_remote_version"],
        "expires_in_seconds": _PAIRING_TTL_SECONDS,
    }


def get_pairing_session_info(code: str, user_id: str | None = None) -> dict[str, Any] | None:
    secret_payload = mcp_token_secret(user_id)
    sessions = secret_payload.get("pairing_sessions") if isinstance(secret_payload, dict) else None
    if not isinstance(sessions, dict):
        return None
    session = sessions.get(code)
    if not isinstance(session, dict):
        return None
    if int(time.time()) - int(session.get("created_at", 0)) > _PAIRING_TTL_SECONDS:
        sessions.pop(code, None)
        merge_mcp_token_secret(user_id, pairing_sessions=sessions)
        return None
    result = dict(session)
    if user_id is not None:
        result["user_id"] = user_id
    return result


def find_pairing_session_info(code: str) -> dict[str, Any] | None:
    """Look up a pairing session by code across the root payload and every user."""
    session = get_pairing_session_info(code)
    if session:
        return session

    payload = _root_secret()
    users = payload.get("users") if isinstance(payload, dict) else None
    if not isinstance(users, dict):
        return None
    for user_id in users:
        session = get_pairing_session_info(code, user_id=str(user_id))
        if session:
            return session
    return None


def complete_pairing_session(
    *,
    repo_root_path: Path | None = None,
    code: str,
    files: dict[str, str],
    user_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root_path or repo_root()
    secret_payload = mcp_token_secret(user_id)
    sessions = secret_payload.get("pairing_sessions") if isinstance(secret_payload, dict) else None
    if not isinstance(sessions, dict):
        raise RuntimeError("No pairing session was found. Generate a new connection command.")
    session = sessions.get(code)
    if not isinstance(session, dict):
        raise RuntimeError("Pairing code was not recognized. Generate a new connection command.")
    if int(time.time()) - int(session.get("created_at", 0)) > _PAIRING_TTL_SECONDS:
        sessions.pop(code, None)
        merge_mcp_token_secret(user_id, pairing_sessions=sessions)
        raise RuntimeError("Pairing code expired. Generate a new connection command.")

    cfg_dir = config_dir(user_id, root)
    written: dict[str, str] = {}
    for name, content in files.items():
        rel = safe_relative_token_path(name)
        if rel is None or not content:
            continue
        dest = cfg_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = str(content)
        dest.write_text(text, encoding="utf-8")
        written[rel.as_posix()] = text

    if not any(name.endswith("_tokens.json") for name in written):
        raise RuntimeError("Pairing did not receive an mcp-remote tokens file.")

    existing_files = secret_payload.get("files") if isinstance(secret_payload, dict) else None
    if not isinstance(existing_files, dict):
        existing_files = {}
    # Normalize prior entries to the current version prefix before merging the
    # newly written files (which already use current-version paths).
    merged_files = normalize_files_to_current_version(root, existing_files)
    merged_files.update(written)
    sessions.pop(code, None)
    merge_mcp_token_secret(user_id, files=merged_files, pairing_sessions=sessions)
    return {
        "status": "ok",
        "detail": "MCP session synced. Return to Companion and test the connection.",
        "persisted_files": list(written.keys()),
    }
