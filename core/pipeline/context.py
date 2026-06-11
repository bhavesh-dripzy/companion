"""The context threaded through the pipeline stages, plus injected dependencies.

`PipelineDeps` is the injection seam: the registry, the KG-seam services, and the
async LLM-completion callable. Tests pass fakes; `app_factory` wires the real ones.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from core.boundaries.protocols import GeocodeService, ProfileReadService, TurnEventPublisher
from core.contracts.attachment import Attachment
from core.contracts.profile import Profile
from core.contracts.routing import RoutingDecision
from core.contracts.session import SessionState
from core.contracts.tool_call import ToolCall, ToolPlan
from core.contracts.tool_result import ProviderResult
from core.contracts.turn import TurnRequest
from core.llm.config import ResolvedLlm
from core.middleware.tracing import Tracer
from core.registry.provider_registry import ProviderRegistry

# (*, user_message, system_prompt, llm) -> dict — matches core.llm.complete_json_object.
CompleteJson = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class PipelineDeps:
    registry: ProviderRegistry
    profile_service: ProfileReadService
    publisher: TurnEventPublisher
    complete_json: CompleteJson
    geocode: GeocodeService | None = None


@dataclass
class PipelineContext:
    request: TurnRequest
    llm: ResolvedLlm
    deps: PipelineDeps
    tracer: Tracer = field(default_factory=Tracer)
    profile: Profile | None = None
    routing: RoutingDecision | None = None
    plans: dict[str, ToolPlan] = field(default_factory=dict)
    calls: list[ToolCall] = field(default_factory=list)
    blocked: list[ToolCall] = field(default_factory=list)
    provider_results: list[ProviderResult] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    assistant_message: str = ""
    session: SessionState = field(default_factory=SessionState)
    confirmed: bool = False
