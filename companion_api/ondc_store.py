"""Persist inbound ONDC callbacks so async on_* responses can be correlated.

Writes one row per transaction+message to the DynamoDB table named by
``ONDC_TXN_TABLE`` when set; otherwise logs only (fine for early bring-up, where
ACK-ing the callback is what matters). The row lets the BAP later read back the
on_* responses that correspond to an action it sent.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3

logger = logging.getLogger(__name__)

TABLE_NAME = os.getenv("ONDC_TXN_TABLE", "")
_TTL_SECONDS = 86400


def record_callback(action: str, body: dict[str, Any], *, verified: bool) -> None:
    """Log and (if a table is configured) persist an inbound callback."""
    context = body.get("context") or {} if isinstance(body, dict) else {}
    transaction_id = context.get("transaction_id") or ""
    message_id = context.get("message_id") or ""
    logger.info(
        "ONDC callback action=%s txn=%s msg=%s verified=%s", action, transaction_id, message_id, verified
    )
    if not TABLE_NAME:
        return
    now = int(time.time())
    try:
        boto3.resource("dynamodb").Table(TABLE_NAME).put_item(
            Item={
                "transaction_id": transaction_id or message_id or "unknown",
                "action_message": f"{action}#{message_id or now}",
                "action": action,
                "message_id": message_id,
                "verified": verified,
                "body": json.dumps(body, separators=(",", ":")),
                "received_at": now,
                "expires_at": now + _TTL_SECONDS,
            }
        )
    except Exception:
        logger.exception("failed to persist ONDC callback")


def save_transaction(transaction_id: str, **state: Any) -> None:
    """Persist per-transaction state (domain, bpp routing, selection) for the flow.

    Stored under sort key ``state`` alongside the callback rows for the same
    transaction. No-op (logs) when no table is configured."""
    logger.info("ONDC transaction state txn=%s keys=%s", transaction_id, list(state))
    if not TABLE_NAME or not transaction_id:
        return
    now = int(time.time())
    try:
        boto3.resource("dynamodb").Table(TABLE_NAME).put_item(
            Item={
                "transaction_id": transaction_id,
                "action_message": "state",
                "state": json.dumps(state, separators=(",", ":")),
                "updated_at": now,
                "expires_at": now + _TTL_SECONDS,
            }
        )
    except Exception:
        logger.exception("failed to persist ONDC transaction state")


def get_transaction(transaction_id: str) -> dict[str, Any]:
    """Load per-transaction state saved by ``save_transaction`` ({} if absent)."""
    if not TABLE_NAME or not transaction_id:
        return {}
    try:
        item = (
            boto3.resource("dynamodb")
            .Table(TABLE_NAME)
            .get_item(Key={"transaction_id": transaction_id, "action_message": "state"})
            .get("Item")
        )
    except Exception:
        logger.exception("failed to load ONDC transaction state")
        return {}
    return json.loads(item["state"]) if item and item.get("state") else {}
