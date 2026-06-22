"""Tests for the Beckn context builder and the signed action sender."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import companion_api.ondc_client as client
from companion_api.ondc_context import bap_identity, new_context


def test_search_context_omits_bpp():
    ctx = new_context(
        "search", domain="ONDC:RET10", bap_id="ondc.jatayulabs.com", bap_uri="https://ondc.jatayulabs.com/ondc"
    )
    assert ctx["action"] == "search"
    assert ctx["domain"] == "ONDC:RET10"
    assert "bpp_id" not in ctx and "bpp_uri" not in ctx
    assert ctx["transaction_id"] and ctx["message_id"]
    assert ctx["timestamp"].endswith("Z")


def test_directed_context_sets_bpp_and_reuses_transaction():
    ctx = new_context(
        "select", domain="ONDC:RET10", bap_id="b", bap_uri="u",
        transaction_id="T-1", bpp_id="seller.example.com", bpp_uri="https://seller.example.com",
    )
    assert ctx["bpp_id"] == "seller.example.com"
    assert ctx["transaction_id"] == "T-1"


def test_bap_identity_derives_uri_from_subscriber(monkeypatch):
    monkeypatch.delenv("ONDC_BAP_URI", raising=False)
    sub, uri = bap_identity({"subscriber_id": "ondc.jatayulabs.com"})
    assert sub == "ondc.jatayulabs.com"
    assert uri == "https://ondc.jatayulabs.com/ondc"


def test_send_action_search_signs_and_targets_gateway(monkeypatch):
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    keys = {
        "subscriber_id": "ondc.jatayulabs.com",
        "unique_key_id": "k1",
        "signing_private_key": base64.b64encode(seed).decode(),
    }
    monkeypatch.setattr(client, "load_ondc_keys", lambda: keys)
    monkeypatch.delenv("ONDC_BAP_URI", raising=False)

    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"message": {"ack": {"status": "ACK"}}}

    monkeypatch.setattr(
        client.requests,
        "post",
        lambda url, data=None, headers=None, timeout=None: (
            captured.update(url=url, headers=headers, data=data) or _Resp()
        ),
    )

    out = client.send_action("search", {"intent": {}}, domain="ONDC:RET10")
    assert captured["url"] == "https://preprod.gateway.ondc.org/search"
    assert captured["headers"]["Authorization"].startswith('Signature keyId="ondc.jatayulabs.com|k1|ed25519"')
    assert out["status_code"] == 200
    assert out["context"]["domain"] == "ONDC:RET10"
