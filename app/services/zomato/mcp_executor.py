"""
Run Zomato MCP tool_plan via stdio bridge (mcp-remote + node), same stack as Cursor.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import importlib.util
import json
import logging
import os
import re
import secrets
import shutil
import time
from base64 import urlsafe_b64encode
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from typing import Any

import boto3
import requests

from app.services.debug_trace import trace

logger = logging.getLogger(__name__)

_PLACEHOLDER = "__ADDRESS_ID__"
_PLACEHOLDER_ALIASES: dict[str, tuple[str, ...]] = {
    "__ADDRESS_ID__": ("address_id", "addressId"),
    "__RESTAURANT_ID__": (
        "restaurant_id",
        "restaurantId",
        "res_id",
        "resId",
        "restaurantIdString",
    ),
    "__CART_ID__": ("cart_id", "cartId"),
    "__ORDER_ID__": ("order_id", "orderId"),
    # NOTE: __VARIANT_ID__, __MENU_ITEM_ID__, and __ITEM_ID__ are intentionally
    # NOT in this map.
    #
    # * __VARIANT_ID__ used to exist but is a single scalar, and
    #   `create_cart.items[]` needs a distinct variant per row, so the
    #   "first-seen wins" global binding silently pinned every row to one
    #   item.
    # * __MENU_ITEM_ID__ / __ITEM_ID__ used to be harvested from menu
    #   envelopes for *some* old code-side matching path that no longer
    #   exists. No Zomato MCP tool actually accepts ``item_id`` /
    #   ``menu_item_id`` as INPUT — those fields are only OUTPUTS on menu
    #   listings. Keeping them harvested + carried forward let the planner
    #   sneak ``"variant_id": "__MENU_ITEM_ID__"`` into create_cart rows;
    #   the executor would substitute (e.g. to ``ctl_767452667``) and
    #   Zomato would reject with "variant id required".
    #
    # The planner LLM is now fully responsible for emitting one literal
    # ``variant_id`` per cart row (sourced from the menu envelopes in the
    # same plan and/or the carried-forward ``session_menu`` block). The
    # executor does no variant matching, validation, or property resolution.
}

# Tools that issue a fresh value for a placeholder. When a turn re-runs one of
# these (e.g. user adds another item → planner emits ``create_cart`` again,
# or pivots to a different restaurant → planner emits ``get_restaurants_for_keyword``
# again), any value seeded from a previous turn is stale and must be cleared
# so the new response's value is re-learned. Mirrors the MCP semantics — not
# a business rule. Keep this map narrow: only include tools whose contract is
# "I create / select the named resource".
_TOOL_ISSUES_PLACEHOLDER: dict[str, tuple[str, ...]] = {
    # Each call selects a *new* restaurant from the user's keyword. Without
    # clearing, a __RESTAURANT_ID__ seeded from turn N silently survives a
    # turn N+1 pivot ("show me Domino's instead") because the existing
    # "first-wins" learning in _learn_placeholders_from_payload refuses to
    # overwrite. Result: subsequent menu/cart tools target the wrong
    # restaurant. Same fix-class as cart_id.
    "get_restaurants_for_keyword": ("__RESTAURANT_ID__",),
    "create_cart": ("__CART_ID__",),
    "checkout_cart": ("__ORDER_ID__",),
    "place_order": ("__ORDER_ID__",),
}
_TOKEN_SECRET_ID = "companion/mcp-tokens"
_MCP_INIT_TIMEOUT_SECONDS = int(os.getenv("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "20"))
_MCP_TOOL_TIMEOUT_SECONDS = int(os.getenv("ZOMATO_MCP_TOOL_TIMEOUT_SECONDS", "30"))
# Per-restaurant menu slice — the SAME N rows are:
#   1. surfaced to the response LLM (trimmed envelope),
#   2. stamped into ``session_menu`` for the next turn's planner.
# Keeping these two slices identical means the user can only see items the
# planner can also pick by literal ``variant_id`` next turn — eliminating
# the "response promised Oova Superstar Egg Biryani but planner couldn't
# find its variant_id" mismatch. A 115-item Burger Singh menu was ~25 KB
# / ~6.4K tokens; trimming to 8 cuts that ~14x. Tunable via env.
_MENU_ITEMS_PER_RESTAURANT = max(1, int(os.getenv("ZOMATO_MENU_ITEMS_PER_RESTAURANT", "8")))
_MAX_TOKEN_FILE_BYTES = 256 * 1024
_OAUTH_PENDING_TTL_SECONDS = 15 * 60
_PAIRING_TTL_SECONDS = 15 * 60
_LEGACY_MCP_OWNER: str | None = os.getenv("LEGACY_ADMIN_USERNAME", "").strip() or None


def _safe_user_id(user_id: str | None) -> str | None:
    if not user_id:
        return None
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", user_id.strip())
    return safe[:80] or None


def _mcp_remote_config_dir(repo_root: Path, user_id: str | None = None) -> Path:
    """
    mcp-remote persists Zomato OAuth tokens under MCP_REMOTE_CONFIG_DIR
    (default: ~/.mcp-auth). On servers, www-data's home is not your laptop — set
    MCP_REMOTE_CONFIG_DIR to a project-owned directory and copy tokens there.
    """
    env = os.getenv("MCP_REMOTE_CONFIG_DIR", "").strip()
    safe_user = _safe_user_id(user_id)
    if env:
        base = Path(env).expanduser().resolve()
        return (base / "users" / safe_user).resolve() if safe_user else base
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        base = Path("/tmp/.mcp-auth").resolve()
        return (base / "users" / safe_user).resolve() if safe_user else base
    base = (repo_root / ".mcp-auth").resolve()
    return (base / "users" / safe_user).resolve() if safe_user else base


def _mcp_remote_version(repo_root: Path) -> str | None:
    pkg = repo_root / "node_modules" / "mcp-remote" / "package.json"
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version")
    return str(version) if version else None


def _mcp_remote_effective_config_dir(repo_root: Path, user_id: str | None = None) -> Path:
    """
    mcp-remote appends mcp-remote-<version> under MCP_REMOTE_CONFIG_DIR.
    The app's config dir is the parent; token files live in this effective dir.
    """
    base = _mcp_remote_config_dir(repo_root, user_id=user_id)
    version = _mcp_remote_version(repo_root)
    if version:
        return base / f"mcp-remote-{version}"
    return base


def _server_url_hash(mcp_url: str) -> str:
    return hashlib.md5(mcp_url.encode("utf-8")).hexdigest()


def _expected_token_paths(repo_root: Path, mcp_url: str, user_id: str | None = None) -> dict[str, str]:
    cfg = _mcp_remote_effective_config_dir(repo_root, user_id=user_id)
    h = _server_url_hash(mcp_url)
    return {
        "client_info": str(cfg / f"{h}_client_info.json"),
        "tokens": str(cfg / f"{h}_tokens.json"),
        "code_verifier": str(cfg / f"{h}_code_verifier.txt"),
    }


def _mcp_remote_token_relative_paths(repo_root: Path, mcp_url: str) -> dict[str, str]:
    version = _mcp_remote_version(repo_root)
    prefix = f"mcp-remote-{version}/" if version else ""
    h = _server_url_hash(mcp_url)
    return {
        "client_info": f"{prefix}{h}_client_info.json",
        "tokens": f"{prefix}{h}_tokens.json",
        "code_verifier": f"{prefix}{h}_code_verifier.txt",
    }


def _has_token_file_for_url(paths: list[str], mcp_url: str) -> bool:
    """True when *any* version of the token file exists in ``paths``.

    Used as a LOOSE match for ``has_secret_tokens`` — the secret store
    can hold tokens written under a prior ``mcp-remote-X.Y.Z/`` prefix,
    and ``restore_mcp_auth_tokens`` will alias them into the current
    version's subdirectory on the next restore. NEVER use this for the
    LOCAL readiness signal: a stale ``mcp-remote-<old>/<hash>_tokens.json``
    is invisible to a newer ``mcp-remote`` binary and pretending it's
    "present" suppresses the auto-restore that would write the
    current-version alias.
    """
    token_name = f"{_server_url_hash(mcp_url)}_tokens.json"
    return any(str(path).replace("\\", "/").endswith("/" + token_name) or str(path) == token_name for path in paths)


def _current_version_token_alias(repo_root: Path, rel: Path) -> Path | None:
    version = _mcp_remote_version(repo_root)
    if not version or not rel.parts:
        return None
    first = rel.parts[0]
    current_first = f"mcp-remote-{version}"
    if not first.startswith("mcp-remote-") or first == current_first:
        return None
    return Path(current_first, *rel.parts[1:])


def _safe_relative_token_path(name: str) -> Path | None:
    cleaned = str(name or "").replace("\\", "/").strip().lstrip("/")
    if not cleaned:
        return None
    rel = Path(cleaned)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        return None
    if len(rel.parts) > 4:
        return None
    return rel


def _iter_token_files(cfg_dir: Path) -> list[Path]:
    if not cfg_dir.is_dir():
        return []
    out: list[Path] = []
    for p in cfg_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.name.endswith("_debug.log") or p.name == ".DS_Store":
            continue
        try:
            if p.stat().st_size > _MAX_TOKEN_FILE_BYTES:
                continue
        except OSError:
            continue
        out.append(p)
    return sorted(out)


def _stdio_env(repo_root: Path, user_id: str | None = None) -> dict[str, str]:
    cfg = _mcp_remote_config_dir(repo_root, user_id=user_id)
    try:
        cfg.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("could not create MCP_REMOTE_CONFIG_DIR %s: %s", cfg, e)
    base = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    base["MCP_REMOTE_CONFIG_DIR"] = str(cfg)
    return base


def diagnose_mcp_environment(
    repo_root: Path | None = None,
    *,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root or repo_root_from_settings()
    url = mcp_url or os.getenv("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    node = node_binary or os.getenv("NODE_BINARY", "node")
    node_path = shutil.which(node)
    proxy_script = root / "node_modules" / "mcp-remote" / "dist" / "proxy.js"
    base_cfg = _mcp_remote_config_dir(root, user_id=user_id)
    effective_cfg = _mcp_remote_effective_config_dir(root, user_id=user_id)
    expected = _expected_token_paths(root, url, user_id=user_id)
    token_status = get_mcp_token_status(root, mcp_url=url, user_id=user_id)
    return {
        "node_binary": node,
        "node_path": node_path,
        "node_available": bool(node_path),
        "mcp_remote_installed": proxy_script.is_file(),
        "mcp_remote_version": _mcp_remote_version(root),
        "python_mcp_sdk_available": importlib.util.find_spec("mcp") is not None,
        "proxy_script": str(proxy_script),
        "config_dir": str(base_cfg),
        "effective_config_dir": str(effective_cfg),
        "expected_files": expected,
        "expected_tokens_present": Path(expected["tokens"]).is_file(),
        "token_status": token_status,
    }


def _flatten_exception_group(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        nested: list[BaseException] = []
        for child in exc.exceptions:
            nested.extend(_flatten_exception_group(child))
        return nested
    return [exc]


def _humanize_mcp_failure(repo_root: Path, exc: BaseException, mcp_url: str | None = None) -> str:
    leaves = _flatten_exception_group(exc)
    parts = [f"{type(e).__name__}: {e}" for e in leaves] or [str(exc)]
    detail = "; ".join(parts)
    provider_name = "MCP"
    if mcp_url:
        host = urlparse(mcp_url).netloc or mcp_url
        provider_name = host.replace("mcp.", "").split(".")[0].title() + " MCP"
    if any(isinstance(e, TimeoutError) for e in leaves):
        detail += (
            f" | {provider_name} timed out while initializing or calling a tool. "
            "This usually means OAuth/token setup is missing, tokens were not restored into the active mcp-remote version directory, or the MCP server is slow."
        )
    if any("Connection closed" in str(e) for e in leaves) or any(
        type(e).__name__ == "McpError" for e in leaves
    ):
        cfg = _mcp_remote_config_dir(repo_root)
        detail += (
            f" | {provider_name} uses OAuth. Use the hosted Authorize flow so Companion can "
            f"write mcp-remote-compatible token files into {cfg} automatically."
        )
    return detail


def _repository_root_via_mcp_remote(start: Path) -> Path | None:
    """First ancestor of ``start`` where ``npm`` installed ``mcp-remote`` (Lambda: /var/task)."""
    for d in start.parents:
        bridge = d / "node_modules" / "mcp-remote" / "dist" / "proxy.js"
        if bridge.is_file():
            return d
    return None


def repo_root_from_settings() -> Path:
    """Deploy root containing ``node_modules/mcp-remote`` (not ``…/app``).

    Lambda/FastAPI: Python lives under ``/var/task/app/`` but the Docker image runs
    ``npm ci`` in ``LAMBDA_TASK_ROOT`` (``/var/task``). Fallback ``parents[2]`` pointed
    at ``…/app`` and wrongly reported ``missing_bridge``.
    """
    here = Path(__file__).resolve()
    task_raw = os.getenv("LAMBDA_TASK_ROOT", "").strip()
    if task_raw:
        task_root = Path(task_raw).resolve()
        if (task_root / "node_modules" / "mcp-remote" / "dist" / "proxy.js").is_file():
            return task_root
    via_bridge = _repository_root_via_mcp_remote(here)
    if via_bridge is not None:
        return via_bridge
    try:
        import companion_api.settings as companion_settings

        return Path(companion_settings.BASE_DIR)
    except Exception:
        pass
    try:
        from django.conf import settings

        return Path(settings.BASE_DIR)
    except Exception:
        pass
    return here.parents[2]


def substitute_address_id(obj: Any, address_id: str) -> Any:
    if obj == _PLACEHOLDER:
        return address_id
    if isinstance(obj, dict):
        return {k: substitute_address_id(v, address_id) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_address_id(i, address_id) for i in obj]
    return obj


def substitute_placeholders(obj: Any, values: dict[str, str]) -> Any:
    if isinstance(obj, str) and obj in values:
        return values[obj]
    if isinstance(obj, dict):
        return {k: substitute_placeholders(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_placeholders(i, values) for i in obj]
    return obj


def _mentions_placeholder(obj: Any) -> bool:
    if isinstance(obj, str) and obj in _PLACEHOLDER_ALIASES:
        return True
    if isinstance(obj, dict):
        return any(_mentions_placeholder(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_mentions_placeholder(i) for i in obj)
    return False


def _unresolved_placeholders(obj: Any) -> list[str]:
    found: list[str] = []
    if isinstance(obj, str) and obj in _PLACEHOLDER_ALIASES:
        return [obj]
    if isinstance(obj, dict):
        for value in obj.values():
            found.extend(_unresolved_placeholders(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_unresolved_placeholders(value))
    return sorted(set(found))


def _tool_result_payload(result: Any) -> dict[str, Any]:
    """Serialize an MCP ``call_tool`` result into a JSON-friendly envelope.

    Backward-compatible shape: ``{"is_error": bool, "data": <json>}``.

    Additively surfaces non-text content blocks so chat hosts can render them:

    * ``images``      — list of ``{"mime_type": str, "data_b64": str}`` for every
      ``type: "image"`` block (e.g. the Zomato UPI QR returned by
      ``checkout_cart``). Older consumers that ignore this key are unaffected.
    * ``attachments`` — list of embedded MCP resources (``type: "resource"``)
      with ``uri`` / ``mime_type`` / ``text`` when the server returns one
      (e.g. FastMCP-UI widget HTML stubs).
    """
    out: dict[str, Any] = {"is_error": bool(getattr(result, "isError", False))}
    try:
        # Walk content blocks once to harvest non-text artifacts. This runs
        # regardless of whether the server also returns structuredContent —
        # Zomato's checkout_cart returns BOTH a structured text message AND
        # a binary QR image, and the old code path silently dropped the image.
        images: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        for blk in getattr(result, "content", ()) or []:
            btype = getattr(blk, "type", None)
            if btype == "image":
                data_b64 = getattr(blk, "data", None) or ""
                if not data_b64:
                    continue
                images.append(
                    {
                        "mime_type": str(getattr(blk, "mimeType", "image/png") or "image/png"),
                        "data_b64": str(data_b64),
                    }
                )
            elif btype == "resource":
                resource = getattr(blk, "resource", None)
                if resource is None:
                    continue
                attachments.append(
                    {
                        "uri": getattr(resource, "uri", None),
                        "mime_type": getattr(resource, "mimeType", None),
                        "text": getattr(resource, "text", None),
                    }
                )
        if images:
            out["images"] = images
        if attachments:
            out["attachments"] = attachments

        if result.structuredContent is not None:
            out["data"] = result.structuredContent
            return out
        parsed: list[Any] = []
        for blk in getattr(result, "content", ()) or []:
            btype = getattr(blk, "type", None)
            if btype == "text":
                txt = getattr(blk, "text", "") or ""
                try:
                    parsed.append(json.loads(txt))
                except json.JSONDecodeError:
                    parsed.append({"raw_text": txt})
            elif btype == "image":
                # Real bytes already captured into ``out["images"]`` above;
                # keep a lightweight tombstone here so the legacy "blocks"
                # shape still lists every block in order.
                parsed.append(
                    {
                        "type": "image",
                        "mime_type": getattr(blk, "mimeType", None),
                    }
                )
            else:
                parsed.append({"type": btype or "?", "repr": str(blk)})
        out["data"] = parsed[0] if len(parsed) == 1 else {"blocks": parsed}
        return out
    except Exception as e:
        logger.warning("serialize tool result: %s", e)
        out["serialization_error"] = str(e)
        return out


# ---------------------------------------------------------------------------
# Payment / UPI QR extraction (Zomato checkout_cart returns an image content
# block holding a standard ``upi://pay?...`` QR PNG). We surface it to the
# chat host so the UI can render the QR for the user to scan.
# ---------------------------------------------------------------------------


_QR_DEFAULT_PATH = Path("/tmp/zomato_qr.png")


def _safe_qr_filename_suffix(cart_id: str | None) -> str | None:
    if not cart_id:
        return None
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(cart_id))[:64]
    return safe or None


def _decode_upi_uri_from_png_bytes(png_bytes: bytes) -> str | None:
    """Best-effort QR decode via OpenCV. Returns ``None`` if cv2 is missing
    or the QR could not be decoded."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return None
    try:
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        # Zomato's QR is tiny (45x45). Upscale to give the detector more pixels.
        big = cv2.resize(
            img,
            (img.shape[1] * 15, img.shape[0] * 15),
            interpolation=cv2.INTER_NEAREST,
        )
        detector = cv2.QRCodeDetector()
        data, _, _ = detector.detectAndDecode(big)
        if not data:
            big = cv2.resize(
                img,
                (img.shape[1] * 30, img.shape[0] * 30),
                interpolation=cv2.INTER_NEAREST,
            )
            data, _, _ = detector.detectAndDecode(big)
        return data or None
    except Exception as e:
        logger.info("QR decode failed: %s", e)
        return None


def _parse_upi_uri(uri: str) -> dict[str, Any]:
    """Pull standard NPCI fields out of a ``upi://pay?...`` URI."""
    info: dict[str, Any] = {"upi_link": uri}
    try:
        parsed = urlparse(uri)
        if parsed.scheme.lower() != "upi":
            return info
        params = parse_qs(parsed.query)

        def _first(key: str) -> str | None:
            v = params.get(key)
            return v[0] if v else None

        info["payee_vpa"] = _first("pa")
        info["payee_name"] = _first("pn")
        info["transaction_ref"] = _first("tr")
        info["amount_inr"] = _first("am")
        info["currency"] = _first("cu") or "INR"
        info["merchant_category_code"] = _first("mc")
        info["transaction_note"] = _first("tn")
    except Exception as e:
        logger.info("parse upi uri failed: %s", e)
    return info


def _extract_payment_from_checkout(
    envelope: dict[str, Any],
    *,
    cart_id: str | None = None,
) -> dict[str, Any] | None:
    """Build a payment summary from a ``checkout_cart`` envelope.

    Returns ``None`` when the envelope carries no image (e.g. ``cash_on_delivery``
    checkouts that don't issue a QR).

    Side effect: writes the QR PNG to ``/tmp/zomato_qr.png`` (and a cart-scoped
    copy when ``cart_id`` is known) so chat hosts can either reference the
    file path directly or use the inline base64 data from the envelope.
    """
    images = envelope.get("images") or []
    if not images:
        return None
    first = images[0]
    data_b64 = first.get("data_b64") or ""
    mime = first.get("mime_type") or "image/png"
    if not data_b64:
        return None
    try:
        raw_bytes = base64.b64decode(data_b64)
    except Exception as e:
        logger.warning("Could not decode QR base64: %s", e)
        return None

    ext = "png"
    if "/" in mime:
        ext = mime.split("/")[-1].split(";")[0].lower() or "png"

    payment: dict[str, Any] = {
        "qr_mime_type": mime,
        "qr_b64": data_b64,
    }

    qr_paths: list[str] = []
    try:
        _QR_DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _QR_DEFAULT_PATH.write_bytes(raw_bytes)
        qr_paths.append(str(_QR_DEFAULT_PATH))
    except OSError as e:
        logger.info("Could not write QR to %s: %s", _QR_DEFAULT_PATH, e)

    suffix = _safe_qr_filename_suffix(cart_id)
    if suffix:
        try:
            scoped = _QR_DEFAULT_PATH.parent / f"zomato_qr_{suffix}.{ext}"
            scoped.write_bytes(raw_bytes)
            qr_paths.append(str(scoped))
        except OSError:
            pass

    if qr_paths:
        payment["qr_paths"] = qr_paths
        payment["qr_path"] = qr_paths[0]

    upi_uri = _decode_upi_uri_from_png_bytes(raw_bytes)
    if upi_uri:
        payment.update(_parse_upi_uri(upi_uri))

    return payment


def _find_best_address_id(tool_payload: dict[str, Any], location_text: str | None = None) -> str | None:
    blob = tool_payload.get("data")
    if blob is None:
        return None
    
    addresses: list[dict[str, Any]] = []
    data_dict = blob if isinstance(blob, dict) else (blob[0] if isinstance(blob, list) and blob else {})
    if isinstance(data_dict, dict):
        # Handle both direct {addresses: []} and {result: {addresses: []}}
        raw_addresses = data_dict.get("addresses") or data_dict.get("result", {}).get("addresses")
        if isinstance(raw_addresses, list):
            addresses = [a for a in raw_addresses if isinstance(a, dict)]

    if not addresses:
        return None

    # 1. Try exact match on tag/label/name
    target = (location_text or "").strip().lower()
    if target:
        for addr in addresses:
            for key in ("address_tag", "addressTag", "location_name", "name", "label"):
                val = str(addr.get(key) or "").strip().lower()
                if val == target:
                    aid = addr.get("address_id") or addr.get("addressId") or addr.get("id")
                    if aid: return str(aid).strip()

        # 2. Try partial match on address line
        for addr in addresses:
            line = str(addr.get("address_line") or addr.get("addressLine") or "").strip().lower()
            if target in line:
                aid = addr.get("address_id") or addr.get("addressId") or addr.get("id")
                if aid: return str(aid).strip()

    # 3. Fallback to first address
    aid = addresses[0].get("address_id") or addresses[0].get("addressId") or addresses[0].get("id")
    return str(aid).strip() if aid else None


def _first_address_id_from_saved_payload(tool_payload: dict[str, Any]) -> str | None:
    return _find_best_address_id(tool_payload)


def _extract_address_rows_from_envelope(
    tool_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Pull the user's saved Zomato addresses out of a ``get_saved_addresses_for_user`` envelope.

    Each returned row has ``address_id`` + ``label`` plus any of the
    upstream fields the response LLM and frontend can use as nudge context
    (``addressTag``, ``address_line``, ``location_name``, …). The shape is
    deliberately permissive — frontends only need ``address_id`` + ``label``,
    everything else just rides along untouched.
    """
    blob = tool_payload.get("data") if isinstance(tool_payload, dict) else None
    if blob is None:
        return []
    data_dict = blob if isinstance(blob, dict) else (blob[0] if isinstance(blob, list) and blob else {})
    if not isinstance(data_dict, dict):
        return []
    raw = (
        data_dict.get("addresses")
        or (data_dict.get("result") or {}).get("addresses")
    )
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        aid = item.get("address_id") or item.get("addressId") or item.get("id")
        if aid is None:
            continue
        sid = str(aid).strip()
        if not sid:
            continue
        # Build a friendly label: prefer tag + line, fall back to line, tag,
        # or whatever string field is present. Matches the same priority
        # ``saved_address_label_from_mcp_item`` uses for the addresses
        # dropdown in the React frontend, so labels are consistent across
        # both surfaces.
        tag = str(item.get("addressTag") or item.get("address_tag") or "").strip()
        line = str(
            item.get("address_line")
            or item.get("addressLine")
            or item.get("location_name")
            or item.get("formatted_address")
            or ""
        ).strip()
        if tag and line and line.lower() != tag.lower():
            label = f"{tag} — {line}"
        else:
            label = line or tag or sid
        rows.append(
            {
                **item,
                "address_id": sid,
                "label": label,
            }
        )
    return rows


def _walk_json(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_json(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_json(value)


def _learn_placeholders_from_payload(
    values: dict[str, str],
    tool_payload: dict[str, Any],
) -> None:
    data = tool_payload.get("data")
    for obj in _walk_json(data):
        if not isinstance(obj, dict):
            continue
        for placeholder, aliases in _PLACEHOLDER_ALIASES.items():
            if placeholder in values:
                continue
            for alias in aliases:
                val = obj.get(alias)
                if val is None:
                    continue
                text = str(val).strip()
                if text:
                    values[placeholder] = text
                    break


_MAX_MENU_CATEGORY_ENRICH = 12


def _listing_item_mappings_blob(envelope: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Return (item_mappings, inner_data dict) from a get_menu_items_listing envelope."""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
    if not data:
        return None, None
    result = data.get("result") if isinstance(data.get("result"), dict) else None
    if not result:
        return None, None
    inner = result.get("data") if isinstance(result.get("data"), dict) else None
    if not inner:
        return None, None
    ims = inner.get("item_mappings")
    if not isinstance(ims, list):
        return None, inner
    rows = [x for x in ims if isinstance(x, dict)]
    return (rows or None), inner


def _variant_price_map_from_category_menu(category_envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    data = category_envelope.get("data") if isinstance(category_envelope.get("data"), dict) else {}
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    inner = result.get("data") if isinstance(result.get("data"), dict) else {}
    items = inner.get("menu_items")
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        vid = str(it.get("variant_id") or "").strip()
        if not vid:
            continue
        out[vid] = {
            "price": it.get("price"),
            "discounted_price": it.get("discounted_price"),
        }
    return out


# ---------------------------------------------------------------------------
# Menu index — per-session memory of variant_id -> menu entry, used solely
# for surfacing ``session_menu`` to the next turn (the carry-forward block
# the planner reads to pick a literal variant_id without re-running a menu
# tool). The executor does NO matching, fuzzy lookup, or property-based
# variant picking — those responsibilities live entirely in the planner LLM
# which has full conversational context. See RCA for "second option" cart
# drop: name-matching here silently dropped any planner-side synthetic name
# that didn't exactly match the menu envelope; the contract is now strict —
# every ``create_cart`` row must carry a literal ``variant_id``.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Restaurant index (multi-restaurant memory across turns)
# ---------------------------------------------------------------------------
# `__RESTAURANT_ID__` is a single scalar — it can only point at one restaurant
# at a time, typically the first hit of the latest keyword search. That breaks
# a very common flow:
#   T1: user asks "show me biryani options"           → list of 5 places
#   T2: user picks one ("show me BOX8 menu")          → planner re-searches
#         Zomato's keyword search is fuzzy and may return Domino's #1, so the
#         menu fetched is the wrong restaurant.
# Fix: every time `get_restaurants_for_keyword` (or any other tool that lists
# restaurants) runs, we extract every (name, res_id) pair shown and persist
# them as a small index. The pipeline echoes this index back to the next
# turn, and surfaces it to the planner so it can use the *literal* res_id
# for a restaurant the user has already seen — no re-search needed.
# ---------------------------------------------------------------------------

# Cap how many restaurants we remember per session. The user-visible
# response list rarely exceeds 5-8 rows, and the planner only needs the
# index when the user picks one by name — keeping more than that bloats
# every prompt for no real recall benefit. Env-tunable.
_MAX_REMEMBERED_RESTAURANTS = max(
    1, int(os.getenv("ZOMATO_REMEMBERED_RESTAURANT_CAP", "8"))
)

# Cross-restaurant LRU cap on ``session_menu``. Two sources contribute:
#   * a menu-fetch tool → ``_MENU_ITEMS_PER_RESTAURANT`` (8) rows per call.
#   * a keyword-search tool → ``_KEYWORD_ITEMS_PER_RESTAURANT`` (3) rows
#     for each of up to ``_MAX_REMEMBERED_RESTAURANTS`` (8) restaurants in
#     one envelope, so up to 24 rows from one search.
# 48 is sized for the common path "find burger options" (24 rows) followed
# by 3 menu opens (+ 24 rows = 48 total) — the user can pivot across
# 3 restaurants' full top-8 menus AFTER a keyword search without the
# oldest restaurant's items getting evicted prematurely. Each entry is
# small (name + variant_id + a few addon ids) so 48 stays well under the
# prompt-size budget.
_MAX_REMEMBERED_MENU_ITEMS = max(
    _MENU_ITEMS_PER_RESTAURANT,
    int(os.getenv("ZOMATO_REMEMBERED_MENU_ITEMS_CAP", "48")),
)


def _collect_restaurants_from_envelope(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract (res_id, name, rating, distance_km, eta) pairs from a search envelope.

    Operates on the `get_restaurants_for_keyword` response shape — but is
    tolerant of nested ``data.results[]`` and ``data.data.results[]`` since
    Zomato wraps things differently across endpoints.
    """
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return []
    data = envelope.get("data")
    candidates: list[Any] = []
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            candidates = data["results"]
        elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("results"), list):
            candidates = data["data"]["results"]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in candidates:
        if not isinstance(row, dict):
            continue
        rid_raw = row.get("res_id") or row.get("restaurant_id") or row.get("resId")
        name = row.get("name") or row.get("restaurant_name")
        if rid_raw is None or not isinstance(name, str) or not name.strip():
            continue
        rid = str(rid_raw).strip()
        if not rid or rid in seen:
            continue
        seen.add(rid)
        entry: dict[str, Any] = {"res_id": rid, "name": name.strip()}
        # Keep a few signals that help disambiguation in the prompt; all optional.
        for k_src, k_dst in (
            ("rating", "rating"),
            ("eta", "eta"),
            ("distance", "distance_km"),
            ("serviceability_status", "serviceability_status"),
        ):
            v = row.get(k_src)
            if v is not None:
                entry[k_dst] = v
        out.append(entry)
    return out


def _merge_recent_restaurants(
    existing: list[dict[str, Any]],
    new_entries: list[dict[str, Any]],
    *,
    cap: int = _MAX_REMEMBERED_RESTAURANTS,
) -> list[dict[str, Any]]:
    """LRU-style merge: new entries land at the *front*, duplicates by res_id
    are replaced, and the list is trimmed to ``cap``.

    Ordering by recency lets the planner prefer "the restaurant just shown"
    when names are ambiguous, while the older tail still answers callbacks
    like "actually try the second one I saw earlier".
    """
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    # New entries first (most recent context wins on conflict)
    for e in new_entries:
        rid = e.get("res_id")
        if not isinstance(rid, str) or not rid:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = e
    # Then existing, skipping anything already inserted
    for e in existing or []:
        if not isinstance(e, dict):
            continue
        rid = e.get("res_id")
        if not isinstance(rid, str) or not rid or rid in by_id:
            continue
        by_id[rid] = e
        order.append(rid)
    return [by_id[r] for r in order[:cap]]


# ---------------------------------------------------------------------------
# session_menu — cross-turn LRU of menu items the user has been shown
# ---------------------------------------------------------------------------
# Same idea as ``session_restaurants`` but at the item level. When the
# planner is composing a follow-up turn ("add the chicken keto salad" /
# "the medium pan one"), the planner LLM reads this list and can produce
# a direct ``create_cart`` step without re-running ``get_menu_items_listing``.
# It also carries the addon/variant schema the LLM needs in order to
# resolve customization picks without another menu fetch.
#
# Storage shape per entry (same as ``menu_index`` values, plus ``res_id``):
#   {res_id, variant_id, item_name, price, discounted_price?, categories?,
#    variants: [...], addon_group_ids: [...], addon_groups: [...]}
# ---------------------------------------------------------------------------


def _session_menu_key(entry: dict[str, Any]) -> str:
    """Stable cache key — ``(res_id, variant_id)`` lower-cased.

    Note: two restaurants can legally share a variant_id (Zomato's IDs are
    globally unique across the platform, but we don't trust that across
    restaurants). Including res_id makes the cache safe even if collisions
    happen on a future schema change.
    """
    rid = str(entry.get("res_id") or "").strip()
    vid = str(entry.get("variant_id") or "").strip()
    return f"{rid}|{vid}".lower()


def _menu_index_to_session_entries(
    menu_index: dict[str, dict[str, Any]],
    res_id: str | None,
) -> list[dict[str, Any]]:
    """Snapshot a per-turn ``menu_index`` into a list ready for session storage.

    Skips entries that lack a usable ``variant_id`` and stamps ``res_id`` on
    each row so cross-restaurant sessions stay disambiguated. Order follows
    the dict insertion order, which the executor maintains in "first time
    seen this turn" order.
    """
    out: list[dict[str, Any]] = []
    rid = (res_id or "").strip() or None
    for entry in menu_index.values():
        vid = str(entry.get("variant_id") or "").strip()
        if not vid:
            continue
        # NOTE: never name a local ``copy`` in this module — the file imports
        # ``copy`` at module scope and any later ``copy.deepcopy(...)`` in the
        # same function would become an UnboundLocalError. Bit me once
        # already in ``_run_session_inner`` (RCA: "copy = dict(entry)").
        snapshot = dict(entry)
        if rid and not snapshot.get("res_id"):
            snapshot["res_id"] = rid
        out.append(snapshot)
    return out


def _merge_session_menu(
    existing: list[dict[str, Any]],
    new_entries: list[dict[str, Any]],
    *,
    cap: int = _MAX_REMEMBERED_MENU_ITEMS,
) -> list[dict[str, Any]]:
    """LRU merge across turns — newest entries at the front, cap-trimmed.

    Conflict resolution (same item appears in both lists, keyed by
    ``(res_id, variant_id)``): the entry with richer customization
    metadata wins, then the newer entry refreshes scalar fields like
    ``price`` / ``discounted_price``. This is the cross-envelope analogue
    of ``_pick_richer_menu_entry``.
    """
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for e in new_entries:
        if not isinstance(e, dict):
            continue
        key = _session_menu_key(e)
        if not key or key == "|":
            continue
        prev = by_key.get(key)
        if prev is None:
            order.append(key)
            by_key[key] = e
        else:
            by_key[key] = _pick_richer_menu_entry(prev, e)
    for e in existing or []:
        if not isinstance(e, dict):
            continue
        key = _session_menu_key(e)
        if not key or key == "|":
            continue
        if key in by_key:
            by_key[key] = _pick_richer_menu_entry(by_key[key], e)
        else:
            order.append(key)
            by_key[key] = e
    return [by_key[k] for k in order[:cap]]


def _hydrate_menu_index_from_session(
    session_menu: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Seed a fresh per-turn ``menu_index`` from carried-forward session_menu.

    Keyed by ``variant_id`` so cross-envelope merges (same variant seen by
    both ``get_menu_items_listing`` and ``get_restaurant_menu_by_categories``)
    deduplicate cleanly. The index is consumed only by the session_menu
    echo at end-of-turn — no cart-time matching happens here.
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(session_menu, list):
        return out
    for entry in session_menu:
        if not isinstance(entry, dict):
            continue
        vid = str(entry.get("variant_id") or "").strip()
        if not vid:
            continue
        prev = out.get(vid)
        if prev is None:
            out[vid] = entry
        else:
            out[vid] = _pick_richer_menu_entry(prev, entry)
    return out


def _extract_addon_groups_table(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pull the envelope-root ``all_addons_data`` block into a lookup table.

    Returns ``{mg_id -> {id, name, min, max, items: [{id, name, price, variant_id?}]}}``.
    Only ``get_restaurant_menu_by_categories`` populates this block; for the
    listing tool the call returns ``{}``.

    Each leaf row's ``item_metadata`` field carries the underlying variant_id
    (encoded as ``{"v": 1014891416, ...}``). We expose it as ``variant_id``
    (with the canonical ``v_`` prefix) so downstream consumers can use it for
    description/disambiguation, even though ``create_cart`` only needs the
    ``ctl_`` leaf id for ``add_ons[]``.
    """
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return {}
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    inner = result.get("data") if isinstance(result.get("data"), dict) else {}
    raw_groups = inner.get("all_addons_data")
    if not isinstance(raw_groups, list):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for g in raw_groups:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or "").strip()
        if not gid:
            continue
        leaves: list[dict[str, Any]] = []
        for it in g.get("items") or []:
            if not isinstance(it, dict):
                continue
            ctl = str(it.get("id") or "").strip()
            if not ctl:
                continue
            leaf: dict[str, Any] = {
                "id": ctl,
                "name": str(it.get("name") or "").strip() or None,
                "price": it.get("price"),
                "state": it.get("item_state"),
            }
            # The "v" key inside item_metadata is the leaf variant_id (no
            # v_ prefix). We expose it so the response LLM can describe
            # alternatives with their full IDs if needed.
            raw_meta = it.get("item_metadata")
            if isinstance(raw_meta, str):
                try:
                    parsed = json.loads(raw_meta)
                    v_int = parsed.get("v") if isinstance(parsed, dict) else None
                    if isinstance(v_int, int) and v_int > 0:
                        leaf["variant_id"] = f"v_{v_int}"
                except Exception:
                    pass
            leaves.append(leaf)
        out[gid] = {
            "id": gid,
            "name": str(g.get("name") or "").strip() or gid,
            "min": int(g.get("min_selection") or 0),
            "max": int(g.get("max_selection") or 0),
            "items": leaves,
        }
    return out


def _normalise_variant_entries(
    raw_variants: Any, fallback_addons: list[str] | None
) -> list[dict[str, Any]]:
    """Walk an item's ``variants[]`` block (Mechanism A — property variants).

    Each variant becomes ``{variant_id, property_values, price, discounted_price,
    addon_group_ids}`` — addon refs are the variant's own ``available_add_ons``
    (Pizza Hut / California Burrito pattern) falling back to the parent's
    refs when the variant entry doesn't list its own (defensive — empirically
    Zomato puts them on the variant when both are present).
    """
    if not isinstance(raw_variants, list):
        return []
    out: list[dict[str, Any]] = []
    fallback = list(fallback_addons or [])
    for v in raw_variants:
        if not isinstance(v, dict):
            continue
        vid = str(v.get("variant_id") or v.get("variantId") or "").strip()
        if not vid:
            continue
        v_addons_raw = v.get("available_add_ons")
        v_addons = (
            [str(x).strip() for x in v_addons_raw if isinstance(x, str) and str(x).strip()]
            if isinstance(v_addons_raw, list)
            else list(fallback)
        )
        out.append(
            {
                "variant_id": vid,
                "property_values": v.get("property_values") if isinstance(v.get("property_values"), dict) else {},
                "price": v.get("price"),
                "discounted_price": v.get("discounted_price"),
                "addon_group_ids": v_addons,
            }
        )
    return out


def _collect_menu_index_from_envelope(
    envelope: dict[str, Any],
    *,
    addon_groups_table: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return ``{variant_id -> enriched_entry}`` from a menu envelope.

    Walks both shapes Zomato uses:
      * ``get_menu_items_listing`` → ``data.item_mappings[]`` (sparse — only
        ``{item_name, categories, variant_id, has_more_variants}``).
      * ``get_restaurant_menu_by_categories`` → ``data.menu_items[]`` (full —
        plus ``variants[]``, ``available_add_ons[]``, ``discounted_price``,
        ``item_id``, and the envelope-root ``all_addons_data[]`` block).

    Each entry's shape covers both Zomato variant mechanisms uniformly:
      ``{variant_id (default), item_name, item_id?, price?, discounted_price?,
        categories?, variants: [...], addon_group_ids: [...],
        addon_groups: [{id, name, min, max, items: [...]}, ...]}``.

    Keyed by ``variant_id`` so the same variant captured by both tools in
    one turn (or across turns) deduplicates cleanly; the richer entry
    (with ``addon_groups`` / nested ``variants[]``) wins on conflict.

    This index is consumed solely by the session_menu echo at end-of-turn —
    the planner LLM reads ``session_menu`` to pick a literal ``variant_id``
    for the next ``create_cart``. The executor itself does no matching.
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return out

    groups_table = addon_groups_table
    if groups_table is None:
        groups_table = _extract_addon_groups_table(envelope)

    def _candidate_rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        inner = result.get("data") if isinstance(result.get("data"), dict) else {}
        ims = inner.get("item_mappings")
        if isinstance(ims, list):
            rows.extend(r for r in ims if isinstance(r, dict))
        mis = inner.get("menu_items")
        if isinstance(mis, list):
            rows.extend(r for r in mis if isinstance(r, dict))
        return rows

    for row in _candidate_rows():
        vid = str(row.get("variant_id") or row.get("variantId") or "").strip()
        if not vid:
            continue
        name = (
            row.get("item_name")
            or row.get("itemName")
            or row.get("name")
            or row.get("title")
        )
        if not isinstance(name, str):
            continue

        parent_addon_raw = row.get("available_add_ons")
        parent_addons = (
            [str(x).strip() for x in parent_addon_raw if isinstance(x, str) and str(x).strip()]
            if isinstance(parent_addon_raw, list)
            else []
        )
        variants_list = _normalise_variant_entries(row.get("variants"), parent_addons)

        addon_id_union: list[str] = []
        seen_mg: set[str] = set()
        for src in (parent_addons, *(v["addon_group_ids"] for v in variants_list)):
            for mg in src:
                if mg and mg not in seen_mg:
                    seen_mg.add(mg)
                    addon_id_union.append(mg)
        expanded_groups: list[dict[str, Any]] = []
        for mg in addon_id_union:
            g = groups_table.get(mg)
            if g:
                expanded_groups.append(g)

        entry: dict[str, Any] = {
            "variant_id": vid,
            "item_name": name.strip(),
            "price": row.get("price"),
            "discounted_price": row.get("discounted_price"),
            "categories": row.get("categories"),
            "variants": variants_list,
            "addon_group_ids": addon_id_union,
            "addon_groups": expanded_groups,
        }
        item_id = row.get("item_id") or row.get("itemId")
        if isinstance(item_id, str) and item_id.strip():
            entry["item_id"] = item_id.strip()

        prev = out.get(vid)
        if prev is None:
            out[vid] = entry
            continue
        out[vid] = _pick_richer_menu_entry(prev, entry)
    return out


# Max featured items we cache per restaurant from a keyword-search envelope.
# Zomato returns up to ~10 ``items[]`` per restaurant on a keyword hit;
# capping at 3 keeps the per-turn session_menu growth bounded (8
# restaurants × 3 = 24 rows from one search, which leaves ~11 slots in the
# 35-row LRU for a follow-up menu fetch) so a single "find burger options"
# turn doesn't evict the genuinely useful per-restaurant menu fetches from
# earlier turns. The chat-side restaurant_cards UI mirrors the same cap so
# what the planner has cached === what the user can see.
_KEYWORD_ITEMS_PER_RESTAURANT = max(
    1, int(os.getenv("ZOMATO_KEYWORD_ITEMS_PER_RESTAURANT", "3"))
)


def _collect_menu_index_from_keyword_envelope(
    envelope: dict[str, Any],
    *,
    per_restaurant_cap: int = _KEYWORD_ITEMS_PER_RESTAURANT,
    restaurants_cap: int = _MAX_REMEMBERED_RESTAURANTS,
) -> dict[str, dict[str, Any]]:
    """Extract featured per-restaurant items from a ``get_restaurants_for_keyword`` envelope.

    Zomato's keyword search response shape (verified against the real MCP,
    samples saved in ``outputs/get_restaurants_for_keyword_*.json``):

        data.results[].{res_id, name, rating, ..., items: [{
            variant_id, catalogue_id, name, price, min_price, image_link,
            is_veg, item_customizations, dish_selected_attr,
        }, ...]}

    Each ``results[].items[]`` row is a real addable dish with a real
    ``variant_id`` scoped to that ``res_id``. By stamping the parent
    restaurant's ``res_id`` directly onto each entry HERE (before the
    entries land in ``menu_index``) we sidestep the global ``current_rid``
    stamping at end-of-turn — which only knows about ``__RESTAURANT_ID__``
    (= the first hit) and would otherwise mis-tag every featured item as
    belonging to restaurant #1.

    Returns ``{variant_id -> entry}`` ready to feed into
    ``_merge_menu_index``. Entries are intentionally low-richness
    (no ``addon_groups`` / property ``variants[]``) — the keyword envelope
    doesn't ship that schema. ``_pick_richer_menu_entry`` preserves any
    later by-categories enrichment for the same variant_id.

    Only the first ``restaurants_cap`` restaurants in the response are
    walked — this matches the chat UI's restaurant_cards cap so the
    "what user sees === what session_menu has" contract holds. Without
    this cap, session_menu would carry items from restaurants the user
    never saw, breaking the planner's session_menu-driven add flow.
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return out
    data = envelope.get("data")
    candidates: list[Any] = []
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            candidates = data["results"]
        elif isinstance(data.get("data"), dict) and isinstance(
            data["data"].get("results"), list
        ):
            candidates = data["data"]["results"]
    seen_restaurants = 0
    for resto in candidates:
        if not isinstance(resto, dict):
            continue
        rid_raw = resto.get("res_id") or resto.get("restaurant_id")
        if rid_raw is None:
            continue
        rid = str(rid_raw).strip()
        if not rid:
            continue
        seen_restaurants += 1
        if seen_restaurants > restaurants_cap:
            break
        items = resto.get("items")
        if not isinstance(items, list):
            continue
        kept = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            vid = str(it.get("variant_id") or "").strip()
            if not vid:
                continue
            name = it.get("name") or it.get("item_name")
            if not isinstance(name, str) or not name.strip():
                continue
            if vid in out:
                continue
            entry: dict[str, Any] = {
                "variant_id": vid,
                "item_name": name.strip(),
                "res_id": rid,
                "price": it.get("price"),
                "categories": None,
                "variants": [],
                "addon_group_ids": [],
                "addon_groups": [],
            }
            item_id = it.get("catalogue_id") or it.get("item_id")
            if isinstance(item_id, str) and item_id.strip():
                entry["item_id"] = item_id.strip()
            out[vid] = entry
            kept += 1
            if kept >= per_restaurant_cap:
                break
    return out


def _menu_entry_richness(entry: dict[str, Any]) -> tuple[int, int]:
    """Score how much customization metadata an entry carries.

    A rich entry comes from ``get_restaurant_menu_by_categories`` (the only
    path that surfaces Mechanism B — addon groups — and full property
    variants). A sparse entry comes from ``get_menu_items_listing``. Used
    to break ties when the same item is captured by both tools in one turn
    OR by tools across multiple turns (cross-turn carry-forward).
    """
    return (
        len(entry.get("addon_groups") or []),
        len(entry.get("variants") or []),
    )


def _pick_richer_menu_entry(
    prev: dict[str, Any], new: dict[str, Any]
) -> dict[str, Any]:
    """Conflict resolution between two entries with the same normalized name.

    Priority order, most→least specific:
      1. Richer metadata wins. Never downgrade an addon/variant-aware entry
         to a sparse listing-tool row — a later ``get_menu_items_listing``
         call must NOT wipe the customization schema we already captured
         from ``get_restaurant_menu_by_categories``.
      2. Shorter canonical name wins (legacy: "Veg Nikku Singh Burger"
         beats "Veg Nikku Singh Burger Combo").
      3. If still tied, the newer entry wins (so price / discount refreshes
         on a follow-up turn while keeping the addon schema intact).
    """
    prev_rich = _menu_entry_richness(prev)
    new_rich = _menu_entry_richness(new)
    if new_rich > prev_rich:
        merged = dict(prev)
        merged.update(new)
        return merged
    if new_rich < prev_rich:
        # Keep prev's addon/variant schema but refresh the cheap scalar
        # fields (price/discounted_price) from the newer envelope.
        merged = dict(prev)
        for k in ("price", "discounted_price", "categories"):
            if new.get(k) is not None:
                merged[k] = new[k]
        return merged
    # Equal richness — prefer the shorter canonical name; ties go to new.
    prev_len = len((prev.get("item_name") or ""))
    new_len = len((new.get("item_name") or ""))
    if new_len and (not prev_len or new_len < prev_len):
        return new
    merged = dict(prev)
    for k in ("price", "discounted_price", "categories"):
        if new.get(k) is not None:
            merged[k] = new[k]
    return merged


def _merge_menu_index(
    existing: dict[str, dict[str, Any]],
    new_entries: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Idempotent merge that respects entry richness.

    Use this instead of ``dict.update`` when folding ``_collect_menu_index_from_envelope``
    output into the running per-session ``menu_index`` — otherwise a sparse
    listing-tool call running AFTER a rich by-categories call in the same
    plan would silently strip ``addon_groups`` / ``variants`` from the entry
    the next-turn planner needs to pick the right child variant_id.
    """
    out = dict(existing)
    for key, entry in new_entries.items():
        prev = out.get(key)
        if prev is None:
            out[key] = entry
        else:
            out[key] = _pick_richer_menu_entry(prev, entry)
    return out


# ---------------------------------------------------------------------------
# create_cart row scrub
# ---------------------------------------------------------------------------
# The executor does NOT match items, validate variant_ids, or resolve property
# variants. The planner LLM (which has the full conversation + session_menu
# context) is fully responsible for picking the correct child ``variant_id``
# from ``entry.variants[]`` and emitting one ``items[]`` row per cart line.
#
# The ONLY transformation here is defensive: Zomato's ``create_cart`` endpoint
# has a non-deterministic server-side bug (``ModifierGroupMetaMissing``) that
# fires when the request enumerates explicit modifier picks for certain
# variants — empirically 0/5 success on Subway's Paneer Tikka Sandwich 15cm
# vs. 10/10 with ``add_ons: []``. We therefore force ``add_ons: []`` on every
# row (regardless of what the planner emitted) and let Zomato auto-fill the
# required leaves server-side. The cart response carries Zomato's picks back
# as ``cart.items[].add_ons[]`` so the assistant can surface them verbatim.
# ---------------------------------------------------------------------------


_VARIANT_ID_RE = re.compile(r"^(?:v_\d+|\d+)$")


def _is_valid_zomato_variant_id(value: Any) -> bool:
    """A Zomato variant_id is either ``v_<digits>`` or a bare ``<digits>``.

    Rejects empty strings, ``__SOMETHING__`` placeholders that slipped past
    substitution, and ``ctl_<digits>`` item_ids (a common planner mix-up
    since item_id and variant_id sit side-by-side on every menu row).
    """
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s:
        return False
    return bool(_VARIANT_ID_RE.match(s))


def _scrub_cart_items_for_zomato(
    args: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Force ``add_ons: []`` on every ``items[]`` row, drop rows with bogus variant_id.

    Returns ``(scrubbed_args, dropped_rows)`` — ``dropped_rows`` captures
    the rows we refused to send so the caller can stamp a diagnostic and
    the response LLM can tell the user honestly *which* item failed.

    Why drop on the client side at all? The planner LLM occasionally
    confuses ``item_id`` (``ctl_…``) for ``variant_id`` (``v_…``) or sneaks
    a leftover ``__MENU_ITEM_ID__`` placeholder string into the row. Either
    of those reaches Zomato as a string the server can't resolve and the
    whole cart create fails with "variant id required". Catching the mix-up
    here lets the rest of the (correct) rows go through; if every row was
    bad we let an empty ``items`` go through and Zomato's own error
    surfaces to the response LLM as the failure reason.
    """
    items = args.get("items") if isinstance(args.get("items"), list) else None
    if not items:
        return args, []
    out: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["add_ons"] = []
        if not _is_valid_zomato_variant_id(row.get("variant_id")):
            dropped.append(
                {
                    "variant_id": row.get("variant_id"),
                    "quantity": row.get("quantity"),
                    "reason": "invalid_variant_id",
                }
            )
            continue
        out.append(row)
    args["items"] = out
    return args, dropped


# ---------------------------------------------------------------------------
# menu_filter sanitisation
# ---------------------------------------------------------------------------
# Zomato's tool *description* claims `menu_filter` supports `flash_sale` and
# rating filters, but the actual JSON schema declares ONLY `min_price` and
# `max_price`, and the server silently drops everything else (no error, no
# filtering). On top of that, `min_price=0` / `max_price=0` are treated as
# "no filter applied", so passing 0 to express "free items" yields the full
# (unfiltered) menu — which is the opposite of what the user asked for.
#
# This sanitiser is purely defensive: it lets the LLM be sloppy without us
# silently shipping a meaningless filter. Empirically verified semantics
# (see RCA notes in repo):
#   * Only `min_price` and `max_price` are honoured.
#   * Both endpoints are inclusive.
#   * The filter compares against `discounted_price` when present, else
#     `price` — i.e. the *effective* price the user actually pays. This
#     guidance lives in the system prompt; here we only enforce shape.
# ---------------------------------------------------------------------------
_MENU_FILTER_SUPPORTED_FIELDS = frozenset({"min_price", "max_price"})


def _sanitize_menu_filter(
    args: dict[str, Any],
    diagnostics: dict[str, Any],
    *,
    tool: str,
) -> dict[str, Any]:
    """Normalise ``args.menu_filter`` in place and return ``args``.

    Drops fields not in ``_MENU_FILTER_SUPPORTED_FIELDS``, drops 0-valued
    min/max (Zomato treats 0 as "skip filter"), and removes the
    ``menu_filter`` key entirely if nothing usable remains. Logs anything it
    had to strip under ``diagnostics["menu_filter_sanitisation"]`` so a tool
    misuse is observable downstream.
    """
    raw = args.get("menu_filter") if isinstance(args, dict) else None
    if raw is None:
        return args
    if not isinstance(raw, dict):
        diagnostics.setdefault("menu_filter_sanitisation", []).append(
            {"tool": tool, "reason": "non_dict", "value_type": type(raw).__name__}
        )
        args.pop("menu_filter", None)
        return args

    cleaned: dict[str, Any] = {}
    dropped: dict[str, Any] = {}
    for key, val in raw.items():
        if key not in _MENU_FILTER_SUPPORTED_FIELDS:
            dropped[key] = val
            continue
        if val is None:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            dropped[key] = val
            continue
        if num <= 0:
            # 0 (and negatives) are sentinels for "no filter applied" on Zomato.
            # Drop them so the LLM saying "max_price=0 = free items" doesn't
            # accidentally request the full menu.
            dropped[key] = val
            continue
        cleaned[key] = num

    if "min_price" in cleaned and "max_price" in cleaned and cleaned["min_price"] > cleaned["max_price"]:
        # Inverted range: drop both rather than silently swap; the LLM should
        # see the result and recover.
        dropped["range_inverted"] = (cleaned["min_price"], cleaned["max_price"])
        cleaned.pop("min_price", None)
        cleaned.pop("max_price", None)

    if cleaned:
        args["menu_filter"] = cleaned
    else:
        args.pop("menu_filter", None)

    if dropped:
        diagnostics.setdefault("menu_filter_sanitisation", []).append(
            {"tool": tool, "dropped": dropped, "kept": cleaned}
        )

    return args


def _truncate_menu_envelope_for_response(
    envelope: dict[str, Any],
    *,
    max_items: int = _MENU_ITEMS_PER_RESTAURANT,
) -> dict[str, Any] | None:
    """Trim a menu-tool envelope in-place so the response LLM sees at most N rows.

    Mutates the envelope (which is the same object already stored in
    ``step_results`` — so the trim propagates to the response builder without
    a copy). The caller MUST run this BEFORE building ``menu_index`` from the
    envelope so the cache and the user-visible slice stay identical (planner
    can never resolve an item the user couldn't see, and vice versa).

    Two shapes are handled:
      * ``get_menu_items_listing``       → ``data.result.data.item_mappings[]``
      * ``get_restaurant_menu_by_categories`` → ``data.result.data.menu_items[]``

    Adds breadcrumb metadata under ``data.result.data._menu_truncation`` so the
    response LLM (and any debug consumer) can mention "showing top N of M" if
    helpful. Returns the breadcrumb dict if any trimming happened, else None.
    """
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
    if not data:
        return None
    result = data.get("result") if isinstance(data.get("result"), dict) else None
    if not result:
        return None
    inner = result.get("data") if isinstance(result.get("data"), dict) else None
    if not inner:
        return None

    breadcrumb: dict[str, Any] = {}
    for key in ("item_mappings", "menu_items"):
        items = inner.get(key)
        if not isinstance(items, list):
            continue
        original = len(items)
        if original <= max_items:
            continue
        inner[key] = items[:max_items]
        breadcrumb[key] = {"shown": max_items, "total": original}

    if breadcrumb:
        existing = inner.get("_menu_truncation")
        if isinstance(existing, dict):
            existing.update(breadcrumb)
        else:
            inner["_menu_truncation"] = breadcrumb
        return breadcrumb
    return None


async def _enrich_menu_listing_with_category_prices(
    session: Any,
    listing_envelope: dict[str, Any],
    *,
    res_id: Any,
    address_id: Any,
) -> dict[str, Any] | None:
    """
    get_menu_items_listing does not include rupee prices (Zomato MCP design).
    Fetch get_restaurant_menu_by_categories for deduped category names from the listing
    and merge price + discounted_price onto each item_mapping row by variant_id.

    Returns the **raw by-categories envelope** (or ``None`` if the fetch was
    skipped / failed) so the caller can also extract the rich customization
    schema (``variants[]`` + ``all_addons_data[]``) into the running
    ``menu_index``. Without that, the listing tool alone surfaces only
    sparse rows and the planner LLM can't see the child variant_ids /
    addon group schema needed to pick the right cart row next turn.
    """
    if listing_envelope.get("is_error"):
        return None
    ims, inner = _listing_item_mappings_blob(listing_envelope)
    if not ims or inner is None:
        return None
    rid = res_id if res_id is not None else inner.get("res_id")
    aid = address_id if address_id is not None else inner.get("address_id")
    if rid is None or aid is None or (isinstance(aid, str) and not str(aid).strip()):
        return None
    cats: list[str] = []
    seen: set[str] = set()
    for it in ims:
        for c in it.get("categories") or []:
            if not isinstance(c, str):
                continue
            s = c.strip()
            if not s or s in seen:
                continue
            seen.add(s)
            cats.append(s)
            if len(cats) >= _MAX_MENU_CATEGORY_ENRICH:
                break
        if len(cats) >= _MAX_MENU_CATEGORY_ENRICH:
            break
    if not cats:
        return None
    try:
        cat_args: dict[str, Any] = {
            "res_id": int(rid) if str(rid).isdigit() else rid,
            "address_id": str(aid).strip(),
            "categories": cats,
        }
        r = await asyncio.wait_for(
            session.call_tool("get_restaurant_menu_by_categories", arguments=cat_args),
            timeout=_MCP_TOOL_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.info("menu price enrichment: category fetch failed: %s", e)
        return None
    cat_env = _tool_result_payload(r)
    if cat_env.get("is_error"):
        return cat_env
    vmap = _variant_price_map_from_category_menu(cat_env)
    merged = inner.get("item_mappings") if vmap else None
    if vmap and isinstance(merged, list):
        for row in merged:
            if not isinstance(row, dict):
                continue
            vid = str(row.get("variant_id") or "").strip()
            if not vid or vid not in vmap:
                continue
            pr = vmap[vid]
            if pr.get("price") is not None:
                row["price"] = pr["price"]
            if pr.get("discounted_price") is not None:
                row["discounted_price"] = pr["discounted_price"]
    # Return the by-categories envelope unconditionally on success so the
    # caller can extract ``variants[]`` + ``all_addons_data[]`` into the
    # running ``menu_index``. Even when no price merge happened (vmap empty),
    # the envelope can still contain the customization schema we need.
    return cat_env


async def _run_session_inner(
    tool_plan: list[dict[str, Any]],
    effective_address_id: str | None,
    repo_root: Path,
    mcp_url: str,
    node_binary: str,
    user_id: str | None = None,
    location_text: str | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
    session_addresses: list[dict[str, Any]] | None = None,
    session_menu: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as e:
        raise RuntimeError(
            "Python MCP SDK is not installed for this interpreter. Use Python 3.10+ "
            "and install requirements.txt, or run the Lambda Python 3.12 container."
        ) from e

    step_results: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "requested_address_id": effective_address_id,
        "requested_location_text": location_text,
    }

    proxy_script = repo_root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        raise RuntimeError(
            "mcp-remote is not installed — run npm install at the repo root."
        )

    # Cold Lambda containers ship with an empty /tmp/.mcp-auth — without this
    # call mcp-remote starts its own OAuth handshake and session.initialize()
    # times out. Idempotent on warm containers.
    ensure_mcp_tokens_on_disk(repo_root, mcp_url=mcp_url, user_id=user_id)

    params = StdioServerParameters(
        command=node_binary,
        args=[str(proxy_script), mcp_url],
        cwd=str(repo_root),
        env=_stdio_env(repo_root, user_id=user_id),
    )

    resolved_id = (effective_address_id or "").strip() or None
    # Seed placeholder_values with any IDs persisted from a previous turn
    # (cart_id, restaurant_id, order_id, etc. — every alias in
    # ``_PLACEHOLDER_ALIASES`` is eligible). This is what makes a multi-turn
    # flow possible: the executor remembers what create_cart returned in turn
    # N so it can resolve __CART_ID__ for checkout_cart in turn N+1. The
    # current-turn address_id (passed in directly) always wins over a stale
    # one in the seed.
    placeholder_values: dict[str, str] = {}
    if isinstance(session_placeholders, dict):
        for k, v in session_placeholders.items():
            if isinstance(k, str) and k.startswith("__") and k.endswith("__"):
                if isinstance(v, (str, int)) and str(v).strip():
                    placeholder_values[k] = str(v).strip()
    # Where did the active __ADDRESS_ID__ come from? Used by the response
    # LLM to decide whether to nudge the user about changing addresses
    # ("auto" / "session_addresses" → mention it; "request" → assume the
    # user just picked it, stay quiet).
    address_source: str | None = None
    if resolved_id:
        placeholder_values["__ADDRESS_ID__"] = resolved_id
        address_source = "request"
    elif placeholder_values.get("__ADDRESS_ID__"):
        resolved_id = placeholder_values["__ADDRESS_ID__"]
        address_source = "session_state"
    # Carry-forward saved-address list (the LRU of {address_id, label, …}
    # rows the frontend echoes back across turns). If we don't already have
    # an address from the request or from session_state, eagerly pick the
    # first entry — this is what makes the chat work without the user ever
    # clicking "Fetch addresses" in the UI: turn 1 fetches the list (via
    # ``prefetch_addresses`` below), turns 2+ just reuse what was learned.
    seed_addresses: list[dict[str, Any]] = []
    if isinstance(session_addresses, list):
        for entry in session_addresses:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("address_id") or entry.get("addressId") or entry.get("id")
            if aid is None:
                continue
            sid = str(aid).strip()
            if not sid:
                continue
            seed_addresses.append({**entry, "address_id": sid})
    if not resolved_id and seed_addresses:
        resolved_id = seed_addresses[0]["address_id"]
        placeholder_values["__ADDRESS_ID__"] = resolved_id
        address_source = "session_addresses"
    if placeholder_values:
        diagnostics["seeded_placeholders"] = sorted(placeholder_values.keys())
    # Surface the seed list immediately so the pipeline can echo it back
    # even on a turn where no address-fetching tool runs (e.g. the user
    # asks a general follow-up like "show me other options").
    if seed_addresses:
        diagnostics["session_addresses"] = list(seed_addresses)
    prefetch_done = False
    # Per-session menu memory: populated after each menu tool, surfaced at
    # end-of-turn as ``diagnostics["session_menu"]`` so the next-turn planner
    # LLM can pick a literal ``variant_id`` for "add the chicken keto salad"
    # without re-running a menu tool. The executor does no matching against
    # this index — it is purely a carry-forward catalogue for the planner.
    # Every menu tool in this turn merges its own fresh entries on top via
    # ``_merge_menu_index``.
    menu_index: dict[str, dict[str, Any]] = _hydrate_menu_index_from_session(session_menu)
    # Track which res_id the carried-forward seed was tied to (when known)
    # so we don't accidentally cross-contaminate items across restaurants
    # in the session_menu echo back. Per-item ``res_id`` always wins; this
    # fallback only kicks in for legacy entries without a stamp.
    seed_session_menu: list[dict[str, Any]] = []
    if isinstance(session_menu, list):
        for entry in session_menu:
            if isinstance(entry, dict):
                seed_session_menu.append(entry)
    # Only items added/refreshed by an actual menu envelope THIS turn are
    # treated as "fresh" for LRU recency. Without this set, menu_index would
    # also contain the hydrated-from-seed entries (in their original insertion
    # order, i.e. oldest first) — pushing the previous turn's items to the
    # front of session_menu and burying genuinely new items past the cap.
    fresh_menu_keys: set[str] = set()
    # Per-session restaurant memory: every (res_id, name) pair we've ever
    # shown the user, with the most-recent search at the front. Seeded
    # from the previous turn's session_state, refreshed by each
    # ``get_restaurants_for_keyword`` response, and echoed back through
    # diagnostics so the pipeline can persist it for the next turn.
    recent_restaurants: list[dict[str, Any]] = []
    if isinstance(session_restaurants, list):
        for entry in session_restaurants:
            if not isinstance(entry, dict):
                continue
            rid = entry.get("res_id")
            name = entry.get("name")
            if not isinstance(rid, str) or not rid.strip():
                continue
            if not isinstance(name, str) or not name.strip():
                continue
            recent_restaurants.append({**entry, "res_id": rid.strip(), "name": name.strip()})

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(
                session.initialize(), timeout=_MCP_INIT_TIMEOUT_SECONDS
            )

            step_index = 0

            async def prefetch_addresses() -> None:
                nonlocal resolved_id, prefetch_done, step_index, address_source
                if prefetch_done:
                    return
                prefetch_done = True
                r = await asyncio.wait_for(
                    session.call_tool("get_saved_addresses_for_user", arguments={}),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
                )
                envelope = _tool_result_payload(r)
                step_results.append(
                    {
                        "tool": "get_saved_addresses_for_user(auto)",
                        "arguments": {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1
                nid = _find_best_address_id(envelope, location_text=location_text)
                if nid:
                    resolved_id = nid
                    placeholder_values["__ADDRESS_ID__"] = nid
                    diagnostics["resolved_address_id"] = resolved_id
                    # Only flag as auto-resolved when we didn't already have
                    # an address from the request / session — keep the
                    # original source for telemetry / response-LLM nudging.
                    if address_source is None:
                        address_source = "auto"
                else:
                    diagnostics["auto_address_failure"] = (
                        "Could not extract address_id from Zomato response."
                    )
                # Learn the FULL list of saved addresses so future turns can
                # skip this MCP call entirely (frontend echoes back
                # ``session_addresses``). Merge with any seed so we don't
                # lose entries on a partial response.
                fresh_rows = _extract_address_rows_from_envelope(envelope)
                if fresh_rows:
                    existing_ids = {row["address_id"] for row in seed_addresses}
                    merged = list(seed_addresses)
                    for row in fresh_rows:
                        if row["address_id"] not in existing_ids:
                            merged.append(row)
                            existing_ids.add(row["address_id"])
                    # Replace; the fresh list is authoritative (covers
                    # newly-added or removed addresses on the user's account).
                    diagnostics["session_addresses"] = fresh_rows if not seed_addresses else merged
                _learn_placeholders_from_payload(placeholder_values, envelope)
                diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())

            for raw in tool_plan:
                if not isinstance(raw, dict):
                    continue
                tool = (raw.get("tool") or "").strip()
                if not tool:
                    continue
                if tool == "get_saved_addresses_for_user":
                    prefetch_done = True
                base_args = copy.deepcopy(raw.get("arguments") or {})

                exec_args = substitute_placeholders(copy.deepcopy(base_args), placeholder_values)

                if tool != "get_saved_addresses_for_user" and _mentions_placeholder(
                    exec_args
                ):
                    unresolved = _unresolved_placeholders(exec_args)
                    if "__ADDRESS_ID__" in unresolved:
                        await prefetch_addresses()
                        exec_args = substitute_placeholders(
                            copy.deepcopy(base_args), placeholder_values
                        )

                # Address-switching: when the planner emits a *literal*
                # address_id that differs from the carried __ADDRESS_ID__,
                # treat it as the user changing addresses ("use my work
                # address") and update the placeholder so subsequent turns
                # carry the new one. Without this, the tool runs against
                # the new address (good) but session_state still says the
                # old address (silent revert next turn). See planner prompt
                # rule 1 — "the executor will override the carried
                # __ADDRESS_ID__ for this turn and persist the new one".
                if (
                    isinstance(exec_args, dict)
                    and tool != "get_saved_addresses_for_user"
                ):
                    literal_aid = exec_args.get("address_id") or exec_args.get("addressId")
                    if isinstance(literal_aid, str) and literal_aid.strip():
                        sid = literal_aid.strip()
                        # Skip placeholder strings — those went through
                        # substitution above. Only react to actual numeric
                        # / opaque ids the planner pasted in.
                        if not (sid.startswith("__") and sid.endswith("__")):
                            current = placeholder_values.get("__ADDRESS_ID__")
                            if current != sid:
                                placeholder_values["__ADDRESS_ID__"] = sid
                                resolved_id = sid
                                address_source = "switched"
                                diagnostics["resolved_address_id"] = sid
                                trace(
                                    "executor.zomato.address_switch",
                                    tool=tool,
                                    from_id=current,
                                    to_id=sid,
                                )

                if _mentions_placeholder(exec_args):
                    unresolved = _unresolved_placeholders(exec_args)
                    diagnostics.setdefault("skipped_tools", []).append(
                        {
                            "tool": tool,
                            "reason": "placeholder_unresolved",
                            "placeholders": unresolved,
                        }
                    )
                    trace(
                        "executor.zomato.skip",
                        tool=tool,
                        reason="placeholder_unresolved",
                        unresolved=unresolved,
                        known_placeholders=sorted(placeholder_values.keys()),
                    )
                    continue

                if tool == "get_saved_addresses_for_user":
                    prefetch_done = True

                # For create_cart, force ``add_ons: []`` on every row to
                # dodge Zomato's ``ModifierGroupMetaMissing`` server bug AND
                # drop rows whose variant_id is clearly bogus (placeholder
                # leftovers, ``ctl_…`` item_ids the planner confused for
                # variants). Everything else — variant selection, quantity,
                # multi-row carts — is the planner LLM's responsibility.
                if tool == "create_cart":
                    exec_args, _dropped_rows = _scrub_cart_items_for_zomato(
                        exec_args if isinstance(exec_args, dict) else {}
                    )
                    if _dropped_rows:
                        logger.warning(
                            "create_cart: dropped %d row(s) with invalid variant_id: %s",
                            len(_dropped_rows),
                            _dropped_rows,
                        )
                        diagnostics.setdefault("create_cart_dropped_rows", []).extend(
                            _dropped_rows
                        )

                # For menu tools, scrub `menu_filter` so we never send fields
                # the server silently ignores (flash_sale / min_rating) or
                # 0-sentinels (which yield the *full* menu instead of the
                # intended subset). See `_sanitize_menu_filter` for the
                # empirical Zomato semantics.
                if tool in {"get_menu_items_listing", "get_restaurant_menu_by_categories"}:
                    exec_args = _sanitize_menu_filter(
                        exec_args if isinstance(exec_args, dict) else {},
                        diagnostics,
                        tool=tool,
                    )

                # checkout_cart's MCP schema only accepts `cart_id` — passing
                # `payment_type` (an arg that belongs to create_cart) causes a
                # pydantic "unexpected_keyword_argument" rejection from the
                # server. The planner LLM occasionally tries to be helpful and
                # echoes payment_type onto the checkout step; strip it here so
                # a single planner slip doesn't break the whole flow.
                if tool == "checkout_cart" and isinstance(exec_args, dict):
                    _dropped_for_checkout = [
                        k for k in ("payment_type", "paymentType")
                        if k in exec_args
                    ]
                    if _dropped_for_checkout:
                        for k in _dropped_for_checkout:
                            exec_args.pop(k, None)
                        diagnostics.setdefault("scrubbed_args", []).append(
                            {"tool": tool, "dropped": _dropped_for_checkout}
                        )

                # Clear any placeholder this tool is about to *issue* so the
                # next `_learn_placeholders_from_payload` walk re-learns the
                # fresh value from this response. Without this, a stale
                # __CART_ID__ seeded from the previous turn would block a new
                # create_cart's cart_id from being picked up — leading to the
                # next turn's checkout pointing at the stale (now-empty) cart.
                _issued = _TOOL_ISSUES_PLACEHOLDER.get(tool)
                if _issued:
                    for _ph in _issued:
                        placeholder_values.pop(_ph, None)

                trace(
                    "executor.zomato.call",
                    tool=tool,
                    arguments=exec_args or {},
                    known_placeholders=sorted(placeholder_values.keys()),
                )
                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments=exec_args or {}),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
                )
                envelope = _tool_result_payload(result)
                trace(
                    "executor.zomato.result",
                    tool=tool,
                    is_error=bool(envelope.get("is_error")),
                    has_payment=isinstance(envelope.get("payment"), dict),
                    envelope_keys=sorted(envelope.keys()),
                )
                step_results.append(
                    {
                        "tool": tool,
                        "arguments": exec_args or {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1

                enrichment_envelope: dict[str, Any] | None = None
                if tool == "get_menu_items_listing" and not envelope.get("is_error"):
                    try:
                        enrichment_envelope = await _enrich_menu_listing_with_category_prices(
                            session,
                            envelope,
                            res_id=exec_args.get("res_id"),
                            address_id=exec_args.get("address_id"),
                        )
                    except Exception as e:
                        logger.info("menu price enrichment failed: %s", e)

                if tool in {"get_menu_items_listing", "get_restaurant_menu_by_categories"}:
                    # IMPORTANT — trim FIRST, then index. The user-visible
                    # slice and the planner's ``session_menu`` MUST be the
                    # same N items per restaurant; otherwise the response LLM
                    # can promise an item ("Oova Superstar Egg Biryani")
                    # whose variant_id the next-turn planner has no way to
                    # cite, and create_cart silently goes to the wrong item.
                    try:
                        trimmed = _truncate_menu_envelope_for_response(envelope)
                    except Exception as e:
                        logger.info("menu envelope truncation failed: %s", e)
                        trimmed = None
                    if trimmed:
                        diagnostics.setdefault("menu_truncation", {})[tool] = trimmed

                    # Collect from the (now-trimmed) main envelope. When the
                    # listing tool was silently followed by a by-categories
                    # enrichment, fold in addon_groups / variants for the
                    # SAME items only — never expand the visible-N set with
                    # extra rows the user never saw.
                    try:
                        new_entries = _collect_menu_index_from_envelope(envelope)
                    except Exception as e:
                        logger.info("menu index extraction failed: %s", e)
                        new_entries = {}
                    if (
                        new_entries
                        and enrichment_envelope is not None
                        and not enrichment_envelope.get("is_error")
                    ):
                        try:
                            rich_entries = _collect_menu_index_from_envelope(
                                enrichment_envelope
                            )
                        except Exception as e:
                            logger.info("menu index enrichment extraction failed: %s", e)
                            rich_entries = {}
                        if rich_entries:
                            visible_vids = set(new_entries.keys())
                            rich_entries = {
                                vid: row
                                for vid, row in rich_entries.items()
                                if vid in visible_vids
                            }
                            if rich_entries:
                                new_entries = _merge_menu_index(new_entries, rich_entries)
                    if new_entries:
                        # Richness-aware merge into the cross-restaurant LRU.
                        # A sparse listing envelope arriving AFTER a
                        # by-categories envelope must not strip the
                        # addon_groups / variants from the existing entry —
                        # the planner reads that schema off ``session_menu``
                        # to pick the right child variant_id.
                        menu_index = _merge_menu_index(menu_index, new_entries)
                        fresh_menu_keys.update(new_entries.keys())
                        diagnostics["menu_index_size"] = len(menu_index)

                if tool == "get_restaurants_for_keyword" and not envelope.get("is_error"):
                    try:
                        new_restos = _collect_restaurants_from_envelope(envelope)
                    except Exception as e:
                        logger.info("restaurant index extraction failed: %s", e)
                        new_restos = []
                    if new_restos:
                        recent_restaurants = _merge_recent_restaurants(
                            recent_restaurants, new_restos
                        )
                    # Lift the per-restaurant ``items[]`` Zomato pre-curates
                    # into every keyword search result. Each item is a real
                    # addable dish with a real variant_id scoped to its
                    # parent restaurant — stamping them into session_menu
                    # lets the user say "add the Crispy Veg Burger" without
                    # us needing a separate ``get_menu_items_listing`` round
                    # trip first. ``_collect_menu_index_from_keyword_envelope``
                    # already stamps the correct per-restaurant res_id on
                    # each entry so the global ``current_rid`` stamping at
                    # end-of-turn (= first hit only) won't mis-tag them.
                    try:
                        kw_entries = _collect_menu_index_from_keyword_envelope(envelope)
                    except Exception as e:
                        logger.info("keyword menu items extraction failed: %s", e)
                        kw_entries = {}
                    if kw_entries:
                        menu_index = _merge_menu_index(menu_index, kw_entries)
                        fresh_menu_keys.update(kw_entries.keys())
                        diagnostics["menu_index_size"] = len(menu_index)

                if tool == "checkout_cart" and not envelope.get("is_error"):
                    try:
                        payment = _extract_payment_from_checkout(
                            envelope,
                            cart_id=str(exec_args.get("cart_id") or "").strip() or None,
                        )
                    except Exception as e:
                        logger.warning("payment extraction failed: %s", e)
                        payment = None
                    if payment:
                        envelope["payment"] = payment
                        diagnostics["checkout_payment"] = {
                            "qr_paths": payment.get("qr_paths"),
                            "upi_link_present": bool(payment.get("upi_link")),
                            "amount_inr": payment.get("amount_inr"),
                            "transaction_ref": payment.get("transaction_ref"),
                        }
                        trace(
                            "executor.zomato.payment_extracted",
                            has_qr_b64=bool(payment.get("qr_b64")),
                            qr_paths=payment.get("qr_paths"),
                            upi_link_present=bool(payment.get("upi_link")),
                            amount_inr=payment.get("amount_inr"),
                            transaction_ref=payment.get("transaction_ref"),
                        )
                    else:
                        trace(
                            "executor.zomato.payment_missing",
                            envelope_keys=sorted(envelope.keys()),
                            note="checkout_cart did not produce a parseable payment block",
                        )

                if tool == "get_saved_addresses_for_user":
                    if resolved_id is None:
                        nid = _first_address_id_from_saved_payload(envelope)
                        if nid:
                            resolved_id = nid
                            placeholder_values["__ADDRESS_ID__"] = nid
                            diagnostics["resolved_address_id"] = resolved_id
                            if address_source is None:
                                address_source = "auto"
                    # Refresh the saved-address index from the explicit call
                    # too, so the next turn can carry the fresh list and the
                    # response LLM has labels to nudge with.
                    try:
                        fresh_rows = _extract_address_rows_from_envelope(envelope)
                    except Exception as e:
                        logger.info("address index extraction failed: %s", e)
                        fresh_rows = []
                    if fresh_rows:
                        diagnostics["session_addresses"] = fresh_rows
                _learn_placeholders_from_payload(placeholder_values, envelope)
                diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())

    diagnostics["resolved_address_id"] = resolved_id
    diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())
    if address_source:
        # ``request | session_state | session_addresses | auto`` — lets the
        # response LLM phrase its nudge correctly ("using your home address —
        # change kar dena ho toh batao" vs "okay, switched to your work
        # address") without us having to thread the same boolean through
        # multiple layers.
        diagnostics["address_source"] = address_source
    # Surface the *values* — not just the keys — so the pipeline can hand them
    # back to the frontend as ``session_state`` and seed the next turn. This
    # is the bridge that lets a Zomato cart created in turn N be checked out
    # in turn N+1 without the LLM ever having to see the raw cart_id.
    diagnostics["session_placeholders"] = dict(placeholder_values)
    # Echo the multi-restaurant memory so the pipeline can persist it. The
    # planner uses this to skip a re-search when the user picks a restaurant
    # we've already shown them — Zomato's fuzzy keyword search is unreliable
    # for short brand names (e.g. "BOX8" → returns Domino's), so reusing the
    # known res_id is strictly better than re-querying.
    if recent_restaurants:
        diagnostics["session_restaurants"] = list(recent_restaurants)

    # Echo the per-item LRU cache. Only items added/refreshed by an actual
    # menu envelope this turn count as "fresh" (they go to the front of the
    # LRU) — hydrated seed entries already live in ``seed_session_menu`` and
    # the merge below will keep them in their existing order behind the new
    # arrivals. Without this split, hydrated entries (insertion order: oldest
    # first) would shadow truly new fetches and bury them past the 15-item
    # cap.
    #
    # Each fresh entry is stamped with the *current* ``__RESTAURANT_ID__``
    # so the response builder and next-turn planner know which restaurant
    # those items belong to.
    current_rid = (placeholder_values.get("__RESTAURANT_ID__") or "").strip() or None
    fresh_menu_entries: list[dict[str, Any]] = []
    for key in fresh_menu_keys:
        entry = menu_index.get(key)
        if not isinstance(entry, dict):
            continue
        # NOTE: don't name this ``copy`` — it would shadow the module-level
        # ``import copy`` at the top of the file and turn every later
        # ``copy.deepcopy(...)`` in this function into an UnboundLocalError.
        snapshot = dict(entry)
        if current_rid and not snapshot.get("res_id"):
            snapshot["res_id"] = current_rid
        fresh_menu_entries.append(snapshot)
    if fresh_menu_entries or seed_session_menu:
        diagnostics["session_menu"] = _merge_session_menu(
            seed_session_menu, fresh_menu_entries
        )
    return step_results, diagnostics


# ---------------------------------------------------------------------------
# Token persistence (Lambda is ephemeral — store in Secrets Manager)
# ---------------------------------------------------------------------------


def _root_mcp_token_secret() -> dict[str, Any]:
    try:
        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=_TOKEN_SECRET_ID)
        raw = resp.get("SecretString") or "{}"
        return json.loads(raw)
    except Exception:
        return {}


def _mcp_token_secret(user_id: str | None = None):
    """Return MCP token payload, scoped to user_id when supplied."""
    payload = _root_mcp_token_secret()
    if not user_id:
        return payload
    safe_user = _safe_user_id(user_id)
    if not safe_user:
        return {}
    users = payload.get("users") if isinstance(payload, dict) else None
    if not isinstance(users, dict):
        users = {}
    user_payload = users.get(safe_user)
    if isinstance(user_payload, dict):
        return user_payload
    if safe_user == _LEGACY_MCP_OWNER and isinstance(payload, dict):
        legacy = {
            key: payload[key]
            for key in ("files", "custom_providers", "pairing_sessions", "oauth_pending")
            if key in payload
        }
        return legacy
    return {}


def _write_mcp_token_secret(payload: dict[str, Any]) -> None:
    client = boto3.client("secretsmanager")
    raw = json.dumps(payload)
    try:
        client.put_secret_value(SecretId=_TOKEN_SECRET_ID, SecretString=raw)
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(
            Name=_TOKEN_SECRET_ID,
            SecretString=raw,
            Description="Zomato MCP OAuth tokens for serverless Companion",
        )


def _merge_mcp_token_secret(user_id: str | None = None, **updates: Any) -> dict[str, Any]:
    if user_id:
        payload = _root_mcp_token_secret()
        if not isinstance(payload, dict):
            payload = {}
        users = payload.get("users")
        if not isinstance(users, dict):
            users = {}
        safe_user = _safe_user_id(user_id) or user_id
        current = users.get(safe_user)
        if not isinstance(current, dict):
            current = _mcp_token_secret(user_id)
            if not isinstance(current, dict):
                current = {}
        current.update(updates)
        current["updated_at"] = utc_now_iso()
        users[safe_user] = current
        payload["users"] = users
        payload["updated_at"] = utc_now_iso()
        _write_mcp_token_secret(payload)
        return current

    payload = _mcp_token_secret()
    if not isinstance(payload, dict):
        payload = {}
    payload.update(updates)
    payload["updated_at"] = utc_now_iso()
    _write_mcp_token_secret(payload)
    return payload


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
    for url in _well_known_candidates(auth_server_url, "oauth-authorization-server"):
        authorization_server = _json_get(url)
        if authorization_server:
            break
    if authorization_server is None:
        for url in _well_known_candidates(auth_server_url, "openid-configuration"):
            authorization_server = _json_get(url)
            if authorization_server:
                break

    if not authorization_server:
        raise RuntimeError("Could not discover Zomato OAuth metadata from the MCP server.")
    if not authorization_server.get("authorization_endpoint"):
        raise RuntimeError("Zomato OAuth metadata did not include an authorization endpoint.")
    if not authorization_server.get("token_endpoint"):
        raise RuntimeError("Zomato OAuth metadata did not include a token endpoint.")

    return {
        "authorization_server_url": auth_server_url,
        "protected_resource": protected_resource or {},
        "authorization_server": authorization_server,
    }


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
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    callback_url: str,
    user_id: str | None = None,
    provider_label: str | None = None,
) -> dict[str, Any]:
    """Start a hosted OAuth flow and return the provider authorization URL."""
    root = repo_root or repo_root_from_settings()
    url = mcp_url or os.getenv("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    metadata = _discover_oauth_metadata(url)
    auth_server = metadata["authorization_server"]
    registration_endpoint = auth_server.get("registration_endpoint")
    if not registration_endpoint:
        raise RuntimeError(
            "Zomato OAuth does not advertise dynamic client registration. "
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
        "software_version": _mcp_remote_version(root) or "0.1.38",
        "scope": scope,
    }
    reg_resp = requests.post(registration_endpoint, json=client_metadata, timeout=12)
    if not reg_resp.ok:
        raise RuntimeError(
            f"Zomato OAuth client registration failed with HTTP {reg_resp.status_code}: {reg_resp.text[:300]}"
        )
    client_info = reg_resp.json()
    if not isinstance(client_info, dict) or not client_info.get("client_id"):
        raise RuntimeError("Zomato OAuth client registration did not return a client_id.")

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
    secret_payload = _mcp_token_secret(user_id)
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
    _merge_mcp_token_secret(user_id, oauth_pending=pending_map)

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
        "detail": f"Continue to {provider_label or 'the provider'} and approve access. Companion will save the MCP session automatically.",
        "mcp_url": url,
    }


def _find_oauth_pending_session(state: str) -> tuple[str | None, dict[str, Any], dict[str, Any], dict[str, Any]]:
    secret_payload = _mcp_token_secret()
    pending_map = secret_payload.get("oauth_pending") if isinstance(secret_payload, dict) else None
    if isinstance(pending_map, dict):
        pending = pending_map.get(state)
        if isinstance(pending, dict):
            return None, secret_payload, pending_map, pending

    root = _root_mcp_token_secret()
    users = root.get("users") if isinstance(root, dict) else None
    if isinstance(users, dict):
        for user_id in users:
            user_payload = _mcp_token_secret(str(user_id))
            user_pending_map = user_payload.get("oauth_pending") if isinstance(user_payload, dict) else None
            if not isinstance(user_pending_map, dict):
                continue
            pending = user_pending_map.get(state)
            if isinstance(pending, dict):
                return str(user_id), user_payload, user_pending_map, pending

    raise RuntimeError("No pending OAuth session was found. Start authorization again.")


def complete_web_oauth(
    *,
    repo_root: Path | None = None,
    state: str,
    code: str,
    provider: str = "zomato",
) -> dict[str, Any]:
    """Complete hosted OAuth and persist mcp-remote-compatible auth files."""
    root = repo_root or repo_root_from_settings()
    user_id, secret_payload, pending_map, pending = _find_oauth_pending_session(state)
    user_id = pending.get("user_id") or user_id
    if int(time.time()) - int(pending.get("created_at", 0)) > _OAUTH_PENDING_TTL_SECONDS:
        pending_map.pop(state, None)
        _merge_mcp_token_secret(user_id, oauth_pending=pending_map)
        raise RuntimeError("OAuth session expired. Start authorization again.")

    # Derive provider label from pending state or fall back to provider param
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
            f"{provider_label} token exchange failed with HTTP {token_resp.status_code}: {token_resp.text[:300]}"
        )
    tokens = token_resp.json()
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise RuntimeError(f"{provider_label} token exchange did not return an access_token.")

    mcp_url = pending["mcp_url"]
    rel_paths = _mcp_remote_token_relative_paths(root, mcp_url)
    cfg_dir = _mcp_remote_config_dir(root, user_id=user_id)
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
    existing_files.update(files)
    pending_map.pop(state, None)
    _merge_mcp_token_secret(user_id, files=existing_files, oauth_pending=pending_map)
    return {
        "status": "connected_pending_test",
        "detail": "Zomato authorization completed. Run Test Connection to verify live MCP access.",
        "persisted_files": list(files.keys()),
        "mcp_url": mcp_url,
    }


def create_pairing_session(
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root or repo_root_from_settings()
    url = mcp_url or os.getenv("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    code = secrets.token_urlsafe(18)
    secret_payload = _mcp_token_secret(user_id)
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
        "mcp_remote_version": _mcp_remote_version(root) or "0.1.38",
        "user_id": user_id,
    }
    _merge_mcp_token_secret(user_id, pairing_sessions=sessions)
    return {
        "code": code,
        "mcp_url": url,
        "mcp_remote_version": sessions[code]["mcp_remote_version"],
        "expires_in_seconds": _PAIRING_TTL_SECONDS,
    }


def get_pairing_session_info(code: str, user_id: str | None = None) -> dict[str, Any] | None:
    secret_payload = _mcp_token_secret(user_id)
    sessions = secret_payload.get("pairing_sessions") if isinstance(secret_payload, dict) else None
    if not isinstance(sessions, dict):
        return None
    session = sessions.get(code)
    if not isinstance(session, dict):
        return None
    if int(time.time()) - int(session.get("created_at", 0)) > _PAIRING_TTL_SECONDS:
        sessions.pop(code, None)
        _merge_mcp_token_secret(user_id, pairing_sessions=sessions)
        return None
    result = dict(session)
    if user_id is not None:
        result["user_id"] = user_id
    return result


def find_pairing_session_info(code: str) -> dict[str, Any] | None:
    session = get_pairing_session_info(code)
    if session:
        return session

    payload = _root_mcp_token_secret()
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
    repo_root: Path | None = None,
    code: str,
    files: dict[str, str],
    user_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root or repo_root_from_settings()
    secret_payload = _mcp_token_secret(user_id)
    sessions = secret_payload.get("pairing_sessions") if isinstance(secret_payload, dict) else None
    if not isinstance(sessions, dict):
        raise RuntimeError("No pairing session was found. Generate a new connection command.")
    session = sessions.get(code)
    if not isinstance(session, dict):
        raise RuntimeError("Pairing code was not recognized. Generate a new connection command.")
    if int(time.time()) - int(session.get("created_at", 0)) > _PAIRING_TTL_SECONDS:
        sessions.pop(code, None)
        _merge_mcp_token_secret(user_id, pairing_sessions=sessions)
        raise RuntimeError("Pairing code expired. Generate a new connection command.")

    cfg_dir = _mcp_remote_config_dir(root, user_id=user_id)
    written: dict[str, str] = {}
    for name, content in files.items():
        rel = _safe_relative_token_path(name)
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
    existing_files.update(written)
    sessions.pop(code, None)
    _merge_mcp_token_secret(user_id, files=existing_files, pairing_sessions=sessions)
    return {
        "status": "ok",
        "detail": "MCP session synced. Return to Companion and test the connection.",
        "persisted_files": list(written.keys()),
    }


def restore_mcp_auth_tokens(repo_root: Path | None = None, user_id: str | None = None) -> bool:
    """Fetch OAuth tokens from Secrets Manager and write to MCP config dir.
    Returns True if tokens were found and restored."""
    root = repo_root or repo_root_from_settings()
    cfg_dir = _mcp_remote_config_dir(root, user_id=user_id)
    secret = _mcp_token_secret(user_id)
    files_data = secret.get("files") if isinstance(secret, dict) else None
    if not isinstance(files_data, dict) or not files_data:
        return False
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files_data.items():
        rel = _safe_relative_token_path(name)
        if rel is None:
            logger.warning("Skipping unsafe MCP token path from secret: %s", name)
            continue
        text = str(content)
        targets = [rel]
        alias = _current_version_token_alias(root, rel)
        if alias is not None:
            targets.append(alias)
        for target in targets:
            dest = cfg_dir / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
    logger.info("Restored %d MCP auth token file(s) from Secrets Manager.", len(files_data))
    return True


def persist_mcp_auth_tokens(repo_root: Path | None = None, user_id: str | None = None) -> dict[str, str]:
    """Read OAuth tokens from MCP config dir and store in Secrets Manager.
    Returns the files that were persisted."""
    root = repo_root or repo_root_from_settings()
    cfg_dir = _mcp_remote_config_dir(root, user_id=user_id)
    files_data: dict[str, str] = {}
    for p in _iter_token_files(cfg_dir):
        try:
            rel = p.relative_to(cfg_dir).as_posix()
        except ValueError:
            continue
        files_data[rel] = p.read_text(encoding="utf-8")
    if files_data:
        try:
            secret_payload = _mcp_token_secret(user_id)
            if not isinstance(secret_payload, dict):
                secret_payload = {}
            existing = secret_payload.get("files")
            if not isinstance(existing, dict):
                existing = {}
            existing.update(files_data)
            _merge_mcp_token_secret(user_id, files=existing)
            logger.info("Persisted %d MCP auth token file(s) to Secrets Manager.", len(files_data))
        except Exception:
            logger.exception("Persist MCP auth tokens failed")
            raise
    return files_data


def get_mcp_token_status(
    repo_root: Path | None = None,
    *,
    mcp_url: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Return a summary of MCP token state."""
    root = repo_root or repo_root_from_settings()
    url = mcp_url or os.getenv("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    cfg_dir = _mcp_remote_config_dir(root, user_id=user_id)
    local_paths = [p.relative_to(cfg_dir).as_posix() for p in _iter_token_files(cfg_dir)]
    secret = _mcp_token_secret(user_id)
    secret_files = list((secret.get("files") or {}).keys())
    expected = _expected_token_paths(root, url, user_id=user_id)
    expected_rel = _mcp_remote_token_relative_paths(root, url)
    expected_token_rel = expected_rel["tokens"]
    # ``has_local_tokens`` must be STRICT: a token file only counts as
    # "locally present" when it sits at the exact path the CURRENT
    # mcp-remote binary will read (``mcp-remote-<current-version>/<hash>_tokens.json``).
    # After an mcp-remote upgrade (0.1.37 → 0.1.38), files at the OLD
    # prefix are still on disk but the new binary cannot see them, so a
    # loose suffix-only match would lie about readiness and suppress the
    # auto-restore that writes the current-version alias — leaving the
    # provider stuck reporting ``ready`` while every real MCP call fails.
    #
    # ``has_secret_tokens`` stays LOOSE on purpose: a secret stored under
    # ANY prior version is restoreable, and ``restore_mcp_auth_tokens``
    # writes both the original path AND a current-version alias on the
    # next hydration.
    return {
        "config_dir": str(cfg_dir),
        "effective_config_dir": str(_mcp_remote_effective_config_dir(root, user_id=user_id)),
        "local_files": local_paths,
        "secret_files": secret_files,
        "has_local_tokens": expected_token_rel in local_paths,
        "has_secret_tokens": expected_token_rel in secret_files or _has_token_file_for_url(secret_files, url),
        "expected_files": expected,
        "expected_secret_files": expected_rel,
        "expected_tokens_present": Path(expected["tokens"]).is_file(),
    }


def ensure_mcp_tokens_on_disk(
    repo_root: Path,
    *,
    mcp_url: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Guarantee mcp-remote tokens are hydrated in the active config dir.

    Idempotent: a no-op when the current-version tokens file already exists
    on disk. On a cold Lambda container (or after ``/tmp`` was wiped) it
    pulls the user's tokens from Secrets Manager and writes them — including
    a current-version alias when the secret was stored under an older
    ``mcp-remote-X.Y.Z/`` prefix. Returns the post-hydration token status so
    callers can fail fast when no tokens exist anywhere.

    This is the single precondition every MCP runtime entry point must
    enforce before spawning ``mcp-remote``; without it, the bridge falls
    into its own OAuth handshake and ``session.initialize()`` times out.
    """
    status = get_mcp_token_status(repo_root, mcp_url=mcp_url, user_id=user_id)
    if status["has_local_tokens"]:
        return status
    if status["has_secret_tokens"]:
        try:
            restore_mcp_auth_tokens(repo_root, user_id=user_id)
        except Exception:
            logger.exception(
                "ensure_mcp_tokens_on_disk: restore from Secrets Manager failed for %s",
                mcp_url,
            )
            return status
        status = get_mcp_token_status(repo_root, mcp_url=mcp_url, user_id=user_id)
    return status


def capture_mcp_oauth_url(
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Try to detect MCP OAuth state via mcp-remote.
    
    Returns:
      - oauth_required: auth URL captured, user should open it
      - connected: mcp-remote connected (tokens present)
      - instructions: subprocess not viable (Lambda), return manual instructions
      - error: something went wrong
    """
    import subprocess
    
    root = repo_root or repo_root_from_settings()
    url = mcp_url or os.getenv("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    node = node_binary or os.getenv("NODE_BINARY", "node")
    
    # Check if node exists
    node_path = shutil.which(node)
    if not node_path:
        return {
            "status": "instructions",
            "detail": (
                "Node.js not found. Use the hosted OAuth flow from the app, then retry MCP testing."
            ),
            "mcp_url": url,
        }
    
    proxy_script = root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        return {
            "status": "instructions",
            "detail": (
                "mcp-remote bridge not found. Deploy with npm dependencies installed, then retry."
            ),
            "mcp_url": url,
        }
    
    cfg = _mcp_remote_config_dir(root)
    
    try:
        cfg.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    
    stdout_text = ""
    stderr_text = ""
    
    try:
        proc = subprocess.Popen(
            [node_path, str(proxy_script), url],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "MCP_REMOTE_CONFIG_DIR": str(cfg)},
        )
        try:
            stdout_text, stderr_text = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                stdout_text, stderr_text = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_text, stderr_text = proc.communicate(timeout=2)
        
    except FileNotFoundError:
        return {
            "status": "instructions",
            "detail": f"Cannot spawn {node_path}. Use hosted OAuth from the app, then retry.",
            "mcp_url": url,
        }
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "status": "error",
            "detail": f"Subprocess error: {type(e).__name__}: {e}. Run locally: npx mcp-remote {url}",
            "mcp_url": url,
        }

    combined_raw = "\n".join([stdout_text or "", stderr_text or ""])
    urls = [u.rstrip(".,;") for u in re.findall(r"https?://[^\s<>'\")]+", combined_raw)]
    # Derive provider-specific domain keyword from the MCP URL for auth URL detection
    parsed_mcp = urlparse(url)
    mcp_host_parts = (parsed_mcp.netloc or "").split(".")
    provider_keyword = mcp_host_parts[-2] if len(mcp_host_parts) >= 2 else (mcp_host_parts[0] if mcp_host_parts else "")
    provider_name = parsed_mcp.netloc.replace("mcp.", "").split(".")[0].title() if parsed_mcp.netloc else "the provider"
    auth_url = next(
        (
            u
            for u in urls
            if any(part in u.lower() for part in ("oauth", "authorize", "auth", "accounts"))
            and not u.rstrip("/").endswith("/mcp")
            and (not provider_keyword or provider_keyword in u.lower())
        ),
        None,
    )
    
    if auth_url:
        return {
            "status": "oauth_required",
            "authorization_url": auth_url,
            "detail": f"Open this URL in your browser to authorize {provider_name}.",
            "mcp_url": url,
        }
    
    combined = combined_raw.lower()
    if any(kw in combined for kw in ("connected", "ready", "session established")):
        return {
            "status": "connected",
            "detail": "mcp-remote connected successfully. Tokens present.",
            "mcp_url": url,
        }
    
    return {
        "status": "instructions",
        "detail": (
            "Could not detect OAuth state automatically. Use hosted OAuth from the app "
            f"(/api/mcp/oauth/start), or run: npx mcp-remote {url}"
        ),
        "mcp_url": url,
        "stderr_tail": "\n".join((stderr_text or "").splitlines()[-6:]),
    }


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# alias so the import in main.py resolves cleanly
utc_now_iso = _utc_now_iso


def run_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    address_id: str | None = None,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
    location_text: str | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
    session_addresses: list[dict[str, Any]] | None = None,
    session_menu: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = repo_root or repo_root_from_settings()
    url = mcp_url or os.getenv(
        "ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp"
    )
    node = node_binary or os.getenv("NODE_BINARY", "node")

    cleaned = []
    for s in tool_plan:
        if isinstance(s, dict):
            tn = str(s.get("tool") or "").strip()
            if tn:
                cleaned.append(
                    {
                        "tool": tn,
                        "arguments": dict(s.get("arguments") or {}),
                    }
                )

    try:
        steps, diagnostics = asyncio.run(
            _run_session_inner(
                cleaned,
                address_id.strip() if address_id else None,
                root,
                url,
                node,
                user_id,
                location_text=location_text,
                session_placeholders=session_placeholders,
                session_restaurants=session_restaurants,
                session_addresses=session_addresses,
                session_menu=session_menu,
            )
        )
        skipped = bool(diagnostics.get("skipped_tools"))
        return {
            "ok": not skipped,
            "error_detail": "tool_plan_incomplete" if skipped else None,
            "steps": steps,
            "diagnostics": diagnostics,
        }
    except BaseException as e:
        logger.exception("MCP execution failed for %s", url)
        return {
            "ok": False,
            "error_class": type(e).__name__,
            "error_detail": _humanize_mcp_failure(root, e, url),
            "steps": [],
            "diagnostics": {"mcp_remote_config_dir": str(_mcp_remote_config_dir(root, user_id=user_id))},
        }


async def _list_tools_inner(
    repo_root: Path,
    mcp_url: str,
    node_binary: str,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as e:
        raise RuntimeError(
            "Python MCP SDK is not installed for this interpreter. Use Python 3.10+ "
            "and install requirements.txt, or run the Lambda Python 3.12 runtime."
        ) from e

    proxy_script = repo_root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        raise RuntimeError("mcp-remote is not installed — run npm install at the repo root.")

    # Cold-container guard: hydrate /tmp/.mcp-auth from Secrets Manager when
    # the current-version tokens file isn't already present locally.
    ensure_mcp_tokens_on_disk(repo_root, mcp_url=mcp_url, user_id=user_id)

    params = StdioServerParameters(
        command=node_binary,
        args=[str(proxy_script), mcp_url],
        cwd=str(repo_root),
        env=_stdio_env(repo_root, user_id=user_id),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=_MCP_INIT_TIMEOUT_SECONDS)
            result = await asyncio.wait_for(session.list_tools(), timeout=_MCP_TOOL_TIMEOUT_SECONDS)
            tools = getattr(result, "tools", None) or []
            out: list[dict[str, Any]] = []
            for tool in tools:
                name = str(getattr(tool, "name", "") or "").strip()
                if not name:
                    continue
                out.append(
                    {
                        "name": name,
                        "description": str(getattr(tool, "description", "") or ""),
                    }
                )
            return out


def list_mcp_tools_sync(
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root or repo_root_from_settings()
    url = mcp_url or os.getenv("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    node = node_binary or os.getenv("NODE_BINARY", "node")
    try:
        tools = asyncio.run(_list_tools_inner(root, url, node, user_id=user_id))
        return {"ok": True, "tools": tools, "mcp_url": url}
    except BaseException as e:
        logger.exception("MCP tool listing failed")
        return {
            "ok": False,
            "error_class": type(e).__name__,
            "error_detail": _humanize_mcp_failure(root, e, url),
            "tools": [],
            "mcp_url": url,
        }
