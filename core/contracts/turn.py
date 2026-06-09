"""The pipeline's input and output envelopes.

`PipelineRunner.run(TurnRequest) -> TurnResponse`. Both allow extra fields so the
web layer can carry provider-specific session keys (the legacy `session_*`
aliases) during migration without breaking the typed core.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.contracts.attachment import Attachment
from core.contracts.session import SessionState
from core.contracts.tool_result import ProviderResult


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: str
    user_id: str | None = None
    address_id: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    chat_id: str | None = None
    session: SessionState = Field(default_factory=SessionState)
    execute_tools: bool = True
    reply_language: str | None = None  # e.g. "hinglish" for voice turns
    user_geo: dict[str, Any] | None = None
    enabled_providers: list[str] | None = None


class TurnResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    assistant_message: str = ""
    task_type: str | None = None
    routed_provider: str | None = None
    # Every provider the orchestrator selected this turn (routed_provider is
    # providers[0]). The web layer surfaces this to the frontend, which renders
    # the names as meta chips — see companion_api.core_shadow._to_envelope.
    selected_tool_providers: list[str] = Field(default_factory=list)
    # Raw per-provider outcomes, kept so the bridge can build the legacy
    # `tool_execution` envelope the frontend reads. Not serialized to the wire.
    provider_results: list[ProviderResult] = Field(default_factory=list)
    session: SessionState = Field(default_factory=SessionState)
    attachments: list[Attachment] = Field(default_factory=list)
    status: str = "ok"
    error: str | None = None
    error_detail: str | None = None
    observable_trace: list[dict[str, Any]] = Field(default_factory=list)
