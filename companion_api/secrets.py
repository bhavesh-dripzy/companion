from __future__ import annotations

import json
import logging
import os
from functools import lru_cache

import boto3

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def load_json_secret(secret_id: str) -> dict[str, str]:
    if not secret_id:
        return {}
    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_id)
        raw = response.get("SecretString") or "{}"
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if v is not None}
    except Exception as exc:
        logger.warning("Could not load secret %s: %s", secret_id, exc)
    return {}


def app_secret() -> dict[str, str]:
    return load_json_secret(os.getenv("APP_SECRET_ID", "").strip())


def secret_or_env(name: str, default: str = "") -> str:
    return (os.getenv(name) or app_secret().get(name) or default).strip()
