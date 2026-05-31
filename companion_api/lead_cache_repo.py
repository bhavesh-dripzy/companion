from __future__ import annotations

from typing import Any
from companion_api import settings
from companion_api.dynamodb import _resource, _json_safe, utc_now_iso
from boto3.dynamodb.conditions import Key
import json
from decimal import Decimal


class LeadCacheRepository:
    def __init__(self, table_name: str | None = None):
        self.table = _resource().Table(table_name or settings.LEAD_CACHE_TABLE)

    def get(self, lead_id: str) -> dict[str, Any] | None:
        response = self.table.get_item(Key={"lead_id": lead_id})
        item = response.get("Item")
        return _json_safe(item) if item else None

    def put(self, lead: dict[str, Any]) -> None:
        lead_id = lead["lead_id"]
        category = lead["category"]
        status = lead["status"]
        last_seen_at = lead.get("last_seen_at") or utc_now_iso()
        status_last_seen = f"{status}#{last_seen_at}"

        item = json.loads(json.dumps({
            **lead,
            "lead_id": lead_id,
            "category": category,
            "status": status,
            "last_seen_at": last_seen_at,
            "status_last_seen": status_last_seen,
            "updated_at": utc_now_iso(),
        }), parse_float=Decimal)
        self.table.put_item(Item=item)

    def list_by_category(
        self,
        category: str,
        status_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        if status_prefix:
            condition = Key("category").eq(category) & Key("status_last_seen").begins_with(status_prefix)
        else:
            condition = Key("category").eq(category)

        response = self.table.query(
            IndexName="category-status-index",
            KeyConditionExpression=condition,
        )
        return [_json_safe(item) for item in response.get("Items", [])]
