"""The agent address bridge delegates to the canonical per-provider default-address stores (the SAME
record the Apps modal writes), and the pipeline hydrates / persists through it. We monkeypatch the
companion_api stores so the test never touches DynamoDB or the local-file fallback."""

from __future__ import annotations

import companion_api.instamart_address as ia
import companion_api.zepto_address as za
from core.agent import address_store
from core.agent.pipeline import _apply_address_ids, _hydrate_addresses
from core.agent.state import ConversationState


def test_get_reads_canonical_store(monkeypatch):
    monkeypatch.setattr(
        ia, "get_default_instamart_address", lambda u: {"address_id": "i1", "tag": "Home", "line": "x"}
    )
    monkeypatch.setattr(za, "get_default_zepto_address", lambda u: None)
    assert address_store.get("u1") == {"instamart": {"address_id": "i1", "label": "Home · x"}}
    assert address_store.get_one("u1", "instamart") == {"address_id": "i1", "label": "Home · x"}


def test_set_writes_canonical_store_with_split_label(monkeypatch):
    saved: dict = {}

    def fake_save(user_id, *, address_id, tag=None, line=None):
        saved.update(user_id=user_id, address_id=address_id, tag=tag, line=line)
        return {}

    monkeypatch.setattr(ia, "save_default_instamart_address", fake_save)
    address_store.set("u2", "instamart", {"address_id": "i9", "label": "Office · 5th street"})
    assert saved == {"user_id": "u2", "address_id": "i9", "tag": "Office", "line": "5th street"}


def test_set_ignores_blank(monkeypatch):
    called = []
    monkeypatch.setattr(
        ia, "save_default_instamart_address", lambda *a, **k: called.append(1) or {}
    )
    address_store.set("u2", "instamart", {"label": "no id"})
    assert called == []


def test_hydrate_seeds_provider_session(monkeypatch):
    monkeypatch.setattr(ia, "get_default_instamart_address", lambda u: None)
    monkeypatch.setattr(
        za, "get_default_zepto_address", lambda u: {"address_id": "z3", "tag": "ghar", "line": None}
    )
    state = ConversationState(user_id="u3", chat_id="c1")
    _hydrate_addresses(state)
    assert state.provider_session["zepto"]["address_id"] == "z3"
    assert state.provider_session["zepto"]["label"] == "ghar"


def test_fe_override_persists_to_canonical_store(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        ia,
        "save_default_instamart_address",
        lambda u, *, address_id, tag=None, line=None: calls.append(address_id) or {},
    )
    state = ConversationState(user_id="u4", chat_id="c1")
    _apply_address_ids(state, {"instamart": "iX"})
    assert state.provider_session["instamart"]["address_id"] == "iX"
    assert calls == ["iX"]
