"""KG-to-cache promotion worker (Tier-2 completion).

Consumes the `companion-lead-promotion` SQS queue. Each `lead_matched` event is
the signal that the leadgen worker connected an open lead to a vendor. This
worker does the two things that should NOT block the matching cycle:

  1. Reflects the matched lead in the DynamoDB profile cache (idempotent append
     to ``matched_leads`` / ``leads_by_category``) so the agent sees the vendor
     connection on the next turn.
  2. Notifies the vendor — gated on the user's consent_status (DPDP): we only
     hand a lead to a vendor when consent is explicitly ``granted``.

Cache-only + notify: it never touches Neo4j, so it stays decoupled from the
graph and can scale independently.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request

import boto3

# Add repo root to sys.path so we can import modules
sys.path.append("/var/www/companion")

import companion_api.settings as settings
from companion_api.profile_cache import ProfileCacheRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("promotion_worker")


def main():
    logger.info("Starting SQS Lead Promotion Worker...")
    queue_url = settings.LEAD_PROMOTION_QUEUE_URL
    if not queue_url:
        logger.error("LEAD_PROMOTION_QUEUE_URL is not configured! Exiting.")
        return

    sqs = boto3.client("sqs", region_name=settings.AWS_REGION)
    cache_repo = ProfileCacheRepository()

    logger.info(f"Long-polling lead promotion queue: {queue_url}")
    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=20,
                VisibilityTimeout=60,
            )
            for msg in response.get("Messages", []):
                receipt_handle = msg["ReceiptHandle"]
                try:
                    body = json.loads(msg["Body"])
                    process_promotion(cache_repo, body)
                except Exception as e:
                    logger.exception(f"Error processing promotion event: {e}")
                # Delete regardless: a poison message must not loop forever (the
                # leadgen worker remains the source of truth via its projection).
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        except Exception as e:
            logger.exception(f"Error in promotion polling loop: {e}")
            time.sleep(5)


def process_promotion(cache_repo: ProfileCacheRepository, event: dict) -> None:
    if event.get("event_type") != "lead_matched":
        logger.info(f"Ignoring unknown promotion event_type={event.get('event_type')}")
        return

    user_id = event.get("user_id")
    lead_id = event.get("lead_id")
    if not user_id or not lead_id:
        logger.warning("Promotion event missing user_id/lead_id; skipping.")
        return

    lead_view = {
        "lead_id": lead_id,
        "category": event.get("category"),
        "product": event.get("product"),
        "status": "matched",
        "vendor_id": event.get("vendor_id"),
        "vendor_name": event.get("vendor_name"),
        "matched_at": event.get("matched_at"),
    }

    profile = cache_repo.get(user_id) or {}
    _append_matched_lead(cache_repo, user_id, profile, lead_view)
    _notify_vendor(profile, event)


def _append_matched_lead(
    cache_repo: ProfileCacheRepository,
    user_id: str,
    profile: dict,
    lead_view: dict,
) -> None:
    """Idempotently reflect the matched lead in the cache (dedup by lead_id)."""
    lead_id = lead_view["lead_id"]
    category = lead_view.get("category") or "other"

    matched = [l for l in (profile.get("matched_leads") or []) if l.get("lead_id") != lead_id]
    matched.append(lead_view)

    by_cat = dict(profile.get("leads_by_category") or {})
    bucket = [l for l in (by_cat.get(category) or []) if l.get("lead_id") != lead_id]
    bucket.append(lead_view)
    by_cat[category] = bucket

    # Drop the now-matched lead from open_leads if it was sitting there.
    open_leads = [l for l in (profile.get("open_leads") or []) if l.get("lead_id") != lead_id]

    cache_repo.update_fields(user_id, {
        "matched_leads": matched,
        "leads_by_category": by_cat,
        "open_leads": open_leads,
    })
    logger.info(f"Promoted matched lead_id={lead_id} into cache for user={user_id}")


def _notify_vendor(profile: dict, event: dict) -> None:
    """Notify the vendor of a matched lead — only when consent is explicitly granted (DPDP)."""
    consent = str(profile.get("consent_status") or "implicit")
    if consent != "granted":
        logger.info(
            f"Consent={consent} for user={event.get('user_id')}; "
            f"recording match but NOT sharing lead {event.get('lead_id')} with vendor."
        )
        return

    webhook = os.getenv("VENDOR_NOTIFY_WEBHOOK_URL", "").strip()
    payload = {
        "lead_id": event.get("lead_id"),
        "category": event.get("category"),
        "product": event.get("product"),
        "vendor_id": event.get("vendor_id"),
        "vendor_name": event.get("vendor_name"),
        "matched_at": event.get("matched_at"),
    }
    if not webhook:
        logger.info(f"Vendor notify (no webhook configured) — would send: {payload}")
        return
    try:
        req = urllib.request.Request(
            webhook,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        logger.info(f"Notified vendor {event.get('vendor_id')} of lead {event.get('lead_id')}")
    except Exception as e:
        logger.error(f"Vendor notification failed for lead {event.get('lead_id')}: {e}")


if __name__ == "__main__":
    main()
