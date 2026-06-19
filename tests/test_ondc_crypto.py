"""Round-trip tests for ONDC onboarding crypto.

Proves the site-verification signature and the /on_subscribe challenge decrypt
work for BOTH key encodings we see in practice: the raw format produced by
scripts/ondc_keygen.py and the nacl/PKCS8 format downloaded from the ONDC
Network Participant portal (keys.json).
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from companion_api.ondc_crypto import _x25519_private, decrypt_challenge, sign_request_id


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def test_sign_request_id_raw_and_nacl_agree():
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    request_id = "0c29d1be-3322-11ed-a261-0242ac120002"

    sig_raw = base64.b64decode(sign_request_id(request_id, _b64(seed)))
    sig_nacl = base64.b64decode(sign_request_id(request_id, _b64(seed + pub)))

    priv.public_key().verify(sig_raw, request_id.encode())
    priv.public_key().verify(sig_nacl, request_id.encode())
    assert sig_raw == sig_nacl


def _encrypt_like_registry(shared_key: bytes, plaintext: str) -> str:
    padder = sym_padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(shared_key), modes.ECB()).encryptor()
    return _b64(enc.update(padded) + enc.finalize())


def _assert_challenge_roundtrip(np_private_b64: str, monkeypatch):
    registry_priv = X25519PrivateKey.generate()
    registry_pub_der = registry_priv.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("ONDC_PUBLIC_KEY", _b64(registry_pub_der))

    np_pub = _x25519_private(np_private_b64).public_key()
    shared = registry_priv.exchange(np_pub)
    challenge = _encrypt_like_registry(shared, "answer-xyz-42")

    assert decrypt_challenge(challenge, np_private_b64) == "answer-xyz-42"


def test_decrypt_challenge_raw_private(monkeypatch):
    priv = X25519PrivateKey.generate()
    raw = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    _assert_challenge_roundtrip(_b64(raw), monkeypatch)


def test_decrypt_challenge_pkcs8_private(monkeypatch):
    priv = X25519PrivateKey.generate()
    der = priv.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _assert_challenge_roundtrip(_b64(der), monkeypatch)
