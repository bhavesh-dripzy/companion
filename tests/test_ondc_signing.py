"""Tests for Beckn/ONDC request signing + verification."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from companion_api.ondc_signing import (
    blake512_b64,
    build_authorization,
    key_id_parts,
    minify,
    signing_string,
    verify_authorization,
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _keypair():
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return seed, pub


def test_signing_string_exact_format():
    assert signing_string("DGST", 100, 200) == "(created): 100\n(expires): 200\ndigest: BLAKE-512=DGST"


def test_blake512_is_64_bytes():
    assert len(base64.b64decode(blake512_b64("hello"))) == 64


def test_sign_verify_roundtrip_raw_key():
    seed, pub = _keypair()
    body = {"context": {"action": "search", "transaction_id": "t1"}, "message": {"intent": {}}}
    header = build_authorization(
        body, subscriber_id="ondc.jatayulabs.com", unique_key_id="k1", signing_private_key_b64=_b64(seed)
    )
    assert verify_authorization(header, minify(body), _b64(pub))
    assert key_id_parts(header) == ("ondc.jatayulabs.com", "k1")


def test_sign_verify_roundtrip_nacl_key():
    seed, pub = _keypair()
    header = build_authorization(
        {"a": 1}, subscriber_id="s", unique_key_id="k", signing_private_key_b64=_b64(seed + pub)
    )
    assert verify_authorization(header, minify({"a": 1}), _b64(pub))


def test_verify_rejects_tampered_body():
    seed, pub = _keypair()
    header = build_authorization(
        {"amount": 100}, subscriber_id="s", unique_key_id="k", signing_private_key_b64=_b64(seed)
    )
    assert not verify_authorization(header, minify({"amount": 999}), _b64(pub))


def test_verify_rejects_expired():
    seed, pub = _keypair()
    header = build_authorization(
        {"a": 1}, subscriber_id="s", unique_key_id="k", signing_private_key_b64=_b64(seed),
        created=1000, expires=2000,
    )
    assert not verify_authorization(header, minify({"a": 1}), _b64(pub), now=999999)
