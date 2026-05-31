from __future__ import annotations
from typing import Any
from companion_api import settings
from companion_api.dynamodb import _resource, _json_safe, utc_now_iso


class ProfileCacheRepository:
    def __init__(self, table_name: str | None = None):
        self.table = _resource().Table(table_name or settings.PROFILE_CACHE_TABLE)

    def get(self, user_id: str) -> dict[str, Any] | None:
        response = self.table.get_item(Key={"PK": f"USER#{user_id}", "SK": "PROFILE"})
        item = response.get("Item")
        return _json_safe(item) if item else None

    def put(self, user_id: str, profile: dict[str, Any]) -> None:
        self.table.put_item(
            Item={
                "PK": f"USER#{user_id}",
                "SK": "PROFILE",
                **profile,
                "updated_at": utc_now_iso(),
            }
        )
