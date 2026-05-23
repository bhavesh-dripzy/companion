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
    "__MENU_ITEM_ID__": ("menu_item_id", "menuItemId", "item_id", "itemId"),
    "__ITEM_ID__": ("item_id", "itemId", "menu_item_id", "menuItemId"),
    "__CART_ID__": ("cart_id", "cartId"),
    "__ORDER_ID__": ("order_id", "orderId"),
    # NOTE: __VARIANT_ID__ is intentionally *not* in this map. It is a
    # single-valued scalar but `create_cart.items[]` needs distinct variants
    # per row, so global-binding (first-seen wins) silently pinned every row
    # to whatever item the menu listed first (a recommendation/combo at the
    # top), causing systematic mis-adds. Variant resolution is now done by
    # name via `_resolve_cart_items_by_name` against the per-session menu
    # index built from `get_menu_items_listing` / `get_restaurant_menu_by_categories`.
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
# Cap how many menu items we hand to the *response* LLM. The executor's
# menu_index (used to resolve item names -> variant_ids for create_cart) is
# built from the FULL response and is unaffected — only the trimmed envelope
# is what flows into `step.result` and on to the response-builder LLM /
# downstream chat host. A 115-item Burger Singh menu was ~25 KB / ~6.4K
# tokens; trimming to 10 cuts that ~10x. Tunable via env so we can dial it
# per environment without code changes.
_MAX_MENU_ITEMS_FOR_RESPONSE = max(1, int(os.getenv("ZOMATO_MENU_RESPONSE_ITEM_LIMIT", "10")))
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
# Menu index — per-session memory of name -> variant_id for cart resolution.
# Built from get_menu_items_listing / get_restaurant_menu_by_categories
# responses so that create_cart can resolve items by user-visible name even
# when the LLM omits or guesses variant_id. See RCA for "Udta Punjab vs
# Nikku Singh" hallucination — this index replaces the broken __VARIANT_ID__
# placeholder.
# ---------------------------------------------------------------------------


def _norm_item_key(name: str) -> str:
    s = (name or "").strip().lower()
    while s.endswith(".") or s.endswith(",") or s.endswith(";"):
        s = s[:-1].rstrip()
    return " ".join(s.split())


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


def _norm_restaurant_key(name: str) -> str:
    """Normalize restaurant name for lookup. Matches the user-typed form.

    Strips punctuation, collapses whitespace, lowercases. Symmetric to
    ``_norm_item_key`` so the same comparison rules apply.
    """
    if not isinstance(name, str):
        return ""
    s = name.strip().lower()
    # Strip trailing tagline-style suffixes ("Paradise Biryani - A Legend Since 1953")
    # — we keep the head for matching, full name in the value.
    if " - " in s:
        s = s.split(" - ", 1)[0]
    while s.endswith(".") or s.endswith(",") or s.endswith(";"):
        s = s[:-1].rstrip()
    return " ".join(s.split())


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


def _collect_menu_index_from_envelope(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return {normalized_name -> {variant_id, item_name, price?, discounted_price?}}.

    Walks both shapes Zomato uses:
      * get_menu_items_listing → result.data.item_mappings[]
      * get_restaurant_menu_by_categories → result.data.menu_items[]
    Multiple variants with the same normalized name collapse to the shortest
    `item_name` (treated as the canonical entry) — e.g. "Veg Nikku Singh
    Burger" wins over "Veg Nikku Singh Burger Combo".
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return out

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
        key = _norm_item_key(name)
        if not key:
            continue
        entry = {
            "variant_id": vid,
            "item_name": name.strip(),
            "price": row.get("price"),
            "discounted_price": row.get("discounted_price"),
            "categories": row.get("categories"),
        }
        prev = out.get(key)
        if prev is None or len(name.strip()) < len(prev.get("item_name") or ""):
            out[key] = entry
    return out


def _lookup_variant_id_by_name(
    query: str,
    menu_index: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Resolve a user-facing item name to a variant_id.

    Match cascade (most-specific first; stop at the first unambiguous hit):
      1. Exact (normalized) match.
      2. Prefix on either side ("Veg Nikku" matches "Veg Nikku Singh Burger" when unique).
      3. Token-set containment — every token of the shorter side appears in the
         longer side. This is the common LLM failure mode ("Veg Nikku Burger" vs
         the canonical "Veg Nikku Singh Burger" — the LLM drops middle words).
      4. Plain substring containment.
    If a step has multiple candidates, refuse to guess (returns None) — silent
    wrong-match is worse than asking the user.
    """
    if not menu_index:
        return None, None
    key = _norm_item_key(query)
    if not key:
        return None, None
    entry = menu_index.get(key)
    if entry:
        return entry["variant_id"], entry.get("item_name")
    # Prefix on either side
    prefix_hits = [
        (k, e)
        for k, e in menu_index.items()
        if k.startswith(key) or key.startswith(k)
    ]
    if len(prefix_hits) == 1:
        _, e = prefix_hits[0]
        return e["variant_id"], e.get("item_name")
    # Token-set containment (shorter ⊆ longer, in either direction)
    query_tokens = set(key.split())
    if query_tokens:
        token_hits: list[tuple[str, dict[str, Any]]] = []
        for k, e in menu_index.items():
            cand_tokens = set(k.split())
            if not cand_tokens:
                continue
            if query_tokens.issubset(cand_tokens) or cand_tokens.issubset(query_tokens):
                token_hits.append((k, e))
        if len(token_hits) == 1:
            _, e = token_hits[0]
            return e["variant_id"], e.get("item_name")
        if len(token_hits) > 1:
            # Tie-break: prefer the candidate whose token-count is closest to
            # the query's (i.e. the shortest superset / least over-specified).
            token_hits.sort(key=lambda kv: abs(len(kv[0].split()) - len(query_tokens)))
            best_diff = abs(len(token_hits[0][0].split()) - len(query_tokens))
            best = [
                kv for kv in token_hits
                if abs(len(kv[0].split()) - len(query_tokens)) == best_diff
            ]
            if len(best) == 1:
                _, e = best[0]
                return e["variant_id"], e.get("item_name")
    # Substring containment
    sub_hits = [(k, e) for k, e in menu_index.items() if key in k or k in key]
    if len(sub_hits) == 1:
        _, e = sub_hits[0]
        return e["variant_id"], e.get("item_name")
    return None, None


def _resolve_cart_items_by_name(
    args: dict[str, Any],
    menu_index: dict[str, dict[str, Any]],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Mutate `args.items[]` so every row has a real `variant_id`.

    Each row may carry any of: `variant_id`, `name`, `item_name`, `title`,
    `quantity`. Rules:
      * Literal variant_id (e.g. `v_992713485`) → keep as-is.
      * Missing/empty variant_id → look up by name via menu_index.
      * Unresolvable rows are dropped and reported under
        `diagnostics["cart_item_resolution"]`.
    """
    items = args.get("items") if isinstance(args.get("items"), list) else None
    if not items:
        return args

    resolved: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        vid_raw = str(raw.get("variant_id") or raw.get("variantId") or "").strip()
        name = (
            raw.get("name")
            or raw.get("item_name")
            or raw.get("itemName")
            or raw.get("title")
        )
        # Strip placeholder-shaped values; the LLM may still emit them.
        if vid_raw.startswith("__") and vid_raw.endswith("__"):
            vid_raw = ""
        if not vid_raw:
            looked_up, matched_name = _lookup_variant_id_by_name(
                str(name or ""), menu_index
            )
            if looked_up:
                vid_raw = looked_up
                notes.append(
                    {
                        "requested_name": str(name or ""),
                        "matched_name": matched_name,
                        "variant_id": looked_up,
                        "source": "menu_index",
                    }
                )
            else:
                notes.append(
                    {
                        "requested_name": str(name or ""),
                        "variant_id": None,
                        "source": "menu_index",
                        "reason": "no_match_or_ambiguous",
                    }
                )
                continue
        new_row: dict[str, Any] = {
            "variant_id": vid_raw,
            "quantity": int(raw.get("quantity") or 1),
        }
        # Forward through additional fields the server may want.
        for extra in ("addons", "addon_groups", "customizations"):
            if extra in raw:
                new_row[extra] = raw[extra]
        resolved.append(new_row)

    if notes:
        diagnostics.setdefault("cart_item_resolution", []).extend(notes)
    args["items"] = resolved
    return args


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
    max_items: int = _MAX_MENU_ITEMS_FOR_RESPONSE,
) -> dict[str, Any] | None:
    """Trim a menu-tool envelope in-place so the response LLM sees at most N rows.

    Mutates the envelope (which is the same object already stored in
    ``step_results`` — so the trim propagates to the response builder without
    a copy). The caller is expected to have populated ``menu_index`` from the
    *full* envelope first; only the user-facing slice is trimmed here.

    Two shapes are handled:
      * ``get_menu_items_listing``       → ``data.result.data.item_mappings[]``
      * ``get_restaurant_menu_by_categories`` → ``data.result.data.menu_items[]``

    Adds breadcrumb metadata under ``data.result.data._menu_truncation`` so the
    response LLM (and any debug consumer) can mention "showing top 10 of N" if
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
) -> None:
    """
    get_menu_items_listing does not include rupee prices (Zomato MCP design).
    Fetch get_restaurant_menu_by_categories for deduped category names from the listing
    and merge price + discounted_price onto each item_mapping row by variant_id.
    """
    if listing_envelope.get("is_error"):
        return
    ims, inner = _listing_item_mappings_blob(listing_envelope)
    if not ims or inner is None:
        return
    rid = res_id if res_id is not None else inner.get("res_id")
    aid = address_id if address_id is not None else inner.get("address_id")
    if rid is None or aid is None or (isinstance(aid, str) and not str(aid).strip()):
        return
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
        return
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
        return
    cat_env = _tool_result_payload(r)
    if cat_env.get("is_error"):
        return
    vmap = _variant_price_map_from_category_menu(cat_env)
    if not vmap:
        return
    merged = inner.get("item_mappings")
    if not isinstance(merged, list):
        return
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
    if resolved_id:
        placeholder_values["__ADDRESS_ID__"] = resolved_id
    if placeholder_values:
        diagnostics["seeded_placeholders"] = sorted(placeholder_values.keys())
    prefetch_done = False
    # Per-session menu memory: populated after each menu tool, consumed by
    # create_cart to resolve items by user-visible name. See RCA notes above.
    menu_index: dict[str, dict[str, Any]] = {}
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
                nonlocal resolved_id, prefetch_done, step_index
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
                else:
                    diagnostics["auto_address_failure"] = (
                        "Could not extract address_id from Zomato response."
                    )
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

                # For create_cart, resolve any items[] entries that are
                # name-only (or carry the broken __VARIANT_ID__ placeholder)
                # using the per-session menu_index built from prior menu tools
                # in *this* plan. Without this, the LLM either invents
                # variant_ids or relies on placeholder substitution that pins
                # every row to the first menu item.
                if tool == "create_cart":
                    exec_args = _resolve_cart_items_by_name(
                        exec_args if isinstance(exec_args, dict) else {},
                        menu_index,
                        diagnostics,
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

                if tool == "get_menu_items_listing" and not envelope.get("is_error"):
                    try:
                        await _enrich_menu_listing_with_category_prices(
                            session,
                            envelope,
                            res_id=exec_args.get("res_id"),
                            address_id=exec_args.get("address_id"),
                        )
                    except Exception as e:
                        logger.info("menu price enrichment failed: %s", e)

                if tool in {"get_menu_items_listing", "get_restaurant_menu_by_categories"}:
                    try:
                        new_entries = _collect_menu_index_from_envelope(envelope)
                    except Exception as e:
                        logger.info("menu index extraction failed: %s", e)
                        new_entries = {}
                    if new_entries:
                        menu_index.update(new_entries)
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
                    # IMPORTANT: trim AFTER the menu_index has captured the
                    # full list. The index keeps the full name->variant_id
                    # mapping (so create_cart can still resolve any item the
                    # user names later in this plan), while the envelope
                    # itself — which is what flows to the response LLM —
                    # is reduced to the top-N rows.
                    try:
                        trimmed = _truncate_menu_envelope_for_response(envelope)
                    except Exception as e:
                        logger.info("menu envelope truncation failed: %s", e)
                        trimmed = None
                    if trimmed:
                        diagnostics.setdefault("menu_truncation", {})[tool] = trimmed

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

                if resolved_id is None and tool == "get_saved_addresses_for_user":
                    nid = _first_address_id_from_saved_payload(envelope)
                    if nid:
                        resolved_id = nid
                        placeholder_values["__ADDRESS_ID__"] = nid
                        diagnostics["resolved_address_id"] = resolved_id
                _learn_placeholders_from_payload(placeholder_values, envelope)
                diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())

    diagnostics["resolved_address_id"] = resolved_id
    diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())
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
    return {
        "config_dir": str(cfg_dir),
        "effective_config_dir": str(_mcp_remote_effective_config_dir(root, user_id=user_id)),
        "local_files": local_paths,
        "secret_files": secret_files,
        "has_local_tokens": expected_token_rel in local_paths or _has_token_file_for_url(local_paths, url),
        "has_secret_tokens": expected_token_rel in secret_files or _has_token_file_for_url(secret_files, url),
        "expected_files": expected,
        "expected_secret_files": expected_rel,
        "expected_tokens_present": Path(expected["tokens"]).is_file(),
    }


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
