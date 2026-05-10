from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    message: str


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1)
    include_whatsapp_link: bool = True
    llm_optional: bool = False


class FoodQueryRequest(BaseModel):
    message: str = Field(min_length=1)
    address_id: str | None = None
    provider: str | None = None
    tool_provider: str | None = None
    execute_tools: bool | None = None
    execute_zomato: bool | None = None
    include_raw_zomato: bool = False


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


class ReminderResponseRequest(BaseModel):
    reminder_id: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    response: Literal["yes", "no"]


class ReminderEvent(BaseModel):
    member_id: str
    reminder_id: str


JsonDict = dict[str, Any]
