"""Wire the registry, KG-seam services, and LLM into a PipelineRunner.

The web layer calls `build_runner()` once and reuses it. Tests pass fakes for any
dependency (no AWS, no LLM). Defaults are the real adapters.
"""

from __future__ import annotations

from core.boundaries.geocode import GoogleGeocodeService
from core.boundaries.personal_facts import LlmPersonalFactWriter
from core.boundaries.profile import CachedProfileService
from core.boundaries.protocols import (
    GeocodeService,
    PersonalFactWriter,
    ProfileReadService,
    TurnEventPublisher,
)
from core.boundaries.publisher import SqsTurnEventPublisher
from core.llm.client import complete_json_object
from core.llm.client import complete_text as complete_text_client
from core.pipeline.context import CompleteJson, CompleteText, PipelineDeps
from core.pipeline.runner import PipelineRunner
from core.registry.bootstrap import default_registry
from core.registry.provider_registry import ProviderRegistry


def build_runner(
    *,
    registry: ProviderRegistry | None = None,
    profile_service: ProfileReadService | None = None,
    publisher: TurnEventPublisher | None = None,
    complete_json: CompleteJson | None = None,
    complete_text: CompleteText | None = None,
    geocode: GeocodeService | None = None,
    personal_fact_writer: PersonalFactWriter | None = None,
) -> PipelineRunner:
    resolved_json = complete_json or complete_json_object
    deps = PipelineDeps(
        registry=registry or default_registry(),
        profile_service=profile_service or CachedProfileService(),
        publisher=publisher or SqsTurnEventPublisher(),
        complete_json=resolved_json,
        complete_text=complete_text or _default_text(complete_json),
        geocode=geocode or GoogleGeocodeService(),
        personal_fact_writer=personal_fact_writer or LlmPersonalFactWriter(),
    )
    return PipelineRunner(deps)


def _default_text(injected_json: CompleteJson | None) -> CompleteText:
    """Prod uses the real plain-text call. When a test injects a fake `complete_json`
    (but no `complete_text`), derive the narrator's text path from it — unwrap the
    fake's {"message": ...} — so the narrator stays offline with no second fake."""
    if injected_json is None:
        return complete_text_client

    async def _from_json(*, user_message: str, system_prompt: str, llm: object) -> str:
        obj = await injected_json(user_message=user_message, system_prompt=system_prompt, llm=llm)
        return str((obj or {}).get("message") or "")

    return _from_json
