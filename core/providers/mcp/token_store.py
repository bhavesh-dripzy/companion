"""MCP OAuth token vault — Secrets Manager <-> on-disk, version-namespaced.

Ported from app/services/zomato/mcp_executor (the vault is provider-agnostic).
Reuses core.providers.mcp.remote primitives for config-dir/version computation so
the bytes on disk land exactly where mcp-remote reads them. The version-namespacing
quirk is load-bearing: mcp-remote reads tokens from ``mcp-remote-<X.Y.Z>/`` and a
version bump makes files saved under the old prefix invisible — so keys are
normalized to the current version on every restore/persist.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

from core.providers.mcp.remote import (
    _safe_user,
    config_dir,
    effective_config_dir,
    remote_version,
    repo_root,
)

logger = logging.getLogger(__name__)

TOKEN_SECRET_ID = "companion/mcp-tokens"
_MAX_TOKEN_FILE_BYTES = 256 * 1024
_LEGACY_MCP_OWNER = os.getenv("LEGACY_ADMIN_USERNAME", "").strip() or None


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def server_url_hash(mcp_url: str) -> str:
    return hashlib.md5(mcp_url.encode("utf-8")).hexdigest()


def expected_token_paths(root: Path, mcp_url: str, user_id: str | None = None) -> dict[str, str]:
    cfg = effective_config_dir(user_id=user_id, root=root)
    h = server_url_hash(mcp_url)
    return {
        "client_info": str(cfg / f"{h}_client_info.json"),
        "tokens": str(cfg / f"{h}_tokens.json"),
        "code_verifier": str(cfg / f"{h}_code_verifier.txt"),
    }


def token_relative_paths(root: Path, mcp_url: str) -> dict[str, str]:
    version = remote_version(root)
    prefix = f"mcp-remote-{version}/" if version else ""
    h = server_url_hash(mcp_url)
    return {
        "client_info": f"{prefix}{h}_client_info.json",
        "tokens": f"{prefix}{h}_tokens.json",
        "code_verifier": f"{prefix}{h}_code_verifier.txt",
    }


def _has_token_file_for_url(paths: list[str], mcp_url: str) -> bool:
    token_name = f"{server_url_hash(mcp_url)}_tokens.json"
    return any(
        str(p).replace("\\", "/").endswith("/" + token_name) or str(p) == token_name for p in paths
    )


def current_version_token_alias(root: Path, rel: Path) -> Path | None:
    version = remote_version(root)
    if not version or not rel.parts:
        return None
    first = rel.parts[0]
    current_first = f"mcp-remote-{version}"
    if not first.startswith("mcp-remote-") or first == current_first:
        return None
    return Path(current_first, *rel.parts[1:])


def normalize_files_to_current_version(root: Path, files: dict[str, str] | None) -> dict[str, str]:
    """Rewrite every ``mcp-remote-<oldver>/...`` key to the current version prefix."""
    if not files:
        return {}
    version = remote_version(root)
    if not version:
        return dict(files)
    current_prefix = f"mcp-remote-{version}/"
    out: dict[str, str] = {}
    for rel, content in files.items():
        key = str(rel or "").replace("\\", "/")
        if not key:
            continue
        if key.startswith("mcp-remote-") and "/" in key:
            key = current_prefix + key.split("/", 1)[1]
        out[key] = str(content)
    return out


def safe_relative_token_path(name: str) -> Path | None:
    cleaned = str(name or "").replace("\\", "/").strip().lstrip("/")
    if not cleaned:
        return None
    rel = Path(cleaned)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        return None
    if len(rel.parts) > 4:
        return None
    return rel


def iter_token_files(cfg_dir: Path) -> list[Path]:
    if not cfg_dir.is_dir():
        return []
    out: list[Path] = []
    for p in cfg_dir.rglob("*"):
        if not p.is_file() or p.name.endswith("_debug.log") or p.name == ".DS_Store":
            continue
        try:
            if p.stat().st_size > _MAX_TOKEN_FILE_BYTES:
                continue
        except OSError:
            continue
        out.append(p)
    return sorted(out)


def _root_secret() -> dict[str, Any]:
    try:
        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=TOKEN_SECRET_ID)
        return json.loads(resp.get("SecretString") or "{}")
    except Exception:
        return {}


def mcp_token_secret(user_id: str | None = None) -> dict[str, Any]:
    """Return the MCP token payload, scoped to ``user_id`` when supplied."""
    payload = _root_secret()
    if not user_id:
        return payload
    safe_user = _safe_user(user_id)
    if not safe_user:
        return {}
    users = payload.get("users") if isinstance(payload, dict) else None
    users = users if isinstance(users, dict) else {}
    user_payload = users.get(safe_user)
    if isinstance(user_payload, dict):
        return user_payload
    if safe_user == _LEGACY_MCP_OWNER and isinstance(payload, dict):
        return {
            k: payload[k]
            for k in ("files", "custom_providers", "pairing_sessions", "oauth_pending")
            if k in payload
        }
    return {}


def _write_secret(payload: dict[str, Any]) -> None:
    client = boto3.client("secretsmanager")
    raw = json.dumps(payload)
    try:
        client.put_secret_value(SecretId=TOKEN_SECRET_ID, SecretString=raw)
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(
            Name=TOKEN_SECRET_ID, SecretString=raw, Description="MCP OAuth tokens for Companion"
        )


def merge_mcp_token_secret(user_id: str | None = None, **updates: Any) -> dict[str, Any]:
    if user_id:
        payload = _root_secret()
        payload = payload if isinstance(payload, dict) else {}
        users = payload.get("users")
        users = users if isinstance(users, dict) else {}
        safe_user = _safe_user(user_id) or user_id
        current = users.get(safe_user)
        if not isinstance(current, dict):
            current = mcp_token_secret(user_id)
            current = current if isinstance(current, dict) else {}
        current.update(updates)
        current["updated_at"] = _utc_now_iso()
        users[safe_user] = current
        payload["users"] = users
        payload["updated_at"] = _utc_now_iso()
        _write_secret(payload)
        return current
    payload = _root_secret()
    payload = payload if isinstance(payload, dict) else {}
    payload.update(updates)
    payload["updated_at"] = _utc_now_iso()
    _write_secret(payload)
    return payload


def restore_mcp_auth_tokens(root: Path | None = None, user_id: str | None = None) -> bool:
    """Fetch OAuth tokens from Secrets Manager and write them to the MCP config dir."""
    root = root or repo_root()
    cfg_dir = config_dir(user_id, root)
    secret = mcp_token_secret(user_id)
    raw_files = secret.get("files") if isinstance(secret, dict) else None
    if not isinstance(raw_files, dict) or not raw_files:
        return False
    files_data = normalize_files_to_current_version(root, raw_files)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files_data.items():
        rel = safe_relative_token_path(name)
        if rel is None:
            logger.warning("Skipping unsafe MCP token path from secret: %s", name)
            continue
        dest = cfg_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(str(content), encoding="utf-8")
    logger.info(
        "Restored %d MCP token file(s) (mcp-remote=%s).",
        len(files_data),
        remote_version(root) or "?",
    )
    return True


def get_mcp_token_status(
    root: Path | None = None, *, mcp_url: str, user_id: str | None = None
) -> dict[str, Any]:
    """Summarize MCP token state. ``has_local_tokens`` is STRICT (current-version path)."""
    root = root or repo_root()
    cfg_dir = config_dir(user_id, root)
    local_paths = [p.relative_to(cfg_dir).as_posix() for p in iter_token_files(cfg_dir)]
    secret = mcp_token_secret(user_id)
    secret_files = list((secret.get("files") or {}).keys())
    expected = expected_token_paths(root, mcp_url, user_id=user_id)
    expected_token_rel = token_relative_paths(root, mcp_url)["tokens"]
    return {
        "config_dir": str(cfg_dir),
        "effective_config_dir": str(effective_config_dir(user_id=user_id, root=root)),
        "local_files": local_paths,
        "secret_files": secret_files,
        "has_local_tokens": expected_token_rel in local_paths,
        "has_secret_tokens": expected_token_rel in secret_files
        or _has_token_file_for_url(secret_files, mcp_url),
        "expected_files": expected,
        "expected_tokens_present": Path(expected["tokens"]).is_file(),
    }
