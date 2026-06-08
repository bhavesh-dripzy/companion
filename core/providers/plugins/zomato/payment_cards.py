"""Zomato payment-attachment builder (clean port of the legacy QR collector).

Ported from `zomato.mcp_executor._extract_payment_from_checkout` +
`agents.pipeline._collect_payment_attachments`, adapted to operate on
`ToolResult`s. A successful `checkout_cart` envelope carries the UPI QR inline at
`images[0].data_b64`; we lift it into a renderable `image` attachment. UPI-field
enrichment (decoding the QR to pull payee/amount) is best-effort and lazy so core
never hard-depends on OpenCV — the attachment renders fine without it.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_PAYMENT_TOOLS = {"checkout_cart"}
_MAX_DATA_B64_CHARS = 200_000  # ~150 KB raw; refuse to inline larger payloads
_UPI_FIELDS = ("upi_link", "amount_inr", "payee_vpa", "payee_name", "transaction_ref")


def _decode_upi(data_b64: str) -> dict[str, Any]:
    """Best-effort: decode the QR PNG to a upi:// URI and parse NPCI fields.

    Lazy-imports OpenCV; returns {} when it's unavailable or the decode fails, so
    the core payment attachment never depends on a heavy optional dependency.
    """
    try:
        import base64
        from urllib.parse import parse_qs, urlparse

        import cv2  # type: ignore
        import numpy as np  # type: ignore

        raw = base64.b64decode(data_b64)
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return {}
        uri, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        if not uri or not uri.lower().startswith("upi://"):
            return {}
        params = parse_qs(urlparse(uri).query)

        def _first(key: str) -> str | None:
            v = params.get(key)
            return v[0] if v else None

        out: dict[str, Any] = {"upi_link": uri}
        for key, npci in (
            ("amount_inr", "am"),
            ("payee_vpa", "pa"),
            ("payee_name", "pn"),
            ("transaction_ref", "tr"),
        ):
            val = _first(npci)
            if val:
                out[key] = val
        return out
    except Exception:
        return {}


def _caption(payment: dict[str, Any]) -> str:
    parts = ["Scan with any UPI app"]
    if payment.get("amount_inr"):
        parts.append(f"₹{payment['amount_inr']}")
    payee = payment.get("payee_name") or payment.get("payee_vpa")
    if payee:
        parts.append(f"to {payee}")
    return " — ".join(parts)


def _payment_attachment(tr: ToolResult) -> Attachment | None:
    envelope = tr.data if isinstance(tr.data, dict) else {}
    images = envelope.get("images") or []
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        return None
    first = images[0]
    data_b64 = first.get("data_b64")
    if not isinstance(data_b64, str) or not data_b64.strip():
        return None
    mime = first.get("mime_type") or "image/png"
    source = f"zomato.{tr.tool}"
    if len(data_b64) > _MAX_DATA_B64_CHARS:
        # Too large to inline — drop a breadcrumb so the UI can fall back to a link.
        payload = {
            "kind": "image",
            "source": source,
            "mime_type": mime,
            "oversize": True,
            "alt": "Payment QR code",
            "caption": "QR image too large to inline; open via UPI link.",
            **_decode_upi(data_b64),
        }
        return Attachment(**{k: v for k, v in payload.items() if v is not None})
    upi = _decode_upi(data_b64)
    payload: dict[str, Any] = {
        "kind": "image",
        "source": source,
        "mime_type": mime,
        "data_b64": data_b64,
        "alt": "Payment QR code",
        "caption": _caption(upi),
    }
    for key in _UPI_FIELDS:
        if upi.get(key):
            payload[key] = upi[key]
    return Attachment(**payload)


def build_payment_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _PAYMENT_TOOLS or not tr.ok:
            continue
        att = _payment_attachment(tr)
        if att is not None:
            out.append(att)
    return out
