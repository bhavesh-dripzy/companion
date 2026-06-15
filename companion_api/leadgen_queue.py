from __future__ import annotations
import json
import logging
import boto3
from companion_api import settings
from companion_api.dynamodb import utc_now_iso

logger = logging.getLogger(__name__)


def get_sqs_client():
    return boto3.client("sqs", region_name=settings.AWS_REGION)


def send_turn_event(
    *,
    user_id: str,
    message_id: str,
    user_message: str,
    assistant_message: str,
    task_type: str | None = None,
    routed_provider: str | None = None,
    session_addresses: list | None = None,
    user_geo: dict | None = None,
    phone: str | None = None,
    member_id: str | None = None,
) -> str | None:
    """Enqueues a turn event to SQS queue for async knowledge graph extraction."""
    if not settings.LEADGEN_QUEUE_URL:
        logger.warning("LEADGEN_QUEUE_URL is not set. Skipping turn event enqueue.")
        return None

    event = {
        "canonical_user_id": user_id,
        "turn_id": message_id,
        "user_message": user_message,
        "assistant_message": assistant_message,
        "task_type": task_type,
        "routed_provider": routed_provider,
        "session_addresses": session_addresses,
        "geo": user_geo,
        "phone": phone,
        "member_id": member_id,
        "ts": utc_now_iso(),
    }

    try:
        sqs = get_sqs_client()
        response = sqs.send_message(
            QueueUrl=settings.LEADGEN_QUEUE_URL,
            MessageBody=json.dumps(event),
        )
        return response.get("MessageId")
    except Exception as e:
        logger.exception(f"Failed to send leadgen turn event to SQS: {e}")
        return None


def send_lead_promotion(event: dict) -> str | None:
    """Enqueues a KG-to-cache promotion event (a matched lead) for the promotion worker."""
    if not settings.LEAD_PROMOTION_QUEUE_URL:
        logger.warning("LEAD_PROMOTION_QUEUE_URL is not set. Skipping promotion event enqueue.")
        return None
    try:
        sqs = get_sqs_client()
        response = sqs.send_message(
            QueueUrl=settings.LEAD_PROMOTION_QUEUE_URL,
            MessageBody=json.dumps(event),
        )
        return response.get("MessageId")
    except Exception as e:
        logger.exception(f"Failed to send lead promotion event to SQS: {e}")
        return None
