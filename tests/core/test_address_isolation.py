"""Per-provider delivery address — a switch on one app must NOT touch the other's address.

Regression for the bug where a Zepto address switch leaked into Instamart's address id (via the
shared per-domain selected_context), so every Instamart cart write then failed.
"""

from __future__ import annotations

from core.agent.domains import grocery_view as V
from core.agent.pipeline import _apply_address_ids
from core.agent.state import ConversationState


def _st():
    st = ConversationState(user_id="u", chat_id="c")
    st.provider_session["instamart"] = {"address_id": "im-addr"}
    st.provider_session["zepto"] = {"address_id": "zp-addr"}
    return st


def test_addr_keys_are_per_provider():
    st = _st()
    keys = V.addr_keys(st, ["instamart", "zepto"])
    assert keys["selected_instamart_address_id"] == "im-addr"
    assert keys["selected_zepto_address_id"] == "zp-addr"  # each its own, not crossed


def test_zepto_address_change_leaves_instamart_untouched():
    st = _st()
    # a Zepto switch only rewrites Zepto's provider_session
    st.provider_session["zepto"] = {"address_id": "zp-new", "label": "vansh"}
    assert V.addr_keys(st, ["instamart"])["selected_instamart_address_id"] == "im-addr"


def test_fe_override_is_per_provider():
    st = _st()
    _apply_address_ids(st, {"zepto": "zp-new"})  # FE changed only Zepto
    assert st.provider_session["zepto"]["address_id"] == "zp-new"
    assert st.provider_session["instamart"]["address_id"] == "im-addr"  # not poisoned


def test_cart_attachment_uses_provider_own_address():
    from core.agent.contracts import Cart, CartItem

    st = _st()
    cart = Cart(provider="instamart", items=[CartItem(item_id="a", name="x", qty=1)])
    atts = V.cart_atts("instamart", cart, st)
    assert atts[0]["delivery_address_id"] == "im-addr"  # instamart's own, not zepto's
