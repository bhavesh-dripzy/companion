from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProfileFact(BaseModel):
    type: str = Field(
        ...,
        description="Fact type, e.g., work_address, home_address, age, city, language, name",
    )
    value: str = Field(..., description="The value of the fact")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Interest(BaseModel):
    category: str = Field(
        ...,
        description="Category like food, electronics, furniture, salon_services, travel",
    )
    detail: str = Field(..., description="Specific detail, e.g., ramen, split AC, massage, sofa")
    weight: float = Field(default=0.5, ge=0.0, le=1.0)


class Lead(BaseModel):
    category: str = Field(
        ...,
        description="Category, e.g. electronics, food, furniture, salon_services",
    )
    product: str = Field(
        ...,
        description="Product details or service requested, e.g. 1.5 ton split AC",
    )
    attrs: dict[str, Any] = Field(
        default_factory=dict, description="Additional attributes extracted"
    )
    budget_signal: str | None = Field(default=None, description="Signal about budget: low, mid, high")
    intent_score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    profile_facts: list[ProfileFact] = Field(default_factory=list)
    interests: list[Interest] = Field(default_factory=list)
    leads: list[Lead] = Field(default_factory=list)


class TurnEvent(BaseModel):
    canonical_user_id: str
    turn_id: str
    user_message: str
    assistant_message: str
    task_type: str | None = None
    routed_provider: str | None = None
    session_addresses: list[dict[str, Any]] | None = None
    geo: dict[str, Any] | None = None
    phone: str | None = None
    member_id: str | None = None
    ts: str
