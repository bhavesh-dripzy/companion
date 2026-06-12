"""Async agent turns: job store, self-invoke dispatch, and the turn worker.

API Gateway's HTTP API caps integrations at 30s while tool-heavy turns run
50s+, so `POST /api/agent/query/` no longer blocks on the pipeline: it creates
a job, re-invokes this same Lambda asynchronously to run the turn, briefly
waits so fast turns still return inline, and otherwise returns an `accepted`
ack the frontend polls via `GET /api/agent/jobs/{job_id}/`. The worker streams
human-friendly progress lines (ProgressNarrator) into the job as the pipeline
advances, then stores the final envelope.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any

import boto3

from companion_api.secrets import secret_or_env
from companion_api.turn_persistence import persist_turn
from companion_api.turn_progress import ProgressNarrator

logger = logging.getLogger(__name__)

_JOB_TTL_SECONDS = 3600
_INLINE_POLL_INTERVAL = 0.45
ASYNC_EVENT_MARKER = "agent_turn"


def _table():
    return boto3.resource("dynamodb").Table(os.environ["AGENT_JOBS_TABLE"])


def async_turns_enabled() -> bool:
    if not os.getenv("AGENT_JOBS_TABLE", "").strip():
        return False
    return secret_or_env("ASYNC_TURNS", "true").strip().lower() != "false"


def create_job(user: str) -> str:
    job_id = str(uuid.uuid4())
    now = int(time.time())
    _table().put_item(
        Item={
            "job_id": job_id,
            "member_id": user,
            "status": "running",
            "progress": ["Thinking…"],
            "created_at": now,
            "expires_at": now + _JOB_TTL_SECONDS,
        }
    )
    return job_id


def update_progress(job_id: str, label: str) -> None:
    try:
        _table().update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET progress = list_append(progress, :p)",
            ExpressionAttributeValues={":p": [label]},
        )
    except Exception:
        logger.warning("progress update failed for job %s", job_id, exc_info=True)


def complete_job(job_id: str, envelope: dict[str, Any]) -> None:
    _table().update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :s, result_json = :r",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "done", ":r": json.dumps(envelope)},
    )


def fail_job(job_id: str, error: str) -> None:
    try:
        _table().update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s, #e = :e",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":s": "error", ":e": error[:500]},
        )
    except Exception:
        logger.exception("could not mark job %s failed", job_id)


def _get_item(job_id: str) -> dict[str, Any] | None:
    response = _table().get_item(Key={"job_id": job_id}, ConsistentRead=True)
    return response.get("Item")


def get_job(job_id: str, user: str) -> dict[str, Any] | None:
    """The poll view of a job, scoped to its owner."""
    item = _get_item(job_id)
    if not item or item.get("member_id") != user:
        return None
    view: dict[str, Any] = {
        "job_id": job_id,
        "status": item.get("status"),
        "progress": list(item.get("progress") or []),
    }
    if item.get("result_json"):
        view["result"] = json.loads(item["result_json"])
    if item.get("error"):
        view["error"] = item["error"]
    return view


def finalize_turn(envelope: dict[str, Any], data: dict[str, Any], user: str) -> dict[str, Any]:
    """Persist the exchange (chat lazy-create + title + messages) onto the envelope."""
    if envelope.get("status") != "ok":
        return envelope
    geo = data.get("geo") if isinstance(data.get("geo"), dict) else None
    chat_id = persist_turn(
        user=user,
        chat_id=data.get("chat_id"),
        user_text=str(data.get("message") or ""),
        assistant_text=str(envelope.get("assistant_message") or ""),
        task_type=envelope.get("task_type"),
        provider=envelope.get("routed_provider"),
        attachments=envelope.get("attachments"),
        session_addresses=data.get("session_addresses"),
        user_geo=geo,
    )
    if chat_id:
        envelope["chat_id"] = chat_id
    return envelope


def _run_worker(job_id: str, data: dict[str, Any], user: str, reply_language: str | None) -> None:
    from companion_api.core_shadow import run_core_turn_sync

    narrator = ProgressNarrator(lambda label: update_progress(job_id, label))
    try:
        envelope = run_core_turn_sync(
            data, user, reply_language=reply_language, trace_listener=narrator
        )
        envelope = finalize_turn(envelope, data, user)
        complete_job(job_id, envelope)
    except Exception as e:
        logger.exception("async turn failed (job=%s user=%s)", job_id, user)
        fail_job(job_id, f"{type(e).__name__}: {e}")


def run_async_turn_event(event: dict[str, Any]) -> dict[str, Any]:
    """Entry point for the self-invoked worker execution."""
    _run_worker(
        str(event["job_id"]),
        dict(event.get("payload") or {}),
        str(event["user"]),
        event.get("reply_language"),
    )
    return {"ok": True}


def _dispatch(job_id: str, data: dict[str, Any], user: str, reply_language: str | None) -> None:
    function_name = os.getenv("AWS_LAMBDA_FUNCTION_NAME", "").strip()
    if function_name:
        boto3.client("lambda").invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(
                {
                    "companion_async": ASYNC_EVENT_MARKER,
                    "job_id": job_id,
                    "user": user,
                    "reply_language": reply_language,
                    "payload": data,
                }
            ).encode("utf-8"),
        )
        return
    # Local/dev fallback: run the worker on a daemon thread in this process.
    threading.Thread(
        target=_run_worker, args=(job_id, data, user, reply_language), daemon=True
    ).start()


def start_async_turn(
    data: dict[str, Any], user: str, *, reply_language: str | None = None
) -> dict[str, Any]:
    """Kick off an async turn; return the final envelope if it finishes fast,
    else an `accepted` ack the client polls."""
    job_id = create_job(user)
    _dispatch(job_id, data, user, reply_language)

    try:
        inline_wait = float(secret_or_env("ASYNC_TURN_INLINE_WAIT", "6") or 6)
    except ValueError:
        inline_wait = 6.0
    deadline = time.time() + max(0.0, min(inline_wait, 15.0))
    progress: list[str] = []
    while time.time() < deadline:
        time.sleep(_INLINE_POLL_INTERVAL)
        item = _get_item(job_id) or {}
        progress = list(item.get("progress") or [])
        status = item.get("status")
        if status == "done" and item.get("result_json"):
            return json.loads(item["result_json"])
        if status == "error":
            return {
                "status": "error",
                "assistant_message": "",
                "error": item.get("error") or "The request failed.",
                "pipeline": "core",
            }
    return {
        "status": "accepted",
        "job_id": job_id,
        "progress": progress or ["Thinking…"],
        "pipeline": "core",
    }
