from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    message: str


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1)
    include_whatsapp_link: bool = True
    llm_optional: bool = False


class FoodQueryRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[dict[str, str]] | None = None
    address_id: str = Field(
        min_length=1,
        description="Saved delivery address id from GET /api/food/saved-addresses/ for this user.",
    )
    provider: str | None = None
    execute_tools: bool | None = None
    execute_zomato: bool | None = None

    @field_validator("address_id", mode="before")
    @classmethod
    def strip_address_id(cls, v: object) -> object:
        if v is None:
            raise ValueError("address_id is required")
        if isinstance(v, str):
            s = v.strip()
            if not s:
                raise ValueError("address_id cannot be empty or whitespace")
            return s
        return v


class AgentQueryRequest(FoodQueryRequest):
    """Request body for the primary agent endpoint (POST /api/agent/query/)."""


class CustomMcpProviderRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    mcp_url: str = Field(min_length=8, max_length=500)
    tools: list[str] | None = None


class ZapierMcpProviderRequest(BaseModel):
    mcp_url: str = Field(min_length=8, max_length=500)
    tools: list[str] | None = None


class ReminderCreateRequest(BaseModel):
    member_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    type: str = "custom"
    schedule_type: Literal["fixed", "interval"] = "interval"
    next_trigger: datetime
    interval_minutes: int | None = None
    retry_interval_minutes: int = 5
    requires_confirmation: bool = True
    created_by: str | None = None


class GoogleCalendarReminderRequest(BaseModel):
    message: str = Field(min_length=1)
    calendar_id: str = "primary"
    timezone: str | None = None
    email_reminder_minutes: int = Field(default=0, ge=0, le=40320)
    popup_reminder_minutes: int | None = Field(default=10, ge=0, le=40320)
    store_reminder: bool = True


class ReminderResponseRequest(BaseModel):
    reminder_id: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    response: Literal["yes", "no"]


class ReminderEvent(BaseModel):
    member_id: str
    reminder_id: str


JsonDict = dict[str, Any]
