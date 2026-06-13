"""Serialize an MCP `call_tool` result into a JSON-friendly envelope.

Ported (behaviour-preserving) from `zomato.mcp_executor._tool_result_payload`. The
envelope is `{"is_error": bool, "data": <json>}`, additively surfacing image and
resource content blocks so chat hosts can render them (e.g. a checkout QR image).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _harvest_non_text(result: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    images: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    for blk in getattr(result, "content", ()) or []:
        btype = getattr(blk, "type", None)
        if btype == "image":
            data_b64 = getattr(blk, "data", None) or ""
            if data_b64:
                images.append(
                    {
                        "mime_type": str(getattr(blk, "mimeType", "image/png") or "image/png"),
                        "data_b64": str(data_b64),
                    }
                )
        elif btype == "resource":
            resource = getattr(blk, "resource", None)
            if resource is not None:
                attachments.append(
                    {
                        "uri": getattr(resource, "uri", None),
                        "mime_type": getattr(resource, "mimeType", None),
                        "text": getattr(resource, "text", None),
                    }
                )
    return images, attachments


def _parse_content_blocks(result: Any) -> Any:
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
            parsed.append({"type": "image", "mime_type": getattr(blk, "mimeType", None)})
        else:
            parsed.append({"type": btype or "?", "repr": str(blk)})
    return parsed[0] if len(parsed) == 1 else {"blocks": parsed}


def tool_result_payload(result: Any) -> dict[str, Any]:
    """Return `{is_error, data, images?, attachments?}` from an MCP call result."""
    out: dict[str, Any] = {"is_error": bool(getattr(result, "isError", False))}
    try:
        images, attachments = _harvest_non_text(result)
        if images:
            out["images"] = images
        if attachments:
            out["attachments"] = attachments
        structured = getattr(result, "structuredContent", None)
        if structured:  # non-empty only — empty {} still has useful content blocks
            out["data"] = structured
            return out
        out["data"] = _parse_content_blocks(result)
        return out
    except Exception as exc:  # never let serialization crash a turn
        logger.warning("serialize tool result: %s", exc)
        out["serialization_error"] = str(exc)
        return out
