"""FE-selected address override (sidebar sync) — _apply_address_ids."""

from __future__ import annotations

from core.agent.pipeline import _apply_address_ids
from core.agent.state import ConversationState


def test_sets_provider_session_per_provider():
    st = ConversationState(user_id="u", chat_id="c")
    _apply_address_ids(st, {"instamart": "addr1", "zepto": "addr2"})
    assert st.provider_session["instamart"]["address_id"] == "addr1"
    assert st.provider_session["zepto"]["address_id"] == "addr2"


def test_change_clears_stale_geo():
    st = ConversationState(user_id="u", chat_id="c")
    st.provider_session["zepto"] = {"address_id": "old", "lat": 1.0, "lng": 2.0}
    _apply_address_ids(st, {"zepto": "new"})
    ps = st.provider_session["zepto"]
    assert ps["address_id"] == "new" and "lat" not in ps and "lng" not in ps


def test_same_address_is_noop():
    st = ConversationState(user_id="u", chat_id="c")
    st.provider_session["instamart"] = {"address_id": "a", "lat": 1.0}
    _apply_address_ids(st, {"instamart": "a"})
    assert st.provider_session["instamart"]["lat"] == 1.0  # untouched


def test_none_and_empty_are_safe():
    st = ConversationState(user_id="u", chat_id="c")
    _apply_address_ids(st, None)
    _apply_address_ids(st, {"instamart": ""})
    assert st.provider_session == {}
