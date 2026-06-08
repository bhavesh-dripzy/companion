"""Wire the registry, KG-seam services, and LLM into a PipelineRunner.

The web layer calls `build_runner()` once and reuses it. Tests pass fakes for any
dependency (no AWS, no LLM). Defaults are the real adapters.
"""

from __future__ import annotations

from core.boundaries.profile import CachedProfileService
from core.boundaries.protocols import ProfileReadService, TurnEventPublisher
from core.boundaries.publisher import SqsTurnEventPublisher
from core.llm.client import complete_json_object
from core.pipeline.context import CompleteJson, PipelineDeps
from core.pipeline.runner import PipelineRunner
from core.registry.bootstrap import default_registry
from core.registry.provider_registry import ProviderRegistry


def build_runner(
    *,
    registry: ProviderRegistry | None = None,
    profile_service: ProfileReadService | None = None,
    publisher: TurnEventPublisher | None = None,
    complete_json: CompleteJson | None = None,
) -> PipelineRunner:
    deps = PipelineDeps(
        registry=registry or default_registry(),
        profile_service=profile_service or CachedProfileService(),
        publisher=publisher or SqsTurnEventPublisher(),
        complete_json=complete_json or complete_json_object,
    )
    return PipelineRunner(deps)
