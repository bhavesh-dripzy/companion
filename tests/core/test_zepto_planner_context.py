"""Regression: the Zepto planner brief must render from a typed `SessionState`.

In the plan stage `ctx.session` is a `core.contracts.session.SessionState`
(a `legacy` flat bag + `placeholders`), NOT a flat dict. The previous renderer
read `ctx.session` as a dict, so `isinstance(..., dict)` was False and it emitted
an EMPTY brief — the planner never saw the cart / saved addresses / products and
fell back to search placeholders for cart-line ops (qty change / remove broke,
address switches hallucinated ids). These tests pin the typed-session read.
"""

import pytest

from core.contracts.session import SessionState
from core.providers.plugins.zepto.planner_context import build_context

pytestmark = pytest.mark.unit


class _Ctx:
    def __init__(self, session):
        self.session = session


_CART = {
    "items": [
        {
            "name": "Humpy A2 Cow Fresh Milk",
            "product_variant_id": "54d5a66c-pv",
            "store_product_id": "813367eb-sp",
            "quantity": 1,
        }
    ]
}
_ADDRS = [{"address_id": "201cab8f-off", "label": "office", "line": "Sector 48"}]
_PRODS = [
    {
        "name": "Humpy A2 Cow Fresh Milk",
        "product_variant_id": "54d5a66c-pv",
        "store_product_id": "813367eb-sp",
        "price_inr": 44,
    }
]


def test_brief_renders_from_typed_sessionstate():
    """The real plan-stage shape: ctx.session is a SessionState with a legacy bag."""
    session = SessionState(
        legacy={
            "session_zepto_cart": _CART,
            "session_zepto_addresses": _ADDRS,
            "session_zepto_products": _PRODS,
        }
    )
    brief = build_context(_Ctx(session))
    assert brief, "brief must not be empty for a populated session"
    # Cart block with the literal pvid/spid the planner needs for a qty change.
    assert "Current Zepto cart" in brief
    assert "pvid=54d5a66c-pv, spid=813367eb-sp" in brief
    # Saved-address block so a switch copies the real addressId (no hallucination).
    assert "office" in brief and "addressId=201cab8f-off" in brief
    # Products block.
    assert "Zepto products already shown" in brief


def test_brief_still_handles_raw_flat_dict_session():
    """Tolerant of a raw flat legacy dict too (some call sites pass it directly)."""
    brief = build_context(_Ctx({"session_zepto_cart": _CART}))
    assert "Current Zepto cart" in brief
    assert "pvid=54d5a66c-pv, spid=813367eb-sp" in brief


def test_empty_session_yields_empty_brief():
    assert build_context(_Ctx(SessionState(legacy={}))) == ""
