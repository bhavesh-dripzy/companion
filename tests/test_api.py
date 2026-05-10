from fastapi.testclient import TestClient

from app.services.llm_config import ResolvedLlm
from companion_api.main import app
from companion_api import main as api_main
from app.services import zomato_mcp_executor as mcp_exec


client = TestClient(app)


def test_health_check_returns_ok():
    response = client.get("/api/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_send_message_optional_llm_returns_whatsapp_url(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.post(
        "/api/send-message/",
        json={"message": "Dawai le aana", "llm_optional": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["whatsapp_url"] == "https://wa.me/919773687877?text=Dawai%20le%20aana"
    assert payload["llm"]["status"] == "skipped"


def test_mcp_token_status_detects_nested_mcp_remote_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_REMOTE_CONFIG_DIR", str(tmp_path / ".mcp-auth"))
    monkeypatch.setattr(mcp_exec, "_mcp_token_secret", lambda: {})

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
        lambda: {"files": {"mcp-remote-0.1.38/abc_tokens.json": '{"access_token":"x"}'}},
    )

    assert mcp_exec.restore_mcp_auth_tokens(tmp_path) is True

    restored = tmp_path / ".mcp-auth" / "mcp-remote-0.1.38" / "abc_tokens.json"
    assert restored.read_text(encoding="utf-8") == '{"access_token":"x"}'


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

    response = client.post("/api/food/query/", json={"message": "pizza near me"})

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
    )

    assert response.status_code == 200
    assert calls["tool_plan"] == plan["tool_plan"]
    assert response.json()["tool_execution"]["routed_providers"] == ["zomato"]
    assert response.json()["zomato_summary"]["error"] is None
