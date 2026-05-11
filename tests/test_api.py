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
from app.services.tool_runtime import (
    execute_tool_plan_sync,
    list_tool_providers,
    select_tool_providers_for_message,
)

client = TestClient(app)


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


def test_food_query_is_plan_only_by_default(monkeypatch):
    plan = {
        "intent": "find_restaurant",
        "summary": "Find pizza restaurants.",
        "tool_plan": [{"tool": "get_saved_addresses_for_user", "arguments": {}}],
        "constraints": {},
    }

    monkeypatch.setattr(
        api_main,
        "resolve_llm_from_env",
        lambda *_args, **_kwargs: (
            ResolvedLlm(provider="gemini", api_key="test", model="gemini-test"),
            None,
        ),
    )
    monkeypatch.setattr(api_main, "build_assistant_message_with_llm", lambda **_kwargs: "done")

    monkeypatch.setattr(
        api_main, "route_food_query",
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
        ),
    )

    monkeypatch.setattr(
        api_main, "execute_tool_plan_sync",
        lambda **kwargs: {
            "ok": True,
            "error": None,
            "routed_providers": ["zomato"],
            "provider_results": {"zomato": {"ok": True, "steps": [], "diagnostics": {}}},
        },
    )

    response = client.post(
        "/api/food/query/",
        json={"message": "pizza near me"},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert isinstance(response.json()["assistant_message"], str)


def test_food_query_is_routed_through_tool_plan(monkeypatch):
    monkeypatch.setattr(
        api_main,
        "resolve_llm_from_env",
        lambda *_args, **_kwargs: (
            ResolvedLlm(provider="gemini", api_key="test", model="gemini-test"),
            None,
        ),
    )
    monkeypatch.setattr(api_main, "build_assistant_message_with_llm", lambda **_kwargs: "done")

    monkeypatch.setattr(
        api_main, "route_food_query",
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

    monkeypatch.setattr(api_main, "execute_tool_plan_sync", fake_execute)

    response = client.post(
        "/api/food/query/",
        json={"message": "pizza near me", "execute_tools": True},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert "tool_plan" in calls
    assert calls["enabled_providers"] == {"zomato"}


def test_list_tool_providers_includes_zomato_and_swiggy():
    providers = {provider["id"]: provider for provider in list_tool_providers()}
    assert providers["zomato"]["label"] == "Zomato MCP"
    assert "get_saved_addresses_for_user" in providers["zomato"]["tools"]
    assert providers["swiggy_food"]["label"] == "Swiggy Food MCP"
    assert "search_restaurants" in providers["swiggy_food"]["tools"]
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


def test_compare_query_executes_multiple_tool_providers(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        api_main,
        "resolve_llm_from_env",
        lambda *_args, **_kwargs: (
            ResolvedLlm(provider="gemini", api_key="test", model="gemini-test"),
            None,
        ),
    )
    monkeypatch.setattr(api_main, "build_assistant_message_with_llm", lambda **_kwargs: "done")

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

    monkeypatch.setattr(api_main, "execute_tool_plan_sync", fake_execute_tool_plan_sync)

    response = client.post(
        "/api/food/query/",
        json={
            "message": "Compare pizza on Zomato vs Swiggy near me",
            "execute_tools": True,
            "tool_providers": ["zomato", "swiggy_food"],
        },
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert calls["kwargs"]["enabled_providers"] == {"zomato", "swiggy_food"}
    assert response.json()["tool_execution"]["routed_providers"] == ["swiggy_food", "zomato"]
    assert set(response.json()["provider_summaries"]) == {"zomato", "swiggy_food"}
    trace = response.json()["observable_trace"]
    assert trace[0]["stage"] == "provider_selection"
    assert trace[0]["providers"] == ["swiggy_food", "zomato"]
    assert any(item["stage"] == "provider_execution" for item in trace)


def test_food_query_honors_single_selected_tool_provider(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        api_main,
        "resolve_llm_from_env",
        lambda *_args, **_kwargs: (
            ResolvedLlm(provider="gemini", api_key="test", model="gemini-test"),
            None,
        ),
    )
    monkeypatch.setattr(api_main, "build_assistant_message_with_llm", lambda **_kwargs: "done")
    monkeypatch.setattr(
        api_main,
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

    monkeypatch.setattr(api_main, "execute_tool_plan_sync", fake_execute_tool_plan_sync)

    response = client.post(
        "/api/food/query/",
        json={
            "message": "Search milk",
            "execute_tools": True,
            "tool_provider": "zepto",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert calls["kwargs"]["enabled_providers"] == {"zepto"}
    assert response.json()["selected_tool_providers"] == ["zepto"]
    assert response.json()["observable_trace"][0]["providers"] == ["zepto"]


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


def test_operational_apis_require_auth():
    checks = [
        client.post("/api/send-message/", json={"message": "hi", "llm_optional": True}),
        client.post("/api/food/query/", json={"message": "pizza"}),
        client.get("/api/tools/providers/"),
        client.post(
            "/api/tools/providers/custom/",
            json={"label": "X", "mcp_url": "https://example.com/mcp"},
        ),
        client.get("/api/mcp/tools/?provider=zomato"),
        client.get("/api/mcp/status/"),
        client.post(
            "/api/reminders/respond/",
            json={"member_id": "m", "reminder_id": "r", "response": "yes"},
        ),
    ]

    assert all(response.status_code == 401 for response in checks)
