from __future__ import annotations
from typing import Any
from companion_api import settings
from companion_api.dynamodb import _resource, _json_safe, utc_now_iso
import json
from decimal import Decimal


class ProfileCacheRepository:
    def __init__(self, table_name: str | None = None):
        self.table = _resource().Table(table_name or settings.PROFILE_CACHE_TABLE)

    def get(self, user_id: str) -> dict[str, Any] | None:
        response = self.table.get_item(Key={"PK": f"USER#{user_id}", "SK": "PROFILE"})
        item = response.get("Item")
        return _json_safe(item) if item else None

    def put(self, user_id: str, profile: dict[str, Any]) -> None:
        item = json.loads(json.dumps({
            "PK": f"USER#{user_id}",
            "SK": "PROFILE",
            **profile,
            "updated_at": utc_now_iso(),
        }), parse_float=Decimal)
        self.table.put_item(Item=item)

    def update_fields(self, user_id: str, fields: dict[str, Any]) -> None:
        """Merge top-level attributes into the profile item (UpdateItem SET).

        Both the immediate personal-fact write-through (Tier-1) and the worker's
        graph projection (Tier-2) go through here so neither clobbers the other:
        each path SETs only the keys it owns. None values are skipped, so a stale
        projection (graph not yet aware of a fact) never nulls out a column the
        other tier just wrote. Names are aliased because cache columns like
        ``name`` / ``status`` are DynamoDB reserved words.
        """
        clean = {k: v for k, v in fields.items() if v is not None}
        if not clean:
            return
        safe = json.loads(json.dumps(clean), parse_float=Decimal)
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        assignments: list[str] = []
        for i, (key, val) in enumerate(safe.items()):
            names[f"#k{i}"] = key
            values[f":v{i}"] = val
            assignments.append(f"#k{i} = :v{i}")
        self.table.update_item(
            Key={"PK": f"USER#{user_id}", "SK": "PROFILE"},
            UpdateExpression="SET " + ", ".join(assignments),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def merge_personal(self, user_id: str, personal: dict[str, Any]) -> None:
        """Immediately persist durable personal columns (Tier-1), stamping updated_at_personal."""
        fields = {k: v for k, v in personal.items() if v is not None}
        if not fields:
            return
        fields["updated_at_personal"] = utc_now_iso()
        self.update_fields(user_id, fields)

    def merge_preferences(self, user_id: str, new: list[str], cap: int = 20) -> None:
        """Union durable personal preferences into the profile (Tier-1), newest last,
        case-insensitively de-duped and capped. Read-merge-write: preferences
        accumulate across turns (unlike scalar columns, which are overwritten)."""
        incoming = [str(p).strip() for p in new if str(p).strip()]
        if not incoming:
            return
        existing = (self.get(user_id) or {}).get("preferences") or []
        merged: list[str] = []
        seen: set[str] = set()
        for pref in [*existing, *incoming]:
            key = pref.strip().lower()
            if pref.strip() and key not in seen:
                seen.add(key)
                merged.append(pref.strip())
        self.update_fields(
            user_id, {"preferences": merged[-cap:], "updated_at_personal": utc_now_iso()}
        )

    def merge_projection(self, user_id: str, projection: dict[str, Any]) -> None:
        """Persist the worker's graph projection (Tier-2) as a merge, stamping updated_at_leads."""
        fields = dict(projection)
        fields["updated_at_leads"] = utc_now_iso()
        fields["updated_at"] = utc_now_iso()
        self.update_fields(user_id, fields)
