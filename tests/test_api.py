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
        json={"username": "admin_aashish", "password": "admin_aashish445*"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health_check_returns_ok():
    response = client.get("/api/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_supports_named_admin_accounts():
    for username in ("admin_aashish", "admin_bhavesh", "admin_kshitij"):
        response = client.post(
            "/api/login/",
            json={"username": username, "password": f"{username}445*"},
        )

        assert response.status_code == 200
        assert response.json()["access_token"]

    legacy = client.post(
        "/api/login/",
        json={"username": "admin", "password": "admin112###"},
    )
    assert legacy.status_code == 401


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
    (token_dir / "abc_tokens.json").write_text('{"access_token":"x"}', encoding="utf-8")

    status = mcp_exec.get_mcp_token_status(root, mcp_url="https://mcp-server.zomato.com/mcp")

    assert status["has_local_tokens"] is True
    assert status["local_files"] == ["mcp-remote-0.1.38/abc_tokens.json"]
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
        "files": {"mcp-remote-0.1.38/legacy_tokens.json": '{"access_token":"aashish"}'},
        "custom_providers": {"custom_legacy": {"label": "Legacy", "mcp_url": "https://legacy.example/mcp"}},
    }
    writes = []

    monkeypatch.setattr(mcp_exec, "_root_mcp_token_secret", lambda: root_secret)
    monkeypatch.setattr(mcp_exec, "_write_mcp_token_secret", writes.append)

    assert "files" in mcp_exec._mcp_token_secret("admin_aashish")
    assert mcp_exec._mcp_token_secret("admin_bhavesh") == {}

    merged = mcp_exec._merge_mcp_token_secret(
        "admin_bhavesh",
        files={"mcp-remote-0.1.38/new_tokens.json": '{"access_token":"bhavesh"}'},
    )

    assert merged["files"] == {"mcp-remote-0.1.38/new_tokens.json": '{"access_token":"bhavesh"}'}
    assert writes[-1]["files"] == root_secret["files"]
    assert writes[-1]["users"]["admin_bhavesh"]["files"] == merged["files"]


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
    monkeypatch.setattr(api_main, "route_food_query", lambda **_kwargs: plan)
    monkeypatch.setattr(
        api_main,
        "execute_tool_plan_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected execution")),
    )
    monkeypatch.setattr(api_main, "build_assistant_message_with_llm", lambda **_kwargs: "done")

    response = client.post(
        "/api/food/query/",
        json={"message": "pizza near me"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_execution"]["requested"] is False
    assert payload["zomato_summary"]["error"] == "zomato_not_executed"


def test_food_query_executes_requested_tool_plan(monkeypatch):
    plan = {
        "intent": "find_restaurant",
        "summary": "Find pizza restaurants.",
        "tool_plan": [{"tool": "get_saved_addresses_for_user", "arguments": {}}],
        "constraints": {},
    }
    calls = {}

    monkeypatch.setattr(
        api_main,
        "resolve_llm_from_env",
        lambda *_args, **_kwargs: (
            ResolvedLlm(provider="gemini", api_key="test", model="gemini-test"),
            None,
        ),
    )
    monkeypatch.setattr(api_main, "route_food_query", lambda **_kwargs: plan)

    def fake_execute_tool_plan_sync(tool_plan, **kwargs):
        calls["tool_plan"] = tool_plan
        calls["kwargs"] = kwargs
        return {
            "ok": True,
            "error": None,
            "routed_providers": ["zomato"],
            "provider_results": {"zomato": {"ok": True, "steps": [], "diagnostics": {}}},
        }

    monkeypatch.setattr(api_main, "execute_tool_plan_sync", fake_execute_tool_plan_sync)
    monkeypatch.setattr(api_main, "build_assistant_message_with_llm", lambda **_kwargs: "done")

    response = client.post(
        "/api/food/query/",
        json={"message": "pizza near me", "execute_tools": True},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert calls["tool_plan"] == plan["tool_plan"]
    assert response.json()["tool_execution"]["routed_providers"] == ["zomato"]
    assert response.json()["zomato_summary"]["error"] is None


def test_tool_provider_listing_includes_zomato_mcp():
    response = client.get(
        "/api/tools/providers/",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    providers = response.json()["providers"]
    assert providers[0]["id"] == "zomato"
    assert providers[0]["label"] == "Zomato MCP"
    ids = {provider["id"]: provider for provider in providers}
    assert ids["swiggy_food"]["available"] is True
    assert ids["swiggy_instamart"]["available"] is True
    assert ids["zepto"]["available"] is True
    assert ids["blinkit"]["available"] is False


def test_explicit_far_location_does_not_use_default_saved_address(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("should not call Zomato with default saved address")

    monkeypatch.setattr("app.services.tool_runtime.run_tool_plan_sync", fail_if_called)
    result = execute_tool_plan_sync(
        [
            {"tool": "get_saved_addresses_for_user", "arguments": {}},
            {
                "tool": "get_restaurants_for_keyword",
                "arguments": {"keyword": "pizza", "address_id": "__ADDRESS_ID__"},
            },
        ],
        user_message="Find pizza near Saket Delhi",
        constraints={"requested_location_text": "Saket, Delhi"},
    )

    assert result["ok"] is False
    assert result["error"] == "zomato_requires_saved_address_for_requested_location"
    assert result["provider_results"]["zomato"]["diagnostics"]["requested_location"] == "Saket, Delhi"


def test_menu_plan_gets_prerequisite_search_steps():
    plan = _finalize_from_parsed_json(
        {
            "intent": "inspect_menu",
            "summary": "Show pizza menu options.",
            "tool_plan": [
                {"tool": "get_restaurant_menu_by_categories", "arguments": {}},
            ],
            "constraints": {"inferred_keyword": "pizza"},
        },
        provider="gemini",
        model="gemini-test",
        trace_id="trace",
    )

    assert [step["tool"] for step in plan["tool_plan"]] == [
        "get_saved_addresses_for_user",
        "get_restaurants_for_keyword",
        "get_restaurant_menu_by_categories",
    ]
    assert plan["tool_plan"][1]["arguments"]["address_id"] == "__ADDRESS_ID__"
    assert plan["tool_plan"][2]["arguments"]["restaurant_id"] == "__RESTAURANT_ID__"
    assert plan["constraints"]["requires_address_resolution"] is True


def test_mcp_executor_learns_downstream_placeholders_from_prior_output():
    values = {}
    mcp_exec._learn_placeholders_from_payload(
        values,
        {
            "data": {
                "results": [
                    {
                        "restaurant_id": "res-123",
                        "items": [{"item_id": "item-9", "variant_id": "var-1"}],
                    }
                ],
                "cart_id": "cart-7",
            }
        },
    )

    assert mcp_exec.substitute_placeholders(
        {
            "restaurant_id": "__RESTAURANT_ID__",
            "item": "__ITEM_ID__",
            "variant": "__VARIANT_ID__",
            "cart_id": "__CART_ID__",
        },
        values,
    ) == {
        "restaurant_id": "res-123",
        "item": "item-9",
        "variant": "var-1",
        "cart_id": "cart-7",
    }


def test_compare_zomato_swiggy_uses_both_provider_plans():
    plan = route_food_query(
        user_message="Compare pizza on Zomato vs Swiggy near me",
        api_key="unused",
        model="unused",
        provider="gemini",
        tool_providers={"zomato", "swiggy_food"},
    )

    assert plan["intent"] == "compare_restaurant_providers"
    assert [step["tool"] for step in plan["tool_plan"]] == [
        "get_saved_addresses_for_user",
        "get_restaurants_for_keyword",
        "search_restaurants",
    ]
    assert plan["constraints"]["comparison_providers"] == ["swiggy_food", "zomato"]


def test_provider_selection_is_inferred_from_message():
    assert select_tool_providers_for_message("Compare pizza on Zomato vs Swiggy near me") == {
        "zomato",
        "swiggy_food",
    }
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
