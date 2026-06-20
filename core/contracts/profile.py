"""User profile projection read across the KG seam (fail-open).

Mirrors the `companion-profile-cache` item: identity, saved addresses (used for
home/office auto-resolution), interests, and open leads. Loosely typed because
the cache shape evolves independently behind the boundary (ADR-0004).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SavedAddress(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: str | None = None
    address_line: str | None = None
    lat: float | None = None
    lng: float | None = None
    # Only a provider-issued id is usable for delivery; free text becomes geo.
    provider_address_id: str | None = None


class Profile(BaseModel):
    model_config = ConfigDict(extra="allow")

    user_id: str
    name: str | None = None
    age: Any = None
    city: str | None = None
    language: str | None = None
    phone: str | None = None
    email: str | None = None
    occupation: str | None = None
    family_size: Any = None
    family_composition: Any = None
    # Durable personal PREFERENCES the user states ("prefers vegetarian", "pays via
    # UPI", "follows cricket") — captured on-path, surfaced for personalization.
    # Distinct from `interests`/leads, which are leadgen/commercial (vendor matching).
    preferences: list[str] = Field(default_factory=list)
    addresses: dict[str, SavedAddress] = Field(default_factory=dict)
    interests: list[dict[str, Any]] = Field(default_factory=list)
    open_leads: list[dict[str, Any]] = Field(default_factory=list)
    matched_leads: list[dict[str, Any]] = Field(default_factory=list)
    leads_by_category: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    consent_status: str | None = None
