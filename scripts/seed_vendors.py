#!/usr/bin/env python3
"""Seed / update Vendor nodes in the knowledge graph via the graph service.

Reads a JSON array of vendors (default: scripts/vendors_seed.json) and POSTs each
to the graph service `POST /vendors` endpoint. This is the ingestion path lead
matching depends on — without vendors, `match_leads` has nothing to match against.

Auth: the graph service bearer token is read from GRAPH_SERVICE_TOKEN, or fetched
from Secrets Manager ({PROJECT_NAME}/graph-service-token) if that env is unset.

Usage:
  GRAPH_SERVICE_URL=https://graph.jatayulabs.com python scripts/seed_vendors.py
  python scripts/seed_vendors.py path/to/vendors.json
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def _resolve_token() -> str:
    token = os.getenv("GRAPH_SERVICE_TOKEN", "").strip()
    if token:
        return token
    import boto3

    project = os.getenv("PROJECT_NAME", "companion")
    region = os.getenv("AWS_REGION", "ap-south-1")
    client = boto3.client("secretsmanager", region_name=region)
    resp = client.get_secret_value(SecretId=f"{project}/graph-service-token")
    return json.loads(resp["SecretString"])["token"]


def _post_vendor(base_url: str, token: str, vendor: dict) -> None:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/vendors",
        data=json.dumps(vendor).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")
    print(f"  ✓ {vendor.get('id')} ({vendor.get('category')}) -> {body}")


def main() -> int:
    seed_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "vendors_seed.json"
    )
    base_url = os.getenv("GRAPH_SERVICE_URL", "https://graph.jatayulabs.com")

    with open(seed_path) as f:
        vendors = json.load(f)
    if not isinstance(vendors, list):
        print("Seed file must contain a JSON array of vendors.", file=sys.stderr)
        return 1

    token = _resolve_token()
    print(f"Seeding {len(vendors)} vendor(s) to {base_url} from {seed_path}")
    failures = 0
    for vendor in vendors:
        try:
            _post_vendor(base_url, token, vendor)
        except Exception as exc:
            failures += 1
            print(f"  ✗ {vendor.get('id')}: {exc}", file=sys.stderr)
    print(f"Done. {len(vendors) - failures} succeeded, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
