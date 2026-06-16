"""The orchestrator's decision: which task and which providers handle the turn.

Unifies the legacy multi-list output (food_providers / finance_providers /
mobility_providers / phone_providers / health_providers) into one provider list
plus a task type. The Route stage maps the orchestrator response onto this.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    FOOD_DELIVERY = "food_delivery"
    GOING_OUT = "going_out"
    FINANCE_INVESTING = "finance_investing"
    HEALTH_NUTRITION = "health_nutrition"
    NORMAL_QUERY = "normal_query"
    REMINDER_CALENDAR = "reminder_calendar"
    MAPS_QUERY = "maps_query"
    WHATSAPP_MESSAGE = "whatsapp_message"
    CAB_BOOKING = "cab_booking"
    PHONE_CALL = "phone_call"
    DATA_LOOKUP = "data_lookup"
    CARPOOL = "carpool"


class RoutingDecision(BaseModel):
    """Where a turn goes after orchestration."""

    task_type: str = TaskType.FOOD_DELIVERY.value
    providers: list[str] = Field(default_factory=list)
    reasoning: str = ""
    # The provider the previous successful turn used (drives stickiness).
    previous_provider: str | None = None

    @property
    def names_single_provider(self) -> bool:
        return len(self.providers) == 1
