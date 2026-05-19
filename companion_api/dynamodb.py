from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key

from companion_api import settings


def _resource():
    return boto3.resource("dynamodb", region_name=settings.AWS_REGION)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReminderRepository:
    def __init__(self, table_name: str | None = None):
        self.table = _resource().Table(table_name or settings.REMINDERS_TABLE)

    def create(self, item: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        reminder = {
            "member_id": str(item["member_id"]),
            "reminder_id": item.get("reminder_id") or str(uuid4()),
            "title": item["title"],
            "description": item.get("description", ""),
            "type": item.get("type", "custom"),
            "schedule_type": item.get("schedule_type", "interval"),
            "next_trigger": item["next_trigger"],
            "interval_minutes": item.get("interval_minutes"),
            "retry_interval_minutes": item.get("retry_interval_minutes", 5),
            "requires_confirmation": item.get("requires_confirmation", True),
            "status": item.get("status", "active"),
            "is_triggered": item.get("is_triggered", False),
            "last_triggered": item.get("last_triggered"),
            "created_by": item.get("created_by"),
            "created_at": now,
            "updated_at": now,
        }
        self.table.put_item(Item={k: v for k, v in reminder.items() if v is not None})
        return reminder

    def get(self, member_id: str, reminder_id: str) -> dict[str, Any] | None:
        response = self.table.get_item(
            Key={"member_id": str(member_id), "reminder_id": str(reminder_id)}
        )
        item = response.get("Item")
        return _json_safe(item) if item else None

    def list_pending(self, member_id: str) -> list[dict[str, Any]]:
        response = self.table.query(
            KeyConditionExpression=Key("member_id").eq(str(member_id)),
        )
        items = [_json_safe(i) for i in response.get("Items", [])]
        pending = [
            i
            for i in items
            if i.get("status") == "active" and bool(i.get("is_triggered")) is True
        ]
        return sorted(pending, key=lambda i: str(i.get("next_trigger", "")))

    def mark_triggered(self, member_id: str, reminder_id: str) -> dict[str, Any] | None:
        now = utc_now_iso()
        response = self.table.update_item(
            Key={"member_id": str(member_id), "reminder_id": str(reminder_id)},
            UpdateExpression="SET is_triggered = :yes, last_triggered = :now, updated_at = :now",
            ExpressionAttributeValues={":yes": True, ":now": now},
            ReturnValues="ALL_NEW",
        )
        attrs = response.get("Attributes")
        return _json_safe(attrs) if attrs else None

    def update_after_response(
        self,
        *,
        member_id: str,
        reminder_id: str,
        next_trigger: str,
        last_triggered: str,
    ) -> dict[str, Any] | None:
        now = utc_now_iso()
        response = self.table.update_item(
            Key={"member_id": str(member_id), "reminder_id": str(reminder_id)},
            UpdateExpression=(
                "SET is_triggered = :no, next_trigger = :next, "
                "last_triggered = :last, updated_at = :now"
            ),
            ExpressionAttributeValues={
                ":no": False,
                ":next": next_trigger,
                ":last": last_triggered,
                ":now": now,
            },
            ReturnValues="ALL_NEW",
        )
        attrs = response.get("Attributes")
        return _json_safe(attrs) if attrs else None


class MemberRepository:
    def __init__(self, table_name: str | None = None):
        self.table = _resource().Table(table_name or settings.MEMBERS_TABLE)

    def get_by_phone(self, phone_number: str) -> dict[str, Any] | None:
        """Find a member by their phone number using the phone-number-index."""
        # Normalize: strip + if present, though Meta usually sends it.
        clean_phone = phone_number.strip().replace("+", "")
        if not clean_phone:
            return None

        # Try with + first, then without (legacy/native format)
        for phone in [f"+{clean_phone}", clean_phone]:
            response = self.table.query(
                IndexName="phone-number-index",
                KeyConditionExpression=Key("phone_number").eq(phone),
            )
            items = response.get("Items", [])
            if items:
                return _json_safe(items[0])
        return None

    def get(self, member_id: str) -> dict[str, Any] | None:
        response = self.table.get_item(Key={"member_id": str(member_id)})
        item = response.get("Item")
        return _json_safe(item) if item else None

    def list_by_family(self, family_id: str) -> list[dict[str, Any]]:
        """List all members in a family using the family-index."""
        response = self.table.query(
            IndexName="family-index",
            KeyConditionExpression=Key("family_id").eq(family_id),
        )
        return [_json_safe(i) for i in response.get("Items", [])]
