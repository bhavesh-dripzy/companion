"""ONDC onboarding crypto: site-verification signing + /on_subscribe challenge decrypt.

Keys are produced by ``scripts/ondc_keygen.py`` and stored in the
``companion/ondc-keys`` secret: an Ed25519 signing pair (raw, base64) and an
X25519 encryption pair (private raw base64, public DER/SubjectPublicKeyInfo
base64) — the encodings the ONDC registry expects. This module is the read
side used by the live endpoints.

``ONDC_PREPROD_PUBLIC_KEY`` is ONDC's pre-production registry encryption public
key (DER/SPKI, base64); set ``ONDC_PUBLIC_KEY`` in env/secret to point the
challenge decrypt at staging or production instead.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import (
    load_der_private_key,
    load_der_public_key,
)

from companion_api.secrets import load_json_secret

ONDC_PREPROD_PUBLIC_KEY = "MCowBQYDK2VuAyEAa9Wbpvd9SsrpOZFcynyt/TO3x0Yrqyys4NUGIvyxX2Q="

_KEY_FIELDS = (
    "subscriber_id",
    "unique_key_id",
    "request_id",
    "signing_private_key",
    "encryption_private_key",
)


def load_ondc_keys() -> dict[str, str]:
    """Load the ONDC NP key bundle from Secrets Manager (matching env vars win)."""
    secret_id = os.getenv("ONDC_KEYS_SECRET_ID", "companion/ondc-keys")
    keys = dict(load_json_secret(secret_id))
    for name in _KEY_FIELDS:
        override = os.getenv(name.upper())
        if override and override.strip():
            keys[name] = override.strip()
    return keys


def ondc_public_key() -> str:
    """The ONDC registry's encryption public key for the active environment."""
    return os.getenv("ONDC_PUBLIC_KEY", "").strip() or ONDC_PREPROD_PUBLIC_KEY


def _ed25519_private(key_b64: str) -> Ed25519PrivateKey:
    """Load an Ed25519 key from raw-32, nacl-64 (seed||pub) or DER/PKCS8 base64."""
    raw = base64.b64decode(key_b64)
    try:
        loaded = load_der_private_key(raw, password=None)
        if isinstance(loaded, Ed25519PrivateKey):
            return loaded
    except Exception:
        pass
    return Ed25519PrivateKey.from_private_bytes(raw[:32])


def _x25519_private(key_b64: str) -> X25519PrivateKey:
    """Load an X25519 key from raw-32 or DER/PKCS8 base64."""
    raw = base64.b64decode(key_b64)
    try:
        loaded = load_der_private_key(raw, password=None)
        if isinstance(loaded, X25519PrivateKey):
            return loaded
    except Exception:
        pass
    return X25519PrivateKey.from_private_bytes(raw[:32])


def sign_request_id(request_id: str, signing_private_key_b64: str) -> str:
    """Ed25519-sign ``request_id`` for the ondc-site-verification.html meta tag."""
    priv = _ed25519_private(signing_private_key_b64)
    return base64.b64encode(priv.sign(request_id.encode("utf-8"))).decode("ascii")


def decrypt_challenge(challenge_b64: str, encryption_private_key_b64: str) -> str:
    """Decrypt the registry's /on_subscribe challenge (X25519 ECDH -> AES-256-ECB)."""
    priv = _x25519_private(encryption_private_key_b64)
    ondc_pub = load_der_public_key(base64.b64decode(ondc_public_key()))
    shared_key = priv.exchange(ondc_pub)
    decryptor = Cipher(algorithms.AES(shared_key), modes.ECB()).decryptor()
    padded = decryptor.update(base64.b64decode(challenge_b64)) + decryptor.finalize()
    unpadder = sym_padding.PKCS7(algorithms.AES.block_size).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
