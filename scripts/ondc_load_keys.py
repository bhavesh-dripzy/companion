"""Load ONDC portal-generated keys (keys.json) into the companion/ondc-keys secret.

The ONDC NP portal's "generate & download key" produces a keys.json holding the
signing pair (Ed25519, nacl-format private) and encryption pair (X25519,
PKCS8/DER private). This validates each private key actually loads, attaches the
``subscriber_id`` + ``unique_key_id`` shown on the portal form plus a fresh
``request_id``, and writes the bundle so the live /on_subscribe and
/ondc-site-verification.html endpoints sign/decrypt with the same keys the
registry has on record.

Usage::

    AWS_PROFILE=companion python scripts/ondc_load_keys.py \\
        --keys ~/Downloads/keys.json \\
        --subscriber-id ondc.jatayulabs.com \\
        --unique-key-id c4236c2d-5e17-4cfa-9182-1de4476fdd75 \\
        --dry-run

Drop --dry-run to write the secret. Re-running overwrites (rotates) it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from companion_api.ondc_crypto import _ed25519_private, _x25519_private  # noqa: E402

SECRET_ID = os.getenv("ONDC_KEYS_SECRET_ID", "companion/ondc-keys")
REGION = os.getenv("AWS_REGION", "ap-south-1")
_REQUIRED = (
    "signing_private_key",
    "signing_public_key",
    "encryption_private_key",
    "encryption_public_key",
)


def build_bundle(keys_path: str, subscriber_id: str, unique_key_id: str, request_id: str) -> dict[str, str]:
    raw = json.load(open(os.path.expanduser(keys_path)))
    missing = [k for k in _REQUIRED if not raw.get(k)]
    if missing:
        sys.exit(f"keys.json is missing required fields: {missing}")

    _ed25519_private(raw["signing_private_key"]).sign(b"validate")
    _x25519_private(raw["encryption_private_key"]).public_key()

    return {
        "subscriber_id": subscriber_id,
        "unique_key_id": unique_key_id,
        "request_id": request_id,
        **{k: raw[k] for k in _REQUIRED},
    }


def store(bundle: dict[str, str]) -> None:
    client = boto3.client("secretsmanager", region_name=REGION)
    body = json.dumps(bundle, separators=(",", ":"))
    try:
        client.create_secret(Name=SECRET_ID, SecretString=body, Description="ONDC NP keys (portal)")
        print(f"Created secret {SECRET_ID}")
    except client.exceptions.ResourceExistsException:
        client.put_secret_value(SecretId=SECRET_ID, SecretString=body)
        print(f"Updated secret {SECRET_ID}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import ONDC portal keys.json into Secrets Manager")
    parser.add_argument("--keys", default="~/Downloads/keys.json")
    parser.add_argument("--subscriber-id", default="ondc.jatayulabs.com")
    parser.add_argument("--unique-key-id", required=True, help="Unique Key ID shown on the portal form")
    parser.add_argument("--request-id", default="", help="reuse an existing request_id (else a new one is minted)")
    parser.add_argument("--dry-run", action="store_true", help="print the bundle (private keys redacted), do not write")
    args = parser.parse_args()

    request_id = args.request_id.strip() or str(uuid.uuid4())
    bundle = build_bundle(args.keys, args.subscriber_id, args.unique_key_id, request_id)

    redacted = {k: (v if "private" not in k else f"<{len(v)} chars hidden>") for k, v in bundle.items()}
    print(json.dumps(redacted, indent=2))

    if args.dry_run:
        print("\n[dry-run] secret NOT written. Re-run without --dry-run to store.")
        return
    store(bundle)
    print(f"\nrequest_id (used by site-verification + /subscribe): {request_id}")


if __name__ == "__main__":
    main()
