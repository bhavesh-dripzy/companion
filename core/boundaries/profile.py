"""Profile read adapter — wraps the DynamoDB profile cache, fail-open.

The blocking boto3 read runs in a thread so the async pipeline never stalls. Any
error (missing table, throttle, bad shape) returns None so the turn degrades to
no personalization rather than failing (ADR-0004). The repo import is lazy so
`core.boundaries` is importable without AWS in tests.
"""

from __future__ import annotations

import asyncio

from core.contracts.profile import Profile


class CachedProfileService:
    async def get(self, user_id: str) -> Profile | None:
        if not user_id:
            return None
        try:
            raw = await asyncio.to_thread(self._read, user_id)
        except Exception:
            return None
        if not raw:
            return None
        try:
            return Profile.model_validate({"user_id": user_id, **raw})
        except Exception:
            return Profile(user_id=user_id)

    @staticmethod
    def _read(user_id: str) -> dict | None:
        from companion_api.profile_cache import ProfileCacheRepository

        return ProfileCacheRepository().get(user_id)
