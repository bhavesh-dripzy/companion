"""Unit tests for the KG/leadgen seam adapters and no-op doubles."""

import asyncio

import pytest

from core.boundaries.null_impls import EmptyProfileService, NoopPublisher
from core.boundaries.profile import CachedProfileService
from core.boundaries.publisher import SqsTurnEventPublisher
from core.contracts.turn_event import TurnEvent

pytestmark = pytest.mark.unit


def test_noop_publisher_captures_and_empty_profile_returns_none():
    pub = NoopPublisher()
    event = TurnEvent(user_id="u1", turn_id="t1", user_message="hi", assistant_message="hello")
    asyncio.run(pub.publish(event))
    assert pub.published == [event]
    assert asyncio.run(EmptyProfileService().get("u1")) is None


def test_cached_profile_service_parses_and_is_fail_open(monkeypatch):
    monkeypatch.setattr(
        CachedProfileService,
        "_read",
        staticmethod(lambda uid: {"name": "Aashish", "city": "Bengaluru"}),
    )
    profile = asyncio.run(CachedProfileService().get("u1"))
    assert profile.user_id == "u1" and profile.name == "Aashish"

    def _boom(uid):
        raise RuntimeError("dynamo down")

    monkeypatch.setattr(CachedProfileService, "_read", staticmethod(_boom))
    assert asyncio.run(CachedProfileService().get("u1")) is None  # fail-open


def test_sqs_publisher_swallows_errors(monkeypatch):
    sent = []
    monkeypatch.setattr(
        SqsTurnEventPublisher, "_send", staticmethod(lambda event: sent.append(event))
    )
    event = TurnEvent(user_id="u1", turn_id="t1", user_message="hi", assistant_message="hello")
    asyncio.run(SqsTurnEventPublisher().publish(event))
    assert sent == [event]

    def _boom(event):
        raise RuntimeError("sqs down")

    monkeypatch.setattr(SqsTurnEventPublisher, "_send", staticmethod(_boom))
    asyncio.run(SqsTurnEventPublisher().publish(event))  # must not raise
