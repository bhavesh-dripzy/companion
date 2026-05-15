import json
import os
import time

os.environ["LEGACY_ADMIN_USERNAME"] = "local_admin"
os.environ["LEGACY_ADMIN_PASSWORD"] = "test_password_123"

from fastapi.testclient import TestClient

from app.services.llm_config import ResolvedLlm
from companion_api.main import app
from companion_api import main as api_main
from app.services import zomato_mcp_executor as mcp_exec
from app.services.food_llm_router import _finalize_from_parsed_json
from app.services.food_llm_router import route_food_query
from app.services.food_response_builder import build_zomato_summary
from app.services.tool_runtime import (
    execute_tool_plan_sync,
    list_tool_providers,
    route_tool_plan,
    select_tool_providers_for_message,
)
from companion_api.reminder_parser import parse_reminder_message

import app.services.agents.pipeline as agent_pipeline

client = TestClient(app)


def stub_agent_intent(monkeypatch, *, food_providers: tuple[str, ...] | list[str] = ("zomato",)) -> None:
    monkeypatch.setattr(
        agent_pipeline,
        "classify_agent_routing",
        lambda **_kwargs: {
            "task_type": "food_delivery",
            "food_providers": list(food_providers),
            "reasoning": "test-stub",
        },
    )


def auth_headers() -> dict[str, str]:
    response = client.post(
        "/api/login/",
        json={"username": "local_admin", "password": "test_password_123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health_check_returns_ok():
    response = client.get("/api/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_uses_env_credentials():
    response = client.post(
        "/api/login/",
        json={"username": "local_admin", "password": "test_password_123"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_login_fails_with_wrong_credentials():
    response = client.post(
        "/api/login/",
        json={"username": "wrong", "password": "wrong"},
    )
    assert response.status_code == 401


def test_login_uses_cognito_when_enabled(monkeypatch):
    class FakeCognitoClient:
        def initiate_auth(self, **kwargs):
            assert kwargs == {
                "ClientId": "client-1",
                "AuthFlow": "USER_PASSWORD_AUTH",
                "AuthParameters": {
                    "USERNAME": "admin_aashish",
                    "PASSWORD": "password-1",
                },
            }
            return {"AuthenticationResult": {"AccessToken": "cognito-token"}}

    monkeypatch.setattr(api_main, "cognito_enabled", lambda: True)
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client-1")
    monkeypatch.setattr(api_main.boto3, "client", lambda *_args, **_kwargs: FakeCognitoClient())

    response = client.post(
        "/api/login/",
        json={"username": "admin_aashish", "password": "password-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "cognito-token",
        "token_type": "bearer",
        "username": "admin_aashish",
    }


def test_send_message_optional_llm_returns_whatsapp_url(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.post(
        "/api/send-message/",
        json={"message": "Dawai le aana", "llm_optional": True},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["whatsapp_url"] == "https://wa.me/919773687877?text=Dawai%20le%20aana"
    assert payload["llm"]["status"] == "skipped"


def test_parse_reminder_message_tomorrow_at_time():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    parsed = parse_reminder_message(
        "Remind me to call mom tomorrow at 9am",
        timezone_name="Asia/Kolkata",
        now=datetime(2026, 5, 15, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    assert parsed["title"] == "call mom"
    assert parsed["next_trigger"].isoformat() == "2026-05-16T09:00:00+05:30"


def test_parse_reminder_message_minutes_from_now():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    parsed = parse_reminder_message(
        "Create a reminder for 10 minutes from now to check the washing machine",
        timezone_name="Asia/Kolkata",
        now=datetime(2026, 5, 15, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    assert parsed["title"] == "check the washing machine"
    assert parsed["next_trigger"].isoformat() == "2026-05-15T12:10:00+05:30"


def test_parse_reminder_message_uses_local_timezone():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    parsed = parse_reminder_message(
        "Create a reminder for 10 minutes from now to check the washing machine",
        timezone_name="America/New_York",
        now=datetime(2026, 5, 15, 12, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert parsed["timezone"] == "America/New_York"
    assert parsed["next_trigger"].isoformat() == "2026-05-15T12:10:00-04:00"


def test_google_calendar_reminder_endpoint_creates_event(monkeypatch):
    calls = {}

    def fake_create_calendar_event(**kwargs):
        calls["event"] = kwargs
        return {"id": "evt-1", "htmlLink": "https://calendar.google.com/event?eid=evt-1", "summary": kwargs["title"]}

    class FakeScheduler:
        def create_reminder(self, payload):
            calls["reminder"] = payload
            return {"reminder_id": "rem-1", **payload}

    monkeypatch.setattr(api_main, "create_calendar_event", fake_create_calendar_event)
    monkeypatch.setattr(api_main, "ReminderScheduler", lambda: FakeScheduler())

    response = client.post(
        "/api/reminders/google-calendar/",
        json={"message": "Remind me to call mom tomorrow at 9am", "timezone": "Asia/Kolkata"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["calendar_event"]["id"] == "evt-1"
    assert body["parsed"]["title"] == "call mom"
    assert calls["event"]["email_reminder_minutes"] == 0
    assert calls["event"]["popup_reminder_minutes"] == 10
    assert calls["reminder"]["type"] == "google_calendar"

def test_google_calendar_oauth_start_reports_missing_config(monkeypatch):
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CALENDAR_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(api_main, "secret_or_env", lambda name, default="": default)
    monkeypatch.setattr("companion_api.google_calendar.secret_or_env", lambda name, default="": default)

    response = client.get(
        "/api/google-calendar/oauth/start",
        headers=auth_headers(),
    )

    assert response.status_code == 503
    assert "Google Calendar OAuth client is not configured" in response.json()["detail"]


def test_mcp_token_status_detects_nested_mcp_remote_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_REMOTE_CONFIG_DIR", str(tmp_path / ".mcp-auth"))
    monkeypatch.setattr(mcp_exec, "_mcp_token_secret", lambda _user_id=None: {})

    root = tmp_path / "repo"
    pkg = root / "node_modules" / "mcp-remote"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"version":"0.1.38"}', encoding="utf-8")
    token_dir = tmp_path / ".mcp-auth" / "mcp-remote-0.1.38"
    token_dir.mkdir(parents=True)
    token_rel = mcp_exec._mcp_remote_token_relative_paths(
        root,
        "https://mcp-server.zomato.com/mcp",
    )["tokens"]
    (tmp_path / ".mcp-auth" / token_rel).write_text('{"access_token":"x"}', encoding="utf-8")

    status = mcp_exec.get_mcp_token_status(root, mcp_url="https://mcp-server.zomato.com/mcp")

    assert status["has_local_tokens"] is True
    assert status["local_files"] == [token_rel]
    assert status["effective_config_dir"].endswith(".mcp-auth/mcp-remote-0.1.38")


def test_mcp_restore_preserves_nested_secret_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_REMOTE_CONFIG_DIR", str(tmp_path / ".mcp-auth"))
    monkeypatch.setattr(
        mcp_exec,
        "_mcp_token_secret",
        lambda _user_id=None: {"files": {"mcp-remote-0.1.38/abc_tokens.json": '{"access_token":"x"}'}},
    )

    assert mcp_exec.restore_mcp_auth_tokens(tmp_path) is True

    restored = tmp_path / ".mcp-auth" / "mcp-remote-0.1.38" / "abc_tokens.json"
    assert restored.read_text(encoding="utf-8") == '{"access_token":"x"}'


def test_mcp_restore_aliases_prior_mcp_remote_version(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_REMOTE_CONFIG_DIR", str(tmp_path / ".mcp-auth"))
    root = tmp_path / "repo"
    pkg = root / "node_modules" / "mcp-remote"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"version":"0.1.38"}', encoding="utf-8")
    token_hash = mcp_exec._server_url_hash("https://mcp.swiggy.com/food")
    old_rel = f"mcp-remote-0.1.37/{token_hash}_tokens.json"
    monkeypatch.setattr(
        mcp_exec,
        "_mcp_token_secret",
        lambda _user_id=None: {"files": {old_rel: '{"access_token":"swiggy"}'}},
    )

    assert mcp_exec.restore_mcp_auth_tokens(root, user_id="admin_aashish") is True

    restored_old = tmp_path / ".mcp-auth" / "users" / "admin_aashish" / old_rel
    restored_current = (
        tmp_path
        / ".mcp-auth"
        / "users"
        / "admin_aashish"
        / "mcp-remote-0.1.38"
        / f"{token_hash}_tokens.json"
    )
    assert restored_old.read_text(encoding="utf-8") == '{"access_token":"swiggy"}'
    assert restored_current.read_text(encoding="utf-8") == '{"access_token":"swiggy"}'


def test_mcp_secret_payloads_are_scoped_per_admin(monkeypatch):
    root_secret = {
        "files": {"mcp-remote-0.1.38/legacy_tokens.json": '{"access_token":"user_a_token"}'},
        "custom_providers": {"custom_legacy": {"label": "Legacy", "mcp_url": "https://legacy.example/mcp"}},
    }
    writes = []

    monkeypatch.setattr(mcp_exec, "_root_mcp_token_secret", lambda: root_secret)
    monkeypatch.setattr(mcp_exec, "_write_mcp_token_secret", writes.append)

    assert "files" in mcp_exec._mcp_token_secret(mcp_exec._LEGACY_MCP_OWNER)
    assert mcp_exec._mcp_token_secret("other_user") == {}

    merged = mcp_exec._merge_mcp_token_secret(
        "other_user",
        files={"mcp-remote-0.1.38/new_tokens.json": '{"access_token":"other_token"}'},
    )

    assert merged["files"] == {"mcp-remote-0.1.38/new_tokens.json": '{"access_token":"other_token"}'}
    assert writes[-1]["files"] == root_secret["files"]


def test_pairing_session_lookup_finds_user_scoped_code(monkeypatch):
    root_secret = {
        "users": {
            "admin_aashish": {
                "pairing_sessions": {
                    "pair-code": {
                        "created_at": int(time.time()),
                        "mcp_url": "https://mcp-server.zomato.com/mcp",
                        "mcp_remote_version": "0.1.38",
                    }
                }
            }
        }
    }

    monkeypatch.setattr(mcp_exec, "_root_mcp_token_secret", lambda: root_secret)

    session = mcp_exec.find_pairing_session_info("pair-code")

    assert session is not None
    assert session["user_id"] == "admin_aashish"
    assert session["mcp_url"] == "https://mcp-server.zomato.com/mcp"


def test_mcp_token_status_is_scoped_to_selected_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_exec, "_mcp_remote_config_dir", lambda _root, user_id=None: tmp_path)
    monkeypatch.setattr(mcp_exec, "_mcp_remote_version", lambda _root: "0.1.38")

    zomato_rel = mcp_exec._mcp_remote_token_relative_paths(
        tmp_path,
        "https://mcp-server.zomato.com/mcp",
    )["tokens"]
    monkeypatch.setattr(
        mcp_exec,
        "_mcp_token_secret",
        lambda _user_id=None: {"files": {zomato_rel: '{"access_token":"zomato"}'}},
    )

    zomato = mcp_exec.get_mcp_token_status(
        tmp_path,
        mcp_url="https://mcp-server.zomato.com/mcp",
        user_id="admin_aashish",
    )
    zepto = mcp_exec.get_mcp_token_status(
        tmp_path,
        mcp_url="https://mcp.zepto.co.in/mcp",
        user_id="admin_aashish",
    )

    assert zomato["has_secret_tokens"] is True
    assert zepto["has_secret_tokens"] is False


def test_mcp_token_status_accepts_prior_mcp_remote_version(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_exec, "_mcp_remote_config_dir", lambda _root, user_id=None: tmp_path)
    monkeypatch.setattr(mcp_exec, "_mcp_remote_version", lambda _root: "0.1.38")
    token_hash = mcp_exec._server_url_hash("https://mcp-server.zomato.com/mcp")
    old_version_rel = f"mcp-remote-0.1.37/{token_hash}_tokens.json"
    monkeypatch.setattr(
        mcp_exec,
        "_mcp_token_secret",
        lambda _user_id=None: {"files": {old_version_rel: '{"access_token":"zomato"}'}},
    )

    status = mcp_exec.get_mcp_token_status(
        tmp_path,
        mcp_url="https://mcp-server.zomato.com/mcp",
        user_id="admin_aashish",
    )

    assert status["has_secret_tokens"] is True


def test_food_query_is_plan_only_by_default(monkeypatch):
    stub_agent_intent(monkeypatch)

    monkeypatch.setattr(
        api_main,
        "resolve_llm_from_env",
        lambda *_args, **_kwargs: (
            ResolvedLlm(provider="gemini", api_key="test", model="gemini-test"),
            None,
        ),
    )
    monkeypatch.setattr(
        agent_pipeline,
        "build_assistant_message_with_llm",
        lambda **_kwargs: "done",
    )

    monkeypatch.setattr(
        agent_pipeline,
        "route_food_query",
        lambda **kwargs: _finalize_from_parsed_json(
            {
                "intent": "find_restaurant",
                "summary": "pizza find",
                "tool_plan": [{"tool": "get_saved_addresses_for_user", "arguments": {}}],
                "constraints": {},
                "execution_notes_for_downstream": "ok",
            },
            provider="gemini",
            model="gemini-2.5-flash-lite",
            trace_id="trace-1",
            tool_provider="zomato",
        ),
    )

    monkeypatch.setattr(
        agent_pipeline,
        "execute_tool_plan_sync",
        lambda **kwargs: {
            "ok": True,
            "error": None,
            "routed_providers": ["zomato"],
            "provider_results": {"zomato": {"ok": True, "steps": [], "diagnostics": {}}},
        },
    )

    response = client.post(
        "/api/food/query/",
        json={"message": "pizza near me", "address_id": "saved-addr-1"},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert isinstance(response.json()["assistant_message"], str)


def test_food_query_is_routed_through_tool_plan(monkeypatch):
    stub_agent_intent(monkeypatch)

    monkeypatch.setattr(
        api_main,
        "resolve_llm_from_env",
        lambda *_args, **_kwargs: (
            ResolvedLlm(provider="gemini", api_key="test", model="gemini-test"),
            None,
        ),
    )
    monkeypatch.setattr(
        agent_pipeline,
        "build_assistant_message_with_llm",
        lambda **_kwargs: "done",
    )

    monkeypatch.setattr(
        agent_pipeline,
        "route_food_query",
        lambda **kwargs: _finalize_from_parsed_json(
            {
                "intent": "find_restaurant",
                "summary": "pizza find",
                "tool_plan": [{"tool": "get_saved_addresses_for_user", "arguments": {}}],
                "constraints": {},
                "execution_notes_for_downstream": "ok",
            },
            provider="gemini",
            model="gemini-2.5-flash-lite",
            trace_id="trace-2",
            tool_provider="zomato",
        ),
    )

    calls = {}

    def fake_execute(tool_plan=None, **kwargs):
        calls.update(kwargs)
        calls["tool_plan"] = tool_plan
        return {
            "ok": True,
            "error": None,
            "routed_providers": ["zomato"],
            "provider_results": {"zomato": {"ok": True, "steps": [], "diagnostics": {}}},
        }

    monkeypatch.setattr(agent_pipeline, "execute_tool_plan_sync", fake_execute)

    response = client.post(
        "/api/food/query/",
        json={"message": "pizza near me", "execute_tools": True, "address_id": "saved-addr-1"},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert "tool_plan" in calls
    assert calls["enabled_providers"] == {"zomato"}


def test_food_query_requires_address_id():
    response = client.post(
        "/api/food/query/",
        json={"message": "pizza near me"},
        headers=auth_headers(),
    )
    assert response.status_code == 422


def test_food_saved_addresses_returns_rows(monkeypatch):
    monkeypatch.setattr(
        mcp_exec,
        "get_mcp_token_status",
        lambda *_a, **_k: {"has_local_tokens": True, "has_secret_tokens": False},
    )
    monkeypatch.setattr(
        mcp_exec,
        "run_tool_plan_sync",
        lambda *_args, **_kwargs: {
            "ok": True,
            "error_detail": None,
            "steps": [
                {
                    "tool": "get_saved_addresses_for_user",
                    "arguments": {},
                    "result": {
                        "is_error": False,
                        "data": {
                            "result": {
                                "addresses": [
                                    {
                                        "address_id": "111",
                                        "location_name": "Test Tower, City",
                                    }
                                ],
                                "count": 1,
                                "success": True,
                            }
                        },
                    },
                }
            ],
            "diagnostics": {},
        },
    )

    response = client.get("/api/food/saved-addresses/", headers=auth_headers())
    assert response.status_code == 200
    body = response.json()
    if os.environ.get("SHOW_SAVED_ADDR_RESPONSE") == "1":
        import json

        print(json.dumps(body, indent=2))
    assert body["status"] == "success"
    assert body["count"] == 1
    assert body["addresses"][0]["address_id"] == "111"
    assert "Test Tower" in body["addresses"][0]["location_name"]


def test_list_tool_providers_includes_zomato_and_swiggy():
    providers = {provider["id"]: provider for provider in list_tool_providers()}
    assert providers["zomato"]["label"] == "Zomato MCP"
    assert "get_saved_addresses_for_user" in providers["zomato"]["tools"]
    assert providers["swiggy_food"]["label"] == "Swiggy Food MCP"
    assert "search_restaurants" in providers["swiggy_food"]["tools"]
    assert "get_addresses" in providers["swiggy_food"]["tools"]
    assert "search_menu" in providers["swiggy_food"]["tools"]
    assert providers["swiggy_instamart"]["label"] == "Swiggy Instamart MCP"
    assert "search_products" in providers["swiggy_instamart"]["tools"]


def test_select_tool_providers_for_message_chooses_correct_providers():
    # Basic food queries go to Zomato
    assert select_tool_providers_for_message("Order pizza") == {"zomato"}
    assert select_tool_providers_for_message("Find biryani near me") == {"zomato"}

    # Grocery queries prefer Zepto over Swiggy Instamart
    assert select_tool_providers_for_message("Buy milk and eggs") == {"zepto"}
    assert select_tool_providers_for_message("Order groceries") == {"zepto"}

    # Explicit comparison includes multiple providers
    assert select_tool_providers_for_message("Compare pizza on Zomato and Swiggy") == {
        "zomato",
        "swiggy_food",
    }

    # Zepto queries
    assert select_tool_providers_for_message("Search milk and brown bread") == {"zepto"}
    assert select_tool_providers_for_message("Find biryani near me") == {"zomato"}


def test_empty_tool_plan_is_not_success():
    result = execute_tool_plan_sync([], enabled_providers={"swiggy_food"})

    assert result["ok"] is False
    assert result["error"] == "no_tool_plan"
    assert result["routed_providers"] == []


def test_finalize_fills_empty_swiggy_food_tool_plan():
    """DeepSeek sometimes returns intent + constraints but tool_plan: []."""
    from app.services.food_llm_router import _finalize_from_parsed_json

    out = _finalize_from_parsed_json(
        {
            "intent": "search_menu_items",
            "summary": "User wants burger delivery options.",
            "tool_plan": [],
            "constraints": {"query": "burger"},
            "execution_notes_for_downstream": "",
        },
        provider="deepseek",
        model="deepseek-chat",
        trace_id="t-heal",
        tool_provider="swiggy_food",
        allowed_tools=None,
    )
    tools = [s.get("tool") for s in out["tool_plan"]]
    assert "get_addresses" in tools
    assert "search_restaurants" in tools
    sr = next(s for s in out["tool_plan"] if s.get("tool") == "search_restaurants")
    assert sr["arguments"].get("query") == "burger"


def test_finalize_shortens_swiggy_search_and_appends_menu():
    from app.services.food_llm_router import _finalize_from_parsed_json

    out = _finalize_from_parsed_json(
        {
            "intent": "get_restaurant_menu",
            "summary": "User wants to see the veg menu for Galaxy Pizza.",
            "tool_plan": [
                {"tool": "get_addresses", "arguments": {}},
                {
                    "tool": "search_restaurants",
                    "arguments": {"query": "User wants to see the veg menu for Galaxy Pizza."},
                },
            ],
            "constraints": {"restaurant_name": "Galaxy Pizza", "menu_type": "veg"},
            "execution_notes_for_downstream": "",
        },
        provider="deepseek",
        model="deepseek-chat",
        trace_id="t-menu",
        tool_provider="swiggy_food",
        allowed_tools=None,
    )
    sr = next(s for s in out["tool_plan"] if s.get("tool") == "search_restaurants")
    assert sr["arguments"].get("query") == "Galaxy Pizza"
    assert any(s.get("tool") == "get_restaurant_menu" for s in out["tool_plan"])


def test_provider_hints_route_shared_tool_names():
    routed = route_tool_plan(
        [
            {"provider": "zomato", "tool": "get_order_history", "arguments": {}},
            {"provider": "swiggy_food", "tool": "get_food_orders", "arguments": {}},
        ],
        enabled_providers={"zomato", "swiggy_food"},
    )

    assert set(routed) == {"zomato", "swiggy_food"}
    assert len(routed["zomato"]) == 1
    assert len(routed["swiggy_food"]) == 1


def test_order_history_router_uses_deterministic_provider_plan():
    plan = route_food_query(
        user_message="Check where did I last order from in both Swiggy and Zomato",
        api_key="unused",
        model="unused",
        provider="gemini",
        tool_providers={"zomato", "swiggy_food"},
    )

    assert plan["intent"] == "get_order_history"
    assert plan["validation"]["resolved_model"] == "deterministic-order-history-router"
    assert plan["tool_plan"] == [
        {"provider": "swiggy_food", "tool": "get_food_orders", "arguments": {}},
        {"provider": "zomato", "tool": "get_order_history", "arguments": {}},
    ]


def test_compare_query_executes_multiple_tool_providers(monkeypatch):
    calls = {}

    stub_agent_intent(monkeypatch, food_providers=("zomato", "swiggy_food"))

    monkeypatch.setattr(
        api_main,
        "resolve_llm_from_env",
        lambda *_args, **_kwargs: (
            ResolvedLlm(provider="gemini", api_key="test", model="gemini-test"),
            None,
        ),
    )
    monkeypatch.setattr(
        agent_pipeline,
        "build_assistant_message_with_llm",
        lambda **_kwargs: "done",
    )

    def fake_execute_tool_plan_sync(tool_plan, **kwargs):
        calls["tool_plan"] = tool_plan
        calls["kwargs"] = kwargs
        return {
            "ok": True,
            "error": None,
            "routed_providers": ["swiggy_food", "zomato"],
            "provider_results": {
                "zomato": {"ok": True, "steps": [], "diagnostics": {}},
                "swiggy_food": {"ok": True, "steps": [], "diagnostics": {}},
            },
        }

    monkeypatch.setattr(agent_pipeline, "execute_tool_plan_sync", fake_execute_tool_plan_sync)

    response = client.post(
        "/api/food/query/",
        json={
            "message": "Compare pizza on Zomato vs Swiggy near me",
            "execute_tools": True,
            "tool_providers": ["zomato", "swiggy_food"],
            "address_id": "saved-addr-1",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert calls["kwargs"]["enabled_providers"] == {"zomato", "swiggy_food"}
    assert response.json()["tool_execution"]["routed_providers"] == ["swiggy_food", "zomato"]
    assert set(response.json()["provider_summaries"]) == {"zomato", "swiggy_food"}
    trace = response.json()["observable_trace"]
    assert trace[0]["stage"] == "intent_orchestration"
    assert trace[1]["stage"] == "provider_selection"
    assert trace[1]["providers"] == ["swiggy_food", "zomato"]
    assert any(item["stage"] == "provider_execution" for item in trace)


def test_food_query_honors_single_selected_tool_provider(monkeypatch):
    calls = {}

    stub_agent_intent(monkeypatch, food_providers=("zepto",))

    monkeypatch.setattr(
        api_main,
        "resolve_llm_from_env",
        lambda *_args, **_kwargs: (
            ResolvedLlm(provider="gemini", api_key="test", model="gemini-test"),
            None,
        ),
    )
    monkeypatch.setattr(
        agent_pipeline,
        "build_assistant_message_with_llm",
        lambda **_kwargs: "done",
    )
    monkeypatch.setattr(
        agent_pipeline,
        "route_food_query",
        lambda **kwargs: _finalize_from_parsed_json(
            {
                "intent": "find_products",
                "summary": "milk search",
                "tool_plan": [{"tool": "search_products", "arguments": {"query": "milk"}}],
                "constraints": {},
            },
            provider="gemini",
            model="gemini-test",
            trace_id="trace-selected-provider",
            tool_provider="zepto",
        ),
    )

    def fake_execute_tool_plan_sync(tool_plan, **kwargs):
        calls["kwargs"] = kwargs
        return {
            "ok": True,
            "error": None,
            "routed_providers": ["zepto"],
            "provider_results": {
                "zepto": {
                    "ok": True,
                    "steps": [{"tool": "search_products"}],
                    "diagnostics": {},
                }
            },
        }

    monkeypatch.setattr(agent_pipeline, "execute_tool_plan_sync", fake_execute_tool_plan_sync)

    response = client.post(
        "/api/food/query/",
        json={
            "message": "Search milk",
            "execute_tools": True,
            "tool_provider": "zepto",
            "address_id": "saved-addr-1",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert calls["kwargs"]["enabled_providers"] == {"zepto"}
    assert response.json()["selected_tool_providers"] == ["zepto"]
    trace = response.json()["observable_trace"]
    assert trace[0]["stage"] == "intent_orchestration"
    assert trace[1]["providers"] == ["zepto"]


def test_food_query_routes_by_message_when_no_tool_provider_forced(monkeypatch):
    calls = {}

    stub_agent_intent(monkeypatch, food_providers=("zepto",))

    monkeypatch.setattr(
        api_main,
        "resolve_llm_from_env",
        lambda *_args, **_kwargs: (
            ResolvedLlm(provider="gemini", api_key="test", model="gemini-test"),
            None,
        ),
    )
    monkeypatch.setattr(
        agent_pipeline,
        "build_assistant_message_with_llm",
        lambda **_kwargs: "done",
    )
    monkeypatch.setattr(
        agent_pipeline,
        "route_food_query",
        lambda **kwargs: _finalize_from_parsed_json(
            {
                "intent": "get_order_history",
                "summary": "zepto order history",
                "tool_plan": [{"tool": "get_order_history", "arguments": {}}],
                "constraints": {},
            },
            provider="gemini",
            model="gemini-test",
            trace_id="trace-message-routing",
            tool_provider="zepto",
        ),
    )

    def fake_execute_tool_plan_sync(tool_plan, **kwargs):
        calls["kwargs"] = kwargs
        return {
            "ok": False,
            "error": "provider_execution_failed",
            "routed_providers": ["zepto"],
            "provider_results": {
                "zepto": {
                    "ok": False,
                    "error_detail": "tool_plan_incomplete",
                    "steps": [{"tool": "get_order_history"}],
                    "diagnostics": {},
                }
            },
        }

    monkeypatch.setattr(agent_pipeline, "execute_tool_plan_sync", fake_execute_tool_plan_sync)

    response = client.post(
        "/api/food/query/",
        json={
            "message": "What was my last order from zepto",
            "execute_tools": True,
            "address_id": "saved-addr-1",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert calls["kwargs"]["enabled_providers"] == {"zepto"}
    assert response.json()["selected_tool_providers"] == ["zepto"]


def test_zepto_provider_uses_official_endpoint_tooling():
    providers = {provider["id"]: provider for provider in list_tool_providers()}
    assert providers["zepto"]["available"] is True
    assert "search_products" in providers["zepto"]["tools"]


def test_custom_mcp_can_be_added_listed_and_removed(monkeypatch):
    store = {}

    def fake_secret(_user_id=None):
        return dict(store)

    def fake_merge(_user_id=None, **updates):
        store.update(updates)
        return dict(store)

    monkeypatch.setattr("app.services.tool_runtime._mcp_token_secret", fake_secret)
    monkeypatch.setattr("app.services.tool_runtime._merge_mcp_token_secret", fake_merge)

    response = client.post(
        "/api/tools/providers/custom/",
        json={"label": "Linear MCP", "mcp_url": "https://linear.example.com/mcp"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    provider_id = response.json()["provider"]["id"]
    assert provider_id.startswith("custom_linear_mcp")

    listed = client.get("/api/tools/providers/", headers=auth_headers())
    providers = {provider["id"]: provider for provider in listed.json()["providers"]}
    assert providers[provider_id]["custom"] is True
    assert providers[provider_id]["mcp_url"] == "https://linear.example.com/mcp"

    deleted = client.delete(
        f"/api/tools/providers/custom/{provider_id}",
        headers=auth_headers(),
    )
    assert deleted.status_code == 200
    assert provider_id not in store.get("custom_providers", {})


def test_zapier_mcp_is_url_authenticated_custom_provider(monkeypatch):
    store = {}

    def fake_secret(_user_id=None):
        return dict(store)

    def fake_merge(_user_id=None, **updates):
        store.update(updates)
        return dict(store)

    monkeypatch.setattr("app.services.tool_runtime._mcp_token_secret", fake_secret)
    monkeypatch.setattr("app.services.tool_runtime._merge_mcp_token_secret", fake_merge)

    response = client.post(
        "/api/tools/providers/zapier/",
        json={"mcp_url": "https://mcp.zapier.com/api/v1/connect/example"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    provider = response.json()["provider"]
    assert provider["id"] == "zapier_mcp"
    assert provider["label"] == "Zapier MCP"
    assert provider["custom"] is True
    assert provider["auth_mode"] == "url"

    listed = client.get("/api/tools/providers/", headers=auth_headers())
    providers = {provider["id"]: provider for provider in listed.json()["providers"]}
    assert providers["zapier_mcp"]["mcp_url"] == "https://mcp.zapier.com/api/v1/connect/example"
    assert providers["zapier_mcp"]["auth_mode"] == "url"


def test_url_authenticated_mcp_executes_without_token_precheck(monkeypatch):
    store = {
        "custom_providers": {
            "zapier_mcp": {
                "id": "zapier_mcp",
                "label": "Zapier MCP",
                "mcp_url": "https://mcp.zapier.com/api/v1/connect/example",
                "auth_mode": "url",
                "tools": ["google_calendar_create_detailed_event"],
            }
        }
    }
    calls = {}

    monkeypatch.setattr("app.services.tool_runtime._mcp_token_secret", lambda _user_id=None: dict(store))

    def fake_run_tool_plan_sync(plan, **kwargs):
        calls["run"] = {"plan": plan, "kwargs": kwargs}
        return {"ok": True, "steps": [{"tool": plan[0]["tool"], "ok": True}]}

    monkeypatch.setattr("app.services.tool_runtime.run_tool_plan_sync", fake_run_tool_plan_sync)

    result = execute_tool_plan_sync(
        [{"tool": "google_calendar_create_detailed_event", "arguments": {"summary": "Check washer"}}],
        enabled_providers={"zapier_mcp"},
        user_id="local_admin",
    )

    assert result["ok"] is True
    assert result["routed_providers"] == ["zapier_mcp"]
    assert calls["run"]["kwargs"]["mcp_url"] == "https://mcp.zapier.com/api/v1/connect/example"


def test_operational_apis_require_auth():
    checks = [
        client.post("/api/send-message/", json={"message": "hi", "llm_optional": True}),
        client.post("/api/food/query/", json={"message": "pizza", "address_id": "x"}),
        client.post("/api/agent/query/", json={"message": "pizza", "address_id": "x"}),
        client.get("/api/food/saved-addresses/"),
        client.get("/api/tools/providers/"),
        client.post(
            "/api/tools/providers/custom/",
            json={"label": "X", "mcp_url": "https://example.com/mcp"},
        ),
        client.get("/api/mcp/tools/?provider=zomato"),
        client.get("/api/mcp/status/"),
        client.get("/api/google-calendar/status/"),
        client.get("/api/google-calendar/oauth/start"),
        client.post("/api/reminders/google-calendar/", json={"message": "Remind me tomorrow at 9am"}),
        client.post(
            "/api/reminders/respond/",
            json={"member_id": "m", "reminder_id": "r", "response": "yes"},
        ),
    ]

    assert all(response.status_code == 401 for response in checks)


def test_build_zomato_summary_address_matches_resolved_id_not_first_saved():
    """Saved-address list order must not override the address_id used for search."""
    zomato = {
        "ok": True,
        "diagnostics": {
            "resolved_address_id": "967909546",
            "requested_address_id": "967909546",
        },
        "steps": [
            {
                "tool": "get_saved_addresses_for_user",
                "arguments": {},
                "result": {
                    "data": {
                        "result": {
                            "addresses": [
                                {
                                    "address_id": "967910141",
                                    "location_name": "HSR Layout, Bangalore",
                                },
                                {
                                    "address_id": "967909546",
                                    "location_name": "Sector 38, Gurgaon",
                                },
                            ]
                        }
                    }
                },
            },
            {
                "tool": "get_restaurants_for_keyword",
                "arguments": {"address_id": "967909546", "keyword": "pizza"},
                "result": {"data": {"results": []}},
            },
        ],
    }
    summary = build_zomato_summary(zomato)
    assert summary["address"] == "Sector 38, Gurgaon"


def test_build_zomato_summary_address_falls_back_to_first_saved_when_no_diagnostics():
    zomato = {
        "ok": True,
        "diagnostics": {},
        "steps": [
            {
                "tool": "get_saved_addresses_for_user",
                "arguments": {},
                "result": {
                    "data": {
                        "result": {
                            "addresses": [
                                {
                                    "address_id": "967910141",
                                    "location_name": "Only one",
                                },
                            ]
                        }
                    }
                },
            },
        ],
    }
    assert build_zomato_summary(zomato)["address"] == "Only one"


def test_build_zomato_summary_menu_items_from_display_name_and_skips_category_buckets():
    zomato = {
        "ok": True,
        "diagnostics": {},
        "steps": [
            {
                "tool": "get_restaurant_menu_by_categories",
                "arguments": {"restaurant_id": "123"},
                "result": {
                    "data": {
                        "result": {
                            "categories": [
                                {
                                    "name": "Burgers",
                                    "items": [
                                        {
                                            "display_name": "Classic Burger",
                                            "price": 199,
                                        },
                                        {
                                            "nm": "Cheese fries",
                                            "sp": 89,
                                        },
                                    ],
                                }
                            ]
                        }
                    }
                },
            },
        ],
    }
    items = build_zomato_summary(zomato)["menu_items"]
    names = [i["name"] for i in items]
    assert "Classic Burger" in names
    assert "Cheese fries" in names
    assert "Burgers" not in names


def test_build_zomato_summary_menu_parses_string_json_data():
    inner = json.dumps(
        {
            "result": {
                "items": [
                    {"title": "Paneer wrap", "mrp": 149},
                ]
            }
        }
    )
    zomato = {
        "ok": True,
        "steps": [
            {
                "tool": "get_menu_items_listing",
                "arguments": {"restaurant_id": "1"},
                "result": {"data": inner},
            },
        ],
    }
    items = build_zomato_summary(zomato)["menu_items"]
    assert len(items) == 1
    assert items[0]["name"] == "Paneer wrap"
