"""Deterministic Zepto address-by-tag resolution (prod test finding F12)."""

import pytest

from core.providers.plugins.zepto.address_resolve import resolve_address_id

pytestmark = pytest.mark.component

_ADDRS = [
    {"address_id": "A-OFFICE", "label": "office", "tag": "office"},
    {"address_id": "A-HOME", "label": "home", "tag": "home"},
    {"address_id": "A-GHAR", "label": "ghar", "tag": "ghar"},
]


def _session():
    return {"session_zepto_addresses": _ADDRS}


def test_resolves_fake_placeholder_arg_by_label():
    # F12: planner emits "office_address_id" -> resolve to the real office id.
    assert resolve_address_id("office_address_id", "change to office", _session()) == "A-OFFICE"


def test_resolves_from_message_when_arg_useless():
    assert resolve_address_id("__ADDRESS_ID__", "deliver to my home instead", _session()) == "A-HOME"


def test_real_id_passes_through():
    assert resolve_address_id("A-GHAR", "whatever", _session()) == "A-GHAR"


def test_unknown_label_returns_none():
    assert resolve_address_id("mars_address_id", "deliver to mars", _session()) is None


def test_ambiguous_match_returns_none():
    addrs = [
        {"address_id": "A1", "label": "Other"},
        {"address_id": "A2", "label": "Other"},
    ]
    session = {"session_zepto_addresses": addrs}
    assert resolve_address_id("other", "use other", session) is None
