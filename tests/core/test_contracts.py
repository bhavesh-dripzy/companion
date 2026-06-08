"""Unit tests for the core data contracts."""

import pytest

from core.contracts import (
    Attachment,
    AuthMode,
    ConfirmPolicy,
    ErrorCode,
    Profile,
    RoutingDecision,
    SessionState,
    Tool,
    ToolCall,
    ToolPlan,
    ToolResult,
)

pytestmark = pytest.mark.unit


def test_tool_matches_name_and_aliases():
    tool = Tool(name="get_addresses", provider="swiggy_food", aliases=("get_user_addresses",))
    assert tool.matches("get_addresses")
    assert tool.matches("get_user_addresses")
    assert not tool.matches("search_menu")


def test_tool_is_frozen():
    tool = Tool(name="x", provider="p")
    assert tool.auth_mode is AuthMode.OAUTH
    assert tool.confirm is ConfirmPolicy.NEVER
    with pytest.raises(Exception):
        tool.name = "y"


def test_tool_plan_empty():
    assert ToolPlan().is_empty
    assert not ToolPlan(calls=[ToolCall(tool="search_restaurants")]).is_empty


def test_tool_result_failed_flag():
    ok = ToolResult(tool="t", provider="p", ok=True)
    bad = ToolResult(tool="t", provider="p", ok=False, error=ErrorCode.AUTH_REQUIRED)
    assert not ok.failed
    assert bad.failed and bad.error is ErrorCode.AUTH_REQUIRED


def test_session_slice_for_returns_namespaced_slice():
    state = SessionState(providers={"zomato": {"cart": {"cart_id": "c1"}}})
    assert state.slice_for("zomato") == {"cart": {"cart_id": "c1"}}
    assert state.slice_for("missing") == {}


def test_attachment_to_wire_flattens_extra_fields():
    att = Attachment(kind="menu_card", res_id="r1", items=[{"name": "Pizza"}])
    wire = att.to_wire()
    assert wire["kind"] == "menu_card"
    assert wire["res_id"] == "r1"
    assert wire["items"] == [{"name": "Pizza"}]


def test_routing_decision_single_provider():
    assert RoutingDecision(providers=["zomato"]).names_single_provider
    assert not RoutingDecision(providers=["zomato", "swiggy_food"]).names_single_provider


def test_profile_parses_loose_cache_shape():
    profile = Profile.model_validate(
        {
            "user_id": "u1",
            "age": "30",  # cache may store a string
            "addresses": {"home": {"provider_address_id": "A1", "extra": 1}},
            "interests": [{"category": "food", "detail": "ramen"}],
        }
    )
    assert profile.user_id == "u1"
    assert profile.addresses["home"].provider_address_id == "A1"
