"""FIND vs ASK classification: resolution is pure (no re-search), so `unresolved` reliably means
'not in the current catalog' — which the add handler turns into a fresh search, while an in-context
match becomes resolved/ambiguous via the deterministic net. Also covers the not_found helper.
"""

from __future__ import annotations

from core.agent.contracts import Cart, CartItem
from core.agent.domains import grocery_resolve as R
from core.agent.domains import grocery_view as V
from core.agent.state import ConversationState

# compact projection shape the resolver/net operate on
_BREADS = [
    {
        "ref": "p1",
        "name": "Harvest Gold Bread",
        "brand": "Harvest Gold",
        "variation": "400g",
        "price": 45,
        "in_cart": False,
    },
    {
        "ref": "p2",
        "name": "English Oven Bread",
        "brand": "English Oven",
        "variation": "350g",
        "price": 55,
        "in_cart": False,
    },
]


def _empty():
    return {"resolved": [], "ambiguous": [], "unresolved": []}


def test_net_passthrough_when_llm_resolved():
    refs = {"resolved": [{"ref": "p1", "qty": 1}], "ambiguous": [], "unresolved": []}
    out = R._net(refs, _BREADS, [{"phrase": "harvest gold bread", "qty": 1}])
    assert out["resolved"] == [{"ref": "p1", "qty": 1}] and not out["unresolved"]


def test_net_empty_llm_falls_back_to_name_overlap():
    # LLM returned nothing → deterministic fallback classifies "bread" as an in-context tie
    out = R._net(_empty(), _BREADS, [{"phrase": "bread", "qty": 1}])
    assert out["resolved"] == [] and not out["unresolved"]
    assert out["ambiguous"] and set(out["ambiguous"][0]["refs"]) == {"p1", "p2"}


def test_net_in_context_unresolved_is_recovered_not_dropped():
    # LLM punted on a phrase that IS in the catalog → net recovers it (ASK), not left unresolved
    refs = {"resolved": [], "ambiguous": [], "unresolved": ["bread"]}
    out = R._net(refs, _BREADS, [{"phrase": "bread", "qty": 1}])
    assert out["unresolved"] == [] and out["ambiguous"]


def test_net_truly_absent_phrase_stays_unresolved_for_FIND():
    # "chips" has no catalog match → stays unresolved → the add handler will treat it as a search
    refs = {"resolved": [], "ambiguous": [], "unresolved": ["chips"]}
    out = R._net(refs, _BREADS, [{"phrase": "chips", "qty": 1}])
    assert out["unresolved"] == ["chips"] and not out["resolved"] and not out["ambiguous"]


def test_net_mixed_keeps_resolved_and_recovers_unresolved():
    refs = {"resolved": [{"ref": "p1", "qty": 1}], "ambiguous": [], "unresolved": ["chips"]}
    out = R._net(
        refs, _BREADS, [{"phrase": "harvest gold bread", "qty": 1}, {"phrase": "chips", "qty": 1}]
    )
    assert {"ref": "p1", "qty": 1} in out["resolved"]
    assert out["unresolved"] == ["chips"]  # absent phrase preserved for FIND


def test_not_found_helper_shape():
    st = ConversationState(user_id="u", chat_id="c")
    st.provider_session["instamart"] = {"address_id": "im-addr"}
    st.carts["instamart"] = Cart(
        provider="instamart", items=[CartItem(item_id="a", name="Milk", qty=1)]
    )
    out = V.not_found(st, "instamart", "'ferrari' aapke cart mein nahi mila.")
    assert out["status"] == "not_found" and out["error"] == "STALE_ITEM"
    assert "ferrari" in out["note"]
    assert not out.get("pending_action")  # never an empty disambiguation picker
    assert out["cards"] and out["cards"][0]["type"] == "cart"  # shows the live cart
    assert out["selected_instamart_address_id"] == "im-addr"
