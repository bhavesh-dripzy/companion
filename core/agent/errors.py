"""ErrorClassifier — provider prose (the only signal MCP gives) → canonical ErrorKind.

Each provider ships a small ordered table of (substring → ErrorKind). One canonical taxonomy
means one set of recoveries in the cart strategies, reused across providers. Grounded in the
live POCs (swiggy_bhavesh.md / zepto_poc.md).
"""

from __future__ import annotations

from core.agent.contracts import ErrorKind

# Ordered: first matching substring (case-insensitive) wins.
_INSTAMART = [
    ("max per item quantity", ErrorKind.MAX_QTY),
    ("partially available", ErrorKind.PARTIAL_STOCK),
    ("not serviceable", ErrorKind.NOT_SERVICEABLE),
    ("currently unavailable or closed", ErrorKind.STORE_CLOSED),
    ("no valid items", ErrorKind.STALE_ITEM),
    ("item inventory not available", ErrorKind.EMPTY_CART),  # get_cart on an empty cart
    ("items array is required", ErrorKind.EMPTY_CART),
]

_ZEPTO = [
    ("out of stock", ErrorKind.PARTIAL_STOCK),
    ("not serviceable", ErrorKind.NOT_SERVICEABLE),
    ("unavailable or closed", ErrorKind.STORE_CLOSED),
    ("cart not found", ErrorKind.EMPTY_CART),
    ("cart is empty", ErrorKind.EMPTY_CART),
    ("empty cart", ErrorKind.EMPTY_CART),
]

_AUTH = ["unauthor", "authentication", "auth required", "401", "token"]

_TABLES: dict[str, list[tuple[str, ErrorKind]]] = {
    "instamart": _INSTAMART,
    "zepto": _ZEPTO,
}


def classify(provider: str, message: str) -> ErrorKind:
    text = (message or "").lower()
    if not text:
        return ErrorKind.UNKNOWN
    if any(a in text for a in _AUTH):
        return ErrorKind.AUTH_REQUIRED
    for needle, kind in _TABLES.get(provider, []):
        if needle in text:
            return kind
    return ErrorKind.UNKNOWN


# Clean, user-facing Hinglish for each ErrorKind — NEVER expose the raw provider prose,
# Report IDs, or "Something went wrong…" boilerplate to the user.
USER_MESSAGES: dict[ErrorKind, str] = {
    ErrorKind.MAX_QTY: "Is item ki max quantity limit hai — cart pehle jaisa hi rakha hai.",
    ErrorKind.PARTIAL_STOCK: "Itni quantity stock mein nahi hai — thodi kam available hai.",
    ErrorKind.NOT_SERVICEABLE: "Yeh address abhi serviceable nahi hai — doosra address try karein.",
    ErrorKind.STORE_CLOSED: "Store abhi band hai — thodi der baad try karein.",
    ErrorKind.STALE_ITEM: "Yeh item abhi nahi mil raha — dobara search karke try karein.",
    ErrorKind.EMPTY_CART: "Aapka cart abhi khaali hai.",
    ErrorKind.AUTH_REQUIRED: "Pehle is app ko connect karna padega.",
    ErrorKind.RATE_LIMIT: "Thoda ruk ke dobara try karein.",
}


def user_message(kind: ErrorKind | str) -> str:
    if isinstance(kind, str):
        try:
            kind = ErrorKind(kind)
        except ValueError:
            return "Kuch dikkat aa gayi — dobara try karein?"
    return USER_MESSAGES.get(kind, "Kuch dikkat aa gayi — dobara try karein?")
