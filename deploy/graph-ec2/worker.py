from __future__ import annotations
import os
import sys
import time
import json
import logging
import boto3

# Add repo root to sys.path so we can import modules
sys.path.append("/var/www/companion")

import companion_api.settings as settings
from companion_api.dynamodb import utc_now_iso
from companion_api.messages_repo import MessageRepository
from companion_api.profile_cache import ProfileCacheRepository
from companion_api.lead_cache_repo import LeadCacheRepository
from companion_api.leadgen_queue import send_lead_promotion
from leadgen.graph_client import GraphClient
from leadgen.extractor import extract_from_conversation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("leadgen_worker")


def load_secrets():
    project_name = os.getenv("PROJECT_NAME", "companion")
    region = os.getenv("AWS_REGION", "ap-south-1")
    client = boto3.client("secretsmanager", region_name=region)
    try:
        # Load app secrets (LLM keys, etc.)
        res = client.get_secret_value(SecretId=f"{project_name}/app")
        secrets = json.loads(res["SecretString"])
        for k, v in secrets.items():
            os.environ[k] = str(v)

        # Load neo4j secrets
        res_neo4j = client.get_secret_value(SecretId=f"{project_name}/neo4j-key")
        neo4j_secrets = json.loads(res_neo4j["SecretString"])
        os.environ["NEO4J_PASSWORD"] = neo4j_secrets["password"]
        os.environ["NEO4J_USERNAME"] = neo4j_secrets.get("username", "neo4j")
        logger.info("Successfully loaded Secrets Manager credentials")
    except Exception as e:
        logger.error(f"Error loading secrets: {e}")


def main():
    logger.info("Starting SQS Leadgen Worker...")
    load_secrets()

    # Initialize graph client
    graph_client = GraphClient()
    logger.info("Bootstrapping Neo4j constraints/indexes...")
    graph_client.bootstrap()

    sqs = boto3.client("sqs", region_name=settings.AWS_REGION)
    queue_url = settings.LEADGEN_QUEUE_URL
    if not queue_url:
        logger.error("LEADGEN_QUEUE_URL is not configured! Exiting.")
        return

    logger.info(f"Long-polling SQS queue: {queue_url}")
    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                VisibilityTimeout=60,
            )

            messages = response.get("Messages", [])
            if not messages:
                continue

            for msg in messages:
                receipt_handle = msg["ReceiptHandle"]
                body = json.loads(msg["Body"])

                logger.info(
                    f"Processing turn event turn_id={body.get('turn_id')} for user={body.get('canonical_user_id')}"
                )
                process_event(graph_client, body)

                # Delete from SQS
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                logger.info(f"Deleted SQS message for turn_id={body.get('turn_id')}")

        except Exception as e:
            logger.exception(f"Error in main polling loop: {e}")
            time.sleep(5)


def process_event(graph_client: GraphClient, event: dict):
    user_id = event.get("canonical_user_id")
    phone = event.get("phone")

    # Identity Resolution
    if phone:
        phone_normalized = phone.strip().replace("+", "")
        records = graph_client.execute_query(
            "MATCH (u:User) WHERE u.phone = $phone OR u.phone = $phone_clean RETURN u.id as id",
            {"phone": phone.strip(), "phone_clean": phone_normalized},
        )
        if records:
            resolved_id = records[0].get("id")
            if resolved_id:
                logger.info(
                    f"Identity resolved phone={phone} to canonical_user_id={resolved_id}"
                )
                user_id = resolved_id

    # Create User node
    user_properties = {"id": user_id}
    if phone:
        user_properties["phone"] = phone.strip()
    if event.get("member_id"):
        user_properties["member_id"] = event.get("member_id")

    graph_client.upsert_user(user_id, user_properties)

    # Fetch recent history
    recent_history = []
    try:
        msg_repo = MessageRepository()
        dynamo_member_id = event.get("member_id") or user_id
        recent_history = msg_repo.list_recent(member_id=dynamo_member_id, limit=8)
    except Exception as e:
        logger.error(f"Error reading conversation history from DynamoDB: {e}")

    # LLM extraction
    extraction_result = extract_from_conversation(
        user_id=user_id,
        user_message=event.get("user_message", ""),
        assistant_message=event.get("assistant_message", ""),
        recent_history=recent_history,
    )

    logger.info(
        f"Extraction result: facts={len(extraction_result.profile_facts)} "
        f"interests={len(extraction_result.interests)} leads={len(extraction_result.leads)}"
    )

    # Graph Upserts
    # A. Profile facts
    for fact in extraction_result.profile_facts:
        try:
            graph_client.upsert_profile_fact(user_id, fact)
        except Exception as e:
            logger.error(f"Error writing profile fact {fact}: {e}")

    # B. Interests
    for interest in extraction_result.interests:
        try:
            graph_client.upsert_interest(user_id, interest)
        except Exception as e:
            logger.error(f"Error writing interest {interest}: {e}")

    # C. Leads
    lead_cache = LeadCacheRepository()
    for lead in extraction_result.leads:
        try:
            lead_data = graph_client.upsert_lead(user_id, lead)
            if lead_data:
                lead_cache.put(lead_data)
                logger.info(f"Updated DynamoDB lead cache for lead_id={lead_data.get('lead_id')}")
        except Exception as e:
            logger.error(f"Error writing lead {lead}: {e}")

    # D. Lead matching (Tier-2): connect this user's open leads to an eligible
    # vendor. Each newly matched lead flips to 'matched' in the graph and emits a
    # promotion event so the cache + vendor get notified out of band.
    try:
        matches = graph_client.match_leads(user_id=user_id)
        for m in matches:
            try:
                lead_cache.put({
                    "lead_id": m.get("lead_id"),
                    "category": m.get("category"),
                    "status": "matched",
                    "product": m.get("product"),
                    "attrs": m.get("attrs") or {},
                    "budget_signal": m.get("budget_signal"),
                    "intent_score": m.get("intent_score"),
                    "vendor_id": m.get("vendor_id"),
                    "vendor_name": m.get("vendor_name"),
                    "user_id": m.get("user_id"),
                    "last_seen_at": utc_now_iso(),
                })
            except Exception as e:
                logger.error(f"Error updating lead cache for matched lead {m.get('lead_id')}: {e}")
            send_lead_promotion({
                "event_type": "lead_matched",
                "lead_id": m.get("lead_id"),
                "user_id": m.get("user_id"),
                "vendor_id": m.get("vendor_id"),
                "vendor_name": m.get("vendor_name"),
                "vendor_phone": m.get("vendor_phone"),
                "vendor_email": m.get("vendor_email"),
                "category": m.get("category"),
                "product": m.get("product"),
                "matched_at": utc_now_iso(),
            })
        if matches:
            logger.info(f"Matched {len(matches)} lead(s) to vendors for user={user_id}")
    except Exception as e:
        logger.error(f"Error during lead matching for user={user_id}: {e}")

    # Write projection back to DynamoDB Profile Cache (merge, not replace — see
    # ProfileCacheRepository.update_fields: never clobbers Tier-1 personal cols).
    try:
        profile_view = graph_client.get_user_profile_projection(user_id)
        cache_repo = ProfileCacheRepository()
        cache_repo.merge_projection(user_id, profile_view)
        logger.info(f"Updated DynamoDB profile cache projection for user={user_id}")
    except Exception as e:
        logger.error(f"Error writing profile cache projection to DynamoDB: {e}")


if __name__ == "__main__":
    main()
