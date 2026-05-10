from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3

from companion_api import settings
from companion_api.dynamodb import ReminderRepository


def _scheduler():
    return boto3.client("scheduler", region_name=settings.AWS_REGION)


def _lambda_safe_name(member_id: str, reminder_id: str) -> str:
    raw = f"reminder-{member_id}-{reminder_id}"
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in raw)[:64]


def _at_expression(iso_dt: str) -> str:
    dt = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return f"at({dt.strftime('%Y-%m-%dT%H:%M:%S')})"


class ReminderScheduler:
    def __init__(self, repo: ReminderRepository | None = None):
        self.repo = repo or ReminderRepository()

    def create_reminder(self, payload: dict[str, Any]) -> dict[str, Any]:
        reminder = self.repo.create(payload)
        self.upsert_schedule(reminder)
        return reminder

    def upsert_schedule(self, reminder: dict[str, Any]) -> None:
        if not settings.REMINDER_TARGET_ARN or not settings.SCHEDULER_ROLE_ARN:
            return

        name = _lambda_safe_name(reminder["member_id"], reminder["reminder_id"])
        target = {
            "Arn": settings.REMINDER_TARGET_ARN,
            "RoleArn": settings.SCHEDULER_ROLE_ARN,
            "Input": json.dumps(
                {
                    "member_id": reminder["member_id"],
                    "reminder_id": reminder["reminder_id"],
                }
            ),
        }
        kwargs = {
            "Name": name,
            "GroupName": settings.SCHEDULER_GROUP_NAME,
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "ScheduleExpression": _at_expression(reminder["next_trigger"]),
            "Target": target,
        }
        client = _scheduler()
        try:
            client.update_schedule(**kwargs)
        except client.exceptions.ResourceNotFoundException:
            client.create_schedule(**kwargs)

    def mark_triggered(self, member_id: str, reminder_id: str) -> dict[str, Any] | None:
        return self.repo.mark_triggered(member_id, reminder_id)

    def respond(self, *, member_id: str, reminder_id: str, response: str) -> dict[str, Any]:
        reminder = self.repo.get(member_id, reminder_id)
        if not reminder:
            raise KeyError("Reminder not found.")

        now = datetime.now(timezone.utc)
        if response == "yes":
            interval = int(reminder.get("interval_minutes") or 1440)
            next_dt = now + timedelta(minutes=interval)
        else:
            retry = int(reminder.get("retry_interval_minutes") or 5)
            next_dt = now + timedelta(minutes=retry)

        updated = self.repo.update_after_response(
            member_id=member_id,
            reminder_id=reminder_id,
            next_trigger=next_dt.isoformat(),
            last_triggered=now.isoformat(),
        )
        if updated:
            self.upsert_schedule(updated)
            return updated
        raise KeyError("Reminder not found.")
