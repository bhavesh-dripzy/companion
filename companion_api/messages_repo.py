from __future__ import annotations
from typing import Any
from boto3.dynamodb.conditions import Key
from companion_api import settings
from companion_api.dynamodb import _resource, _json_safe, utc_now_iso


class MessageRepository:
    def __init__(self, table_name: str | None = None):
        self.table = _resource().Table(table_name or settings.MESSAGES_TABLE)

    def create(
        self,
        *,
        member_id: str,
        message_id: str,
        role: str,
        text: str,
        task_type: str | None = None,
        provider: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        item = {
            "member_id": str(member_id),
            "created_at_message_id": f"{now}#{message_id}",
            "role": role,
            "text": text,
            "created_at": now,
        }
        if task_type:
            item["task_type"] = task_type
        if provider:
            item["provider"] = provider
        if metadata:
            item["metadata"] = metadata

        self.table.put_item(Item=item)
        return item

    def list_recent(self, member_id: str, limit: int = 20) -> list[dict[str, Any]]:
        response = self.table.query(
            KeyConditionExpression=Key("member_id").eq(str(member_id)),
            ScanIndexForward=False,  # Descending (latest first)
            Limit=limit,
        )
        # Reverse to get chronological order (oldest first)
        items = [_json_safe(i) for i in response.get("Items", [])]
        items.reverse()
        return items
