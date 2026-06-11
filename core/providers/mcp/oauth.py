"""Hosted MCP OAuth start (PKCE + dynamic client registration).

The Companion-hosted callback replaces mcp-remote's localhost callback
(unusable on Lambda). Providers that allowlist redirect domains and reject the
hosted callback fall back to a localhost-registered client whose post-login
redirect URL the user pastes back into Companion ("paste" completion);
`oauth_complete` finishes either flow with the same token exchange.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from core.providers.mcp.remote import remote_version, repo_root
from core.providers.mcp.token_store import mcp_token_secret, merge_mcp_token_secret
from core.providers.mcp.token_vault import _discover_oauth_metadata

OAUTH_PENDING_TTL_SECONDS = 15 * 60
PASTE_REDIRECT_URI = "http://localhost:3334/oauth/callback"
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


def _register_client(
    registration_endpoint: str, redirect_uri: str, scope: str, software_version: str
) -> dict[str, Any]:
    """Dynamically register an OAuth client for one redirect URI."""
    client_metadata = {
        "redirect_uris": [redirect_uri],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": "Companion Food Agent",
        "client_uri": redirect_uri.rsplit("/api/", 1)[0],
        "software_id": "2e6dc280-f3c3-4e01-99a7-8181dbd1d23d",
        "software_version": software_version,
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
    return client_info


def _granted_redirect_uri(client_info: dict[str, Any], requested: str) -> str | None:
    """The redirect URI the server actually registered: the requested one, a
    localhost one from a fixed/shared client, or None if neither is usable."""
    uris = client_info.get("redirect_uris")
    if not isinstance(uris, list) or not uris:
        return requested
    if requested in uris:
        return requested
    for uri in uris:
        if isinstance(uri, str) and uri.startswith("http://localhost"):
            return uri
    return None


def _negotiate_client(
    registration_endpoint: str, callback_url: str, scope: str, version: str, force_paste: bool
) -> tuple[str, str, dict[str, Any]]:
    """Pick completion mode + redirect URI: hosted callback if the server takes
    it, else a localhost URI completed by paste-back."""
    if not force_paste:
        try:
            client_info = _register_client(registration_endpoint, callback_url, scope, version)
        except RuntimeError:
            client_info = None
        if client_info:
            granted = _granted_redirect_uri(client_info, callback_url)
            if granted == callback_url:
                return "redirect", callback_url, client_info
            if granted:
                return "paste", granted, client_info
    client_info = _register_client(registration_endpoint, PASTE_REDIRECT_URI, scope, version)
    granted = _granted_redirect_uri(client_info, PASTE_REDIRECT_URI)
    if not granted:
        raise RuntimeError(
            "The MCP OAuth server rejected both the hosted callback and a localhost redirect."
        )
    return "paste", granted, client_info


def _store_pending(user_id: str | None, state: str, pending: dict[str, Any]) -> None:
    """Persist the pending OAuth session, pruning expired ones."""
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
        if isinstance(v, dict) and now - int(v.get("created_at", 0)) < OAUTH_PENDING_TTL_SECONDS
    }
    pending_map[state] = pending
    merge_mcp_token_secret(user_id, oauth_pending=pending_map)


def start_web_oauth(
    *,
    repo_root_path: Path | None = None,
    mcp_url: str | None = None,
    callback_url: str,
    user_id: str | None = None,
    provider_label: str | None = None,
    provider_id: str | None = None,
    force_paste: bool = False,
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
    version = remote_version(root) or _DEFAULT_MCP_REMOTE_VERSION
    completion, redirect_uri, client_info = _negotiate_client(
        registration_endpoint, callback_url, scope, version, force_paste
    )

    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    _store_pending(
        user_id,
        state,
        {
            "state": state,
            "created_at": int(time.time()),
            "mcp_url": url,
            "callback_url": redirect_uri,
            "code_verifier": verifier,
            "client_info": client_info,
            "metadata": metadata,
            "user_id": user_id,
            "provider_label": provider_label,
            "provider_id": provider_id,
            "completion": completion,
        },
    )

    params = {
        "response_type": "code",
        "client_id": client_info["client_id"],
        "redirect_uri": redirect_uri,
        "resource": url,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
        "scope": scope,
    }
    label = provider_label or "the provider"
    if completion == "redirect":
        detail = (
            f"Continue to {label} and approve access. "
            "Companion will save the MCP session automatically."
        )
    else:
        detail = (
            f"Log in to {label}. You will land on a page that cannot open — copy that "
            "page's full address and paste it back in Companion to finish connecting."
        )
    return {
        "status": "oauth_required",
        "authorization_url": f"{auth_server['authorization_endpoint']}?{urlencode(params)}",
        "completion": completion,
        "detail": detail,
        "mcp_url": url,
    }
