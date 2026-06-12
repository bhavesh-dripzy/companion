"""Generate the ONDC Network Participant key pairs and store them in Secrets Manager.

ONDC onboarding needs two key pairs (see ONDC registry onboarding docs):

  * Signing key pair  — Ed25519. Used to sign every Beckn request (the
    ``Authorization`` / ``X-Gateway-Authorization`` headers) and the
    ``request_id`` in the site-verification file. ONDC wants the **raw**
    32-byte public key, base64 (standard) encoded.
  * Encryption key pair — X25519 (Curve25519). Used to derive a shared
    secret with ONDC's public encryption key (ECDH) to decrypt the
    ``/on_subscribe`` challenge. ONDC wants the public key in **DER /
    SubjectPublicKeyInfo** form, base64 encoded.

The private halves never leave this script except to land in the
``companion/ondc-keys`` secret. Only the public halves (safe to share) are
printed, for pasting into the ONDC Network Participant portal.

Usage::

    AWS_PROFILE=companion python scripts/ondc_keygen.py            # create
    AWS_PROFILE=companion python scripts/ondc_keygen.py --update   # rotate

By default it refuses to overwrite an existing secret; pass ``--update`` to
rotate (the old keys are replaced — re-register the new public keys with ONDC
afterwards).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys

import boto3
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

SECRET_ID = "companion/ondc-keys"
REGION = os.getenv("AWS_REGION", "ap-south-1")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def generate_keys(*, subscriber_id: str, unique_key_id: str) -> dict[str, str]:
    # --- Signing key pair (Ed25519) ---
    sign_priv = Ed25519PrivateKey.generate()
    sign_priv_raw = sign_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    sign_pub_raw = sign_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # --- Encryption key pair (X25519) ---
    enc_priv = X25519PrivateKey.generate()
    enc_priv_raw = enc_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # ONDC wants the encryption public key in DER (SubjectPublicKeyInfo).
    enc_pub_der = enc_priv.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    return {
        "subscriber_id": subscriber_id,
        "unique_key_id": unique_key_id,
        # private halves — kept secret
        "signing_private_key": _b64(sign_priv_raw),
        "encryption_private_key": _b64(enc_priv_raw),
        # public halves — registered with ONDC
        "signing_public_key": _b64(sign_pub_raw),
        "encryption_public_key": _b64(enc_pub_der),
    }


def store(payload: dict[str, str], *, update: bool) -> None:
    client = boto3.client("secretsmanager", region_name=REGION)
    body = json.dumps(payload, separators=(",", ":"))
    try:
        client.create_secret(
            Name=SECRET_ID,
            SecretString=body,
            Description="ONDC BAP signing (Ed25519) + encryption (X25519) keys",
        )
        print(f"Created secret {SECRET_ID}")
    except client.exceptions.ResourceExistsException:
        if not update:
            print(
                f"Secret {SECRET_ID} already exists. Re-run with --update to rotate.",
                file=sys.stderr,
            )
            sys.exit(1)
        client.put_secret_value(SecretId=SECRET_ID, SecretString=body)
        print(f"Updated secret {SECRET_ID} (keys rotated)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate + store ONDC NP keys")
    parser.add_argument("--subscriber-id", default="ondc.houzzat.in")
    parser.add_argument("--unique-key-id", default="companion-key-1")
    parser.add_argument("--update", action="store_true", help="rotate existing keys")
    parser.add_argument(
        "--print-private",
        action="store_true",
        help="also print private keys (avoid; for emergency recovery only)",
    )
    args = parser.parse_args()

    payload = generate_keys(
        subscriber_id=args.subscriber_id, unique_key_id=args.unique_key_id
    )
    store(payload, update=args.update)

    print("\n=== Register these PUBLIC values on the ONDC NP portal ===")
    print(f"subscriber_id          : {payload['subscriber_id']}")
    print(f"unique_key_id          : {payload['unique_key_id']}")
    print(f"signing_public_key     : {payload['signing_public_key']}")
    print(f"encryption_public_key  : {payload['encryption_public_key']}")
    if args.print_private:
        print("\n=== PRIVATE (do not share) ===")
        print(f"signing_private_key    : {payload['signing_private_key']}")
        print(f"encryption_private_key : {payload['encryption_private_key']}")


if __name__ == "__main__":
    main()
