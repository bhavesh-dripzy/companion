"""The narrator must echo the REAL cart (the one the UI shows), not recompute or recall totals from
history. `_for_narrator` injects a deterministic `cart` summary (count + total) from the result's
cart card, and surfaces the action (incl. clear) so the narrator confirms it confidently.
"""

from __future__ import annotations

from core.agent.pipeline import _for_narrator


def _cart_card(items, total, empty=False):
    return {"type": "cart", "provider": "zepto", "items": items, "total": total, "empty": empty}


def test_cart_summary_uses_real_count_and_total():
    # the regression: cart actually has 1 item @ ₹86, narrator must NOT say 2 / ₹112
    result = {
        "status": "ok",
        "cards": [_cart_card([{"name": "Frubon HiLo", "qty": 1, "price": "₹56"}], "₹86")],
        "domain": "grocery",
    }
    out = _for_narrator({"action": "add", "domain": "grocery"}, result, "add frubon")
    assert out["cart"] == {"provider": "zepto", "item_count": 1, "total": "₹86", "empty": False}
    assert out["action"] == "add"


def test_cart_summary_sums_quantities():
    result = {
        "status": "ok",
        "cards": [_cart_card([{"name": "Milk", "qty": 2}, {"name": "Bread", "qty": 1}], "₹150")],
        "domain": "grocery",
    }
    out = _for_narrator({"action": "set_qty", "domain": "grocery"}, result, "")
    assert out["cart"]["item_count"] == 3 and out["cart"]["total"] == "₹150"


def test_clear_action_reaches_narrator_with_empty_cart():
    # a clear is a tap (empty message) → narrator still gets action=clear + empty cart for a confident confirm
    result = {"status": "ok", "cards": [_cart_card([], "₹0", empty=True)], "domain": "grocery"}
    out = _for_narrator({"action": "clear", "domain": "grocery"}, result, "")
    assert out["action"] == "clear"
    assert out["cart"]["empty"] is True and out["cart"]["item_count"] == 0


def test_no_cart_field_when_no_cart_card():
    result = {"status": "ok", "cards": [{"type": "product_list", "items": []}], "domain": "grocery"}
    out = _for_narrator({"action": "search", "domain": "grocery"}, result, "")
    assert "cart" not in out
