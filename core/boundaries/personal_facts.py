"""Personal-fact writer adapter — Tier-1 immediate cache write, fail-open.

`Persist` hands this the turn text plus the pipeline's own LLM callable + config,
so the narrow personal-fact extraction reuses the exact model resolved for the
turn. The detected columns are merged into the DynamoDB profile cache right away
(UpdateItem, never clobbering lead columns) so the next turn sees them — instead
of waiting for the async worker. Any failure is swallowed: this must never break
the reply (ADR-0004). Imports are lazy so `core.boundaries` needs no AWS in tests.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from core.llm.config import ResolvedLlm


class LlmPersonalFactWriter:
    async def capture(
        self,
        *,
        user_id: str,
        user_message: str,
        assistant_message: str,
        complete_json: Callable[..., Awaitable[dict[str, Any]]],
        llm: ResolvedLlm,
    ) -> None:
        if not user_id:
            return
        with contextlib.suppress(Exception):
            from companion_api.personal_fact_extractor import extract_personal_facts

            facts = await extract_personal_facts(
                user_message=user_message,
                assistant_message=assistant_message,
                complete_json=complete_json,
                llm=llm,
            )
            if facts:
                await asyncio.to_thread(self._write, user_id, facts)

    @staticmethod
    def _write(user_id: str, facts: dict[str, Any]) -> None:
        from companion_api.profile_cache import ProfileCacheRepository

        ProfileCacheRepository().merge_personal(user_id, facts)
