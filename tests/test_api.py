from fastapi.testclient import TestClient

from companion_api.main import app
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
