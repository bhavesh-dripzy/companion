"""
Run Zomato MCP tool_plan via stdio bridge (mcp-remote + node), same stack as Cursor.
"""

from __future__ import annotations

import asyncio
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
from urllib.parse import urlencode, urlparse
from typing import Any

import boto3
import requests

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
    "__VARIANT_ID__": ("variant_id", "variantId"),
    "__CART_ID__": ("cart_id", "cartId"),
    "__ORDER_ID__": ("order_id", "orderId"),
}
_TOKEN_SECRET_ID = "companion/mcp-tokens"
_MCP_INIT_TIMEOUT_SECONDS = int(os.getenv("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "20"))
_MCP_TOOL_TIMEOUT_SECONDS = int(os.getenv("ZOMATO_MCP_TOOL_TIMEOUT_SECONDS", "30"))
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


def repo_root_from_settings() -> Path:
    try:
        from django.conf import settings

        return Path(settings.BASE_DIR)
    except Exception:
        return Path(__file__).resolve().parents[2]


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
    out: dict[str, Any] = {"is_error": bool(getattr(result, "isError", False))}
    try:
        if result.structuredContent is not None:
            out["data"] = result.structuredContent
            return out
        parsed: list[Any] = []
        for blk in getattr(result, "content", ()) or []:
            if getattr(blk, "type", None) == "text":
                txt = getattr(blk, "text", "") or ""
                try:
                    parsed.append(json.loads(txt))
                except json.JSONDecodeError:
                    parsed.append({"raw_text": txt})
            else:
                parsed.append({"type": getattr(blk, "type", "?"), "repr": str(blk)})
        out["data"] = parsed[0] if len(parsed) == 1 else {"blocks": parsed}
        return out
    except Exception as e:
        logger.warning("serialize tool result: %s", e)
        out["serialization_error"] = str(e)
        return out


def _first_address_id_from_saved_payload(tool_payload: dict[str, Any]) -> str | None:
    blob = tool_payload.get("data")
    if blob is None:
        return None
    objs: list[dict[str, Any]] = []
    if isinstance(blob, dict):
        objs.append(blob)
        r = blob.get("result")
        if isinstance(r, dict):
            objs.append(r)
    elif isinstance(blob, list):
        for entry in blob:
            if isinstance(entry, dict):
                objs.append(entry)
                rr = entry.get("result")
                if isinstance(rr, dict):
                    objs.append(rr)

    for dd in objs:
        addresses = dd.get("addresses")
        if isinstance(addresses, list) and addresses and isinstance(addresses[0], dict):
            aid = addresses[0].get("address_id")
            if aid is not None:
                s = str(aid).strip()
                if s:
                    return s
    return None


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
    diagnostics: dict[str, Any] = {"requested_address_id": effective_address_id}

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
    placeholder_values: dict[str, str] = {}
    if resolved_id:
        placeholder_values["__ADDRESS_ID__"] = resolved_id
    prefetch_done = False

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
                nid = _first_address_id_from_saved_payload(envelope)
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
                    continue

                if tool == "get_saved_addresses_for_user":
                    prefetch_done = True

                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments=exec_args or {}),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
                )
                envelope = _tool_result_payload(result)
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
) -> dict[str, Any]:
    """Start a hosted OAuth flow and return the Zomato authorization URL."""
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
    }
    secret_payload = _mcp_token_secret()
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
    secret_payload["oauth_pending"] = pending_map
    secret_payload["updated_at"] = utc_now_iso()
    _write_mcp_token_secret(secret_payload)

    params = {
        "response_type": "code",
        "client_id": client_info["client_id"],
        "redirect_uri": callback_url,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
        "scope": scope,
    }
    authorization_url = f"{auth_server['authorization_endpoint']}?{urlencode(params)}"
    return {
        "status": "oauth_required",
        "authorization_url": authorization_url,
        "detail": "Continue to Zomato and approve access. Companion will save the MCP session automatically.",
        "mcp_url": url,
    }


def complete_web_oauth(
    *,
    repo_root: Path | None = None,
    state: str,
    code: str,
    provider: str = "zomato",
) -> dict[str, Any]:
    """Complete hosted OAuth and persist mcp-remote-compatible auth files."""
    root = repo_root or repo_root_from_settings()
    secret_payload = _mcp_token_secret()
    pending_map = secret_payload.get("oauth_pending") if isinstance(secret_payload, dict) else None
    if not isinstance(pending_map, dict):
        raise RuntimeError("No pending OAuth session was found. Start authorization again.")
    pending = pending_map.get(state)
    if not isinstance(pending, dict):
        raise RuntimeError("OAuth state was not recognized. Start authorization again.")
    if int(time.time()) - int(pending.get("created_at", 0)) > _OAUTH_PENDING_TTL_SECONDS:
        pending_map.pop(state, None)
        _merge_mcp_token_secret(oauth_pending=pending_map)
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
    cfg_dir = _mcp_remote_config_dir(root)
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
    _merge_mcp_token_secret(files=existing_files, oauth_pending=pending_map)
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