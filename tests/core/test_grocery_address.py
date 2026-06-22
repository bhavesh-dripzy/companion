"""switch_context: address matching + the Instamart cart-poison guard (deterministic, no live MCP).

The poison guard is safety-critical: a non-serviceable Instamart switch must NOT change the address
and must re-pin the old one to un-poison the cart. We assert that with a stub adapter.
"""

from __future__ import annotations

import asyncio

import pytest

from core.agent.contracts import Cart, CartItem, CartResult, ErrorKind
from core.agent.domains import grocery_address as A
from core.agent.state import ConversationState


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    async def _empty(system, payload):
        return {}

    monkeypatch.setattr("core.agent.llm.roles._complete", _empty)


ADDRS = [
    {"id": "a1", "label": "ghar · Canal House, Civil Lines, Delhi", "lat": 28.6, "lng": 77.2},
    {"id": "a2", "label": "vansh · CHD Avenue, Sector 72, Gurugram", "lat": 28.4, "lng": 77.0},
]


def test_match_by_label_token():
    assert asyncio.run(A._match(ADDRS, "ghar"))["id"] == "a1"
    assert asyncio.run(A._match(ADDRS, "gurugram"))["id"] == "a2"


def test_match_unknown_returns_none():
    assert asyncio.run(A._match(ADDRS, "Star Residency")) is None


class _StubAdapter:
    """Records write_cart calls; fails the first (probe) when `serviceable_new` is False."""

    def __init__(self, serviceable_new: bool):
        self.serviceable_new = serviceable_new
        self.writes: list[str] = []

    async def write_cart(self, s, items, ctx, *, replace):
        self.writes.append(ctx.address_id)
        if ctx.address_id == "NEW" and not self.serviceable_new:
            return CartResult.failure(ErrorKind.NOT_SERVICEABLE, "not serviceable")
        return CartResult.success(Cart(provider="instamart", items=list(_one_item())))


def _one_item():
    return [CartItem(item_id="sp1", name="Milk", qty=1, price_minor=5000)]


def _state_with_cart():
    st = ConversationState(user_id="u", chat_id="c")
    st.carts["instamart"] = Cart(provider="instamart", items=_one_item())
    return st


def _ctx(addr):
    from core.agent.providers.base import ExecCtx

    return ExecCtx(user_id="u", address_id=addr)


def test_probe_non_serviceable_refuses_and_repins_old():
    st = _state_with_cart()
    stub = _StubAdapter(serviceable_new=False)
    ok = asyncio.run(A._probe_move(stub, None, st, "instamart", _ctx("NEW"), "OLD"))
    assert ok is False  # not serviceable → don't switch
    # probed the new address, then re-pinned the OLD one to un-poison
    assert stub.writes == ["NEW", "OLD"]


def test_probe_serviceable_moves_cart():
    st = _state_with_cart()
    stub = _StubAdapter(serviceable_new=True)
    ok = asyncio.run(A._probe_move(stub, None, st, "instamart", _ctx("NEW"), "OLD"))
    assert ok is True and stub.writes == ["NEW"]  # moved, no recovery needed


def test_probe_empty_cart_is_noop_ok():
    st = ConversationState(user_id="u", chat_id="c")  # empty cart
    stub = _StubAdapter(serviceable_new=False)
    ok = asyncio.run(A._probe_move(stub, None, st, "instamart", _ctx("NEW"), "OLD"))
    assert ok is True and stub.writes == []  # nothing to poison → no write at all
