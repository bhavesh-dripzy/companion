"""Partial-ambiguity: when SOME items resolve and SOME are ambiguous/unknown, the resolved items
must still land in the cart and the turn must ask for the rest (with concrete options) — never
silently drop what resolved. Covers grocery_resolve.disambig_options + grocery_view.disambiguation_result.
"""

from __future__ import annotations

from core.agent.contracts import Cart, CartItem, Product
from core.agent.domains import grocery_resolve as R
from core.agent.domains import grocery_view as V
from core.agent.state import ConversationState


def _st_with_catalog():
    st = ConversationState(user_id="u", chat_id="c")
    st.provider_session["instamart"] = {"address_id": "im-addr", "label": "ghar"}
    # milk resolved; two breads tie (ambiguous)
    st.catalog.append(
        [
            Product(provider="instamart", item_id="m1", name="Amul Taaza Milk", price_minor=12000),
            Product(
                provider="instamart",
                item_id="b1",
                name="Harvest Gold Bread",
                price_minor=4500,
                image_url="http://img/hg.png",
                variation="400 g",
            ),
            Product(
                provider="instamart", item_id="b2", name="English Oven Bread", price_minor=5500
            ),
        ]
    )
    return st


def test_disambig_options_shape():
    st = _st_with_catalog()
    refs = {"ambiguous": [{"phrase": "bread", "refs": ["p2", "p3"]}]}
    opts = R.disambig_options(st, "instamart", refs)
    assert len(opts) == 1
    assert opts[0]["phrase"] == "bread"
    names = {c["name"] for c in opts[0]["choices"]}
    assert names == {"Harvest Gold Bread", "English Oven Bread"}
    assert all(c["provider"] == "instamart" and c["ref"] for c in opts[0]["choices"])
    # choices carry image + size so the FE renders them as product cards (not bare text chips)
    hg = next(c for c in opts[0]["choices"] if c["name"] == "Harvest Gold Bread")
    assert hg["image"] == "http://img/hg.png" and hg["size"] == "400 g" and hg["price"] == "₹45"


def test_disambiguation_result_keeps_added_and_asks():
    st = _st_with_catalog()
    # milk already in the cart (the part that resolved + got written)
    st.carts["instamart"] = Cart(
        provider="instamart",
        items=[CartItem(item_id="m1", name="Amul Taaza Milk", qty=1, price_minor=12000)],
    )
    opts = R.disambig_options(
        st, "instamart", {"ambiguous": [{"phrase": "bread", "refs": ["p2", "p3"]}]}
    )
    out = V.disambiguation_result(st, "instamart", ["Amul Taaza Milk"], opts, unresolved=["maggi"])

    assert out["status"] == "ambiguous" and out["error"] is None
    # the resolved item is shown (cart card present), not dropped
    assert out["cards"] and out["cards"][0]["type"] == "cart"
    # the FE gets tappable disambiguation options
    assert out["pending_action"]["type"] == "disambiguate"
    assert out["pending_action"]["options"] == opts
    # the narrator note states EXACTLY the scenario: added X, ask the choice, flag the miss
    note = out["note"]
    assert "Amul Taaza Milk" in note and "add" in note
    assert "Harvest Gold Bread" in note and "English Oven Bread" in note
    assert "maggi" in note
    # per-provider selected address round-trips
    assert out["selected_instamart_address_id"] == "im-addr"


def test_disambiguation_result_no_cart_card_when_nothing_added():
    st = _st_with_catalog()
    st.carts["instamart"] = Cart(provider="instamart")
    opts = R.disambig_options(
        st, "instamart", {"ambiguous": [{"phrase": "bread", "refs": ["p2", "p3"]}]}
    )
    out = V.disambiguation_result(st, "instamart", added=[], options=opts, unresolved=[])
    assert out["cards"] == [] and out["attachments"] == []  # pure ask, no fake cart
    assert out["pending_action"]["options"] == opts
