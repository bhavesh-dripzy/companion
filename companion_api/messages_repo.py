from __future__ import annotations
from typing import Any
from boto3.dynamodb.conditions import Attr, Key
from companion_api import settings
from companion_api.dynamodb import _json_safe, _resource, utc_now_iso


# Single-table design on ``companion-messages`` to keep the CDK schema
# unchanged (PK=member_id, SK=string) while supporting multiple chats per
# user. Two row shapes share the table, disambiguated by SK prefix:
#
#   * "chat#<chat_id>"                                   → chat metadata
#       attrs: title, created_at, updated_at,
#              last_message_preview, message_count
#
#   * "msg#<chat_id>#<created_at_iso>#<message_id>"     → individual message
#       attrs: chat_id, role, text, created_at,
#              task_type?, provider?, metadata?
#
# Listing a user's chats   → query PK=member_id AND begins_with(SK, "chat#")
# Listing chat's messages  → query PK=member_id AND begins_with(SK, "msg#<id>#")
# Deleting a chat          → delete metadata row + scan-delete message rows
#
# Legacy rows (pre-multi-chat) use SK = "<iso>#<message_id>" without a
# prefix; they are simply ignored by the new endpoints (no migration).
_CHAT_SK_PREFIX = "chat#"
_MSG_SK_PREFIX = "msg#"


def _chat_sk(chat_id: str) -> str:
    return f"{_CHAT_SK_PREFIX}{chat_id}"


def _msg_sk(chat_id: str, created_at: str, message_id: str) -> str:
    return f"{_MSG_SK_PREFIX}{chat_id}#{created_at}#{message_id}"


def _msg_sk_prefix(chat_id: str) -> str:
    return f"{_MSG_SK_PREFIX}{chat_id}#"


class MessageRepository:
    """Per-chat message store. Each row lives under ``msg#<chat_id>#…`` so
    a single Dynamo query against ``begins_with(SK, msg#<chat_id>#)``
    returns the full chronological log for one conversation, and the same
    table also stores chat metadata under ``chat#<chat_id>`` (see
    ``ChatRepository``).
    """

    def __init__(self, table_name: str | None = None):
        self.table = _resource().Table(table_name or settings.MESSAGES_TABLE)

    def create(
        self,
        *,
        member_id: str,
        chat_id: str,
        message_id: str,
        role: str,
        text: str,
        task_type: str | None = None,
        provider: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        item: dict[str, Any] = {
            "member_id": str(member_id),
            "created_at_message_id": _msg_sk(str(chat_id), now, str(message_id)),
            "chat_id": str(chat_id),
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

    def list_for_chat(
        self, member_id: str, chat_id: str, limit: int = 80
    ) -> list[dict[str, Any]]:
        """Return the most recent ``limit`` messages of one chat, sorted
        chronologically (oldest first) — the order the chat UI renders in.
        """
        response = self.table.query(
            KeyConditionExpression=(
                Key("member_id").eq(str(member_id))
                & Key("created_at_message_id").begins_with(_msg_sk_prefix(str(chat_id)))
            ),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = [_json_safe(i) for i in response.get("Items", [])]
        items.reverse()
        return items

    def list_recent(self, member_id: str, limit: int = 8) -> list[dict[str, Any]]:
        """Return the last ``limit`` messages of the user's most recently
        active chat (oldest first) — the transcript slice the leadgen
        extractor feeds its LLM. Empty when the user has no chats yet.
        """
        chats = ChatRepository(self.table.name).list_chats(member_id, limit=1)
        if not chats:
            return []
        chat_id = chats[0].get("chat_id")
        if not chat_id:
            return []
        return self.list_for_chat(member_id, str(chat_id), limit=limit)

    def delete_chat_messages(self, member_id: str, chat_id: str) -> int:
        """Scan-delete every message row for one chat. Returns the deleted
        count. Cheap because each chat is bounded — at most ~hundreds of
        rows — and we paginate naturally via the query response.
        """
        prefix = _msg_sk_prefix(str(chat_id))
        deleted = 0
        last_evaluated_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": (
                    Key("member_id").eq(str(member_id))
                    & Key("created_at_message_id").begins_with(prefix)
                ),
                "ProjectionExpression": "member_id, created_at_message_id",
            }
            if last_evaluated_key:
                kwargs["ExclusiveStartKey"] = last_evaluated_key
            response = self.table.query(**kwargs)
            items = response.get("Items", []) or []
            if items:
                with self.table.batch_writer() as batch:
                    for it in items:
                        batch.delete_item(
                            Key={
                                "member_id": it["member_id"],
                                "created_at_message_id": it["created_at_message_id"],
                            }
                        )
                        deleted += 1
            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break
        return deleted


class ChatRepository:
    """Chat metadata (the things a sidebar needs: title, timestamps,
    preview snippet) on the same Dynamo table as messages — single-table
    pattern, no CDK change. PK=member_id, SK=``chat#<chat_id>``.
    """

    def __init__(self, table_name: str | None = None):
        self.table = _resource().Table(table_name or settings.MESSAGES_TABLE)

    def create(
        self,
        *,
        member_id: str,
        chat_id: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        item: dict[str, Any] = {
            "member_id": str(member_id),
            "created_at_message_id": _chat_sk(str(chat_id)),
            "chat_id": str(chat_id),
            "title": (title or "New chat").strip() or "New chat",
            "created_at": now,
            "updated_at": now,
            "last_message_at": now,
            "last_message_preview": "",
            "message_count": 0,
        }
        self.table.put_item(Item=item)
        return _json_safe(item)

    def get(self, member_id: str, chat_id: str) -> dict[str, Any] | None:
        response = self.table.get_item(
            Key={
                "member_id": str(member_id),
                "created_at_message_id": _chat_sk(str(chat_id)),
            }
        )
        item = response.get("Item")
        return _json_safe(item) if item else None

    def list_chats(self, member_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return the user's chats sorted by ``updated_at`` desc — the
        order the sidebar wants (most recently active at top).

        Dynamo doesn't sort by an attribute on query, only by SK, so we
        fetch all chat# rows (capped at a sane batch — users won't have
        thousands of chats) and sort in memory.
        """
        response = self.table.query(
            KeyConditionExpression=(
                Key("member_id").eq(str(member_id))
                & Key("created_at_message_id").begins_with(_CHAT_SK_PREFIX)
            ),
        )
        items = [_json_safe(i) for i in response.get("Items", [])]
        items.sort(key=lambda i: str(i.get("updated_at") or ""), reverse=True)
        return items[:limit]

    def touch(
        self,
        *,
        member_id: str,
        chat_id: str,
        last_message_preview: str | None = None,
        title: str | None = None,
        increment_count: int = 0,
    ) -> dict[str, Any] | None:
        """Bump ``updated_at`` / ``last_message_at`` (and optionally
        ``last_message_preview``, ``title``, ``message_count``) after a
        new turn. Used by the agent-query handler to keep the sidebar's
        list fresh and the chat sorted to the top after every reply.
        """
        now = utc_now_iso()
        update_parts = [
            "updated_at = :now",
            "last_message_at = :now",
        ]
        values: dict[str, Any] = {":now": now}
        names: dict[str, str] = {}

        if last_message_preview is not None:
            update_parts.append("last_message_preview = :preview")
            values[":preview"] = last_message_preview[:200]
        if title is not None:
            # ``title`` is a reserved-ish word; alias for safety.
            update_parts.append("#title = :title")
            names["#title"] = "title"
            values[":title"] = title[:200]
        if increment_count:
            update_parts.append("message_count = if_not_exists(message_count, :zero) + :inc")
            values[":zero"] = 0
            values[":inc"] = int(increment_count)

        kwargs: dict[str, Any] = {
            "Key": {
                "member_id": str(member_id),
                "created_at_message_id": _chat_sk(str(chat_id)),
            },
            "UpdateExpression": "SET " + ", ".join(update_parts),
            "ExpressionAttributeValues": values,
            "ConditionExpression": Attr("created_at_message_id").exists(),
            "ReturnValues": "ALL_NEW",
        }
        if names:
            kwargs["ExpressionAttributeNames"] = names
        try:
            response = self.table.update_item(**kwargs)
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            # Chat row was deleted (or never existed); silently skip.
            return None
        attrs = response.get("Attributes")
        return _json_safe(attrs) if attrs else None

    def rename(
        self, *, member_id: str, chat_id: str, title: str
    ) -> dict[str, Any] | None:
        return self.touch(member_id=member_id, chat_id=chat_id, title=title)

    def delete(self, member_id: str, chat_id: str) -> bool:
        """Delete a chat's metadata row. Caller is responsible for also
        deleting its messages via ``MessageRepository.delete_chat_messages``
        — done as two steps so a failure mid-way leaves the chat
        partially deleted but never silently splits state.
        """
        try:
            self.table.delete_item(
                Key={
                    "member_id": str(member_id),
                    "created_at_message_id": _chat_sk(str(chat_id)),
                },
                ConditionExpression=Attr("created_at_message_id").exists(),
            )
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False


def derive_chat_title(text: str, *, max_chars: int = 60) -> str:
    """First-turn auto-title: take the user's opening line and trim to
    a sidebar-friendly length. Multi-line messages get collapsed; very
    short ones (e.g. "hi") still produce a usable label.
    """
    if not isinstance(text, str):
        return "New chat"
    cleaned = " ".join(text.split())
    if not cleaned:
        return "New chat"
    if len(cleaned) <= max_chars:
        return cleaned
    # Try to break on a word boundary near the cap.
    trimmed = cleaned[:max_chars].rsplit(" ", 1)[0]
    return (trimmed or cleaned[:max_chars]).rstrip(",.?!:;-") + "…"
