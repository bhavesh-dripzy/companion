"""Normalized internal model — one shape regardless of provider.

Money is always **minor units (paise)**: Instamart prices arrive in rupees (x100 here),
Zepto already in paise. Item ids differ per provider (spinId / productVariantId / menu id)
but flow through `Product.item_id` uniformly. Pure data — no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorKind(str, Enum):
    OK = "ok"
    MAX_QTY = "max_qty"
    PARTIAL_STOCK = "partial_stock"
    NOT_SERVICEABLE = "not_serviceable"
    STORE_CLOSED = "store_closed"
    STALE_ITEM = "stale_item"
    EMPTY_CART = "empty_cart"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMIT = "rate_limit"
    UNKNOWN = "unknown"


def rupees_to_minor(value: Any) -> int:
    """₹120 / '₹120.00' / 120 → 12000 (paise). Robust to strings with ₹/commas."""
    if value is None:
        return 0
    if isinstance(value, int | float):
        return round(float(value) * 100)
    s = str(value).replace("₹", "").replace(",", "").strip()
    try:
        return round(float(s) * 100)
    except ValueError:
        return 0


def fmt_minor(minor: int) -> str:
    """12000 → '₹120'. Whole rupees when even, else 2dp."""
    rupees = (minor or 0) / 100
    return f"₹{int(rupees)}" if rupees == int(rupees) else f"₹{rupees:.2f}"


@dataclass
class Product:
    """A single purchasable variation shown to the user."""

    provider: str
    item_id: str  # spinId / productVariantId / menu id
    name: str
    price_minor: int = 0
    in_stock: bool = True
    brand: str = ""
    variation: str = ""  # pack size / portion
    image_url: str = ""
    secondary_id: str = ""  # Zepto storeProductId, etc.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CartItem:
    item_id: str
    name: str
    qty: int
    price_minor: int = 0
    secondary_id: str = ""
    image_url: str = ""  # providers' cart APIs omit it → enriched from the catalog (§5b)


@dataclass
class Cart:
    provider: str
    items: list[CartItem] = field(default_factory=list)
    total_minor: int = 0
    bill: list[dict[str, Any]] = field(default_factory=list)  # [{label, minor}]
    cart_id: str = ""
    unavailable: str = ""  # set when checkout pricing fails (store closed / not serviceable)

    @property
    def is_empty(self) -> bool:
        return not self.items

    def qty_of(self, item_id: str) -> int:
        return next((i.qty for i in self.items if i.item_id == item_id), 0)


@dataclass
class CartResult:
    """The normalized outcome of a cart mutation (success OR a classified failure)."""

    ok: bool
    cart: Cart | None = None
    error: ErrorKind = ErrorKind.OK
    message: str = ""  # raw provider prose (for narration/debug)
    detail: dict[str, Any] = field(default_factory=dict)  # e.g. {cap: 1, available: 3}

    @classmethod
    def success(cls, cart: Cart) -> CartResult:
        return cls(ok=True, cart=cart)

    @classmethod
    def failure(cls, kind: ErrorKind, message: str = "", **detail: Any) -> CartResult:
        return cls(ok=False, error=kind, message=message, detail=detail)
