from fastapi.testclient import TestClient

from companion_api.main import app


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
