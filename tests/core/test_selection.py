"""NL entity switch commits a saved address by its tag; no false positives."""

from core.contracts.selectable import Selectable
from core.middleware.selection import resolve_selections

_SEL = [
    Selectable(
        kind="delivery_address",
        saved_key="session_instamart_addresses",
        committed_key="selected_instamart_address_id",
        id_field="address_id",
        label_fields=("addressTag", "label"),
    )
]

_ROWS = {
    "session_instamart_addresses": [
        {"address_id": "275412200", "addressTag": "Work", "label": "Work — H1 Hostel"},
        {"address_id": "360938440", "addressTag": "Room", "label": "Room — Crown Plaza"},
        {"address_id": "d8lr39c", "addressTag": "Holiday", "label": "Holiday — Holiday Inn Gurgaon"},
    ]
}


def test_switch_to_named_address_commits_it():
    out = resolve_selections("switch my instamart delivery address to Work", _ROWS, _SEL)
    assert out == {"selected_instamart_address_id": "275412200"}


def test_deliver_to_named_address_commits_it():
    out = resolve_selections("find milk delivered to my Holiday Inn Gurgaon address", _ROWS, _SEL)
    assert out == {"selected_instamart_address_id": "d8lr39c"}


def test_no_switch_cue_means_no_commit():
    # "work" appears but there's no switch verb — must not hijack the turn.
    out = resolve_selections("what's the weather like at work today", _ROWS, _SEL)
    assert out == {}


def test_switch_to_unknown_label_no_commit():
    out = resolve_selections("switch my delivery address to my farmhouse", _ROWS, _SEL)
    assert out == {}


def test_no_saved_rows_no_commit():
    out = resolve_selections("switch to Work", {}, _SEL)
    assert out == {}
