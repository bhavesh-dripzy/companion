"""Hosted OAuth start negotiation (redirect vs paste) + paste-back completion."""

import json
from urllib.parse import parse_qs, quote, urlsplit

import pytest

import core.providers.mcp.oauth as oauth
import core.providers.mcp.oauth_complete as oauth_complete

pytestmark = pytest.mark.unit

CALLBACK = "https://houzzat.in/api/mcp/oauth/callback"
MCP_URL = "https://mcp.example.com/mcp"
METADATA = {
    "authorization_server_url": "https://auth.example",
    "protected_resource": {},
    "authorization_server": {
        "authorization_endpoint": "https://auth.example/authorize",
        "registration_endpoint": "https://auth.example/register",
        "token_endpoint": "https://auth.example/token",
        "scopes_supported": ["openid"],
    },
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload


@pytest.fixture
def vault(monkeypatch):
    """In-memory stand-in for the Secrets Manager token vault."""
    store = {}

    def merge(user_id, **sections):
        store.setdefault(user_id, {}).update(sections)

    for mod in (oauth, oauth_complete):
        monkeypatch.setattr(
            mod, "mcp_token_secret", lambda user_id=None: store.get(user_id, {}), raising=True
        )
        monkeypatch.setattr(mod, "merge_mcp_token_secret", merge, raising=True)
    monkeypatch.setattr(oauth, "_discover_oauth_metadata", lambda url: METADATA)
    monkeypatch.setattr(oauth, "remote_version", lambda root: "0.1.38")
    return store


def _post_script(monkeypatch, responses):
    """Make oauth.requests.post pop scripted responses, recording each call."""
    calls = []

    def fake_post(url, json=None, **kwargs):
        calls.append({"url": url, "json": json})
        return responses.pop(0)

    monkeypatch.setattr(oauth.requests, "post", fake_post)
    return calls


def _echo_client(redirect_uri):
    return FakeResponse(200, {"client_id": "cid-1", "redirect_uris": [redirect_uri]})


def test_hosted_callback_accepted_uses_redirect_completion(monkeypatch, vault):
    calls = _post_script(monkeypatch, [_echo_client(CALLBACK)])
    result = oauth.start_web_oauth(
        mcp_url=MCP_URL, callback_url=CALLBACK, user_id="alice", provider_id="zomato"
    )
    assert result["completion"] == "redirect"
    assert quote(CALLBACK, safe="") in result["authorization_url"]
    assert len(calls) == 1
    pending = next(iter(vault["alice"]["oauth_pending"].values()))
    assert pending["callback_url"] == CALLBACK
    assert pending["completion"] == "redirect"
    assert pending["provider_id"] == "zomato"


def test_allowlist_rejection_falls_back_to_paste(monkeypatch, vault):
    calls = _post_script(
        monkeypatch,
        [FakeResponse(403, {"error": "Invalid redirect_uris"}), _echo_client(oauth.PASTE_REDIRECT_URI)],
    )
    result = oauth.start_web_oauth(mcp_url=MCP_URL, callback_url=CALLBACK, user_id="alice")
    assert result["completion"] == "paste"
    assert quote(oauth.PASTE_REDIRECT_URI, safe="") in result["authorization_url"]
    assert len(calls) == 2
    pending = next(iter(vault["alice"]["oauth_pending"].values()))
    assert pending["callback_url"] == oauth.PASTE_REDIRECT_URI


def test_fixed_client_pivots_to_its_own_localhost_uri(monkeypatch, vault):
    fixed = FakeResponse(
        200,
        {
            "client_id": "shared",
            "redirect_uris": [
                "http://localhost:52155/oauth/callback",
                "https://claude.ai/api/mcp/auth_callback",
            ],
        },
    )
    calls = _post_script(monkeypatch, [fixed])
    result = oauth.start_web_oauth(mcp_url=MCP_URL, callback_url=CALLBACK, user_id="alice")
    assert result["completion"] == "paste"
    assert quote("http://localhost:52155/oauth/callback", safe="") in result["authorization_url"]
    assert len(calls) == 1


def test_force_paste_skips_hosted_registration(monkeypatch, vault):
    calls = _post_script(monkeypatch, [_echo_client(oauth.PASTE_REDIRECT_URI)])
    result = oauth.start_web_oauth(
        mcp_url=MCP_URL, callback_url=CALLBACK, user_id="alice", force_paste=True
    )
    assert result["completion"] == "paste"
    assert len(calls) == 1
    assert calls[0]["json"]["redirect_uris"] == [oauth.PASTE_REDIRECT_URI]


def test_extract_state_and_code_variants():
    url = "http://localhost:3334/oauth/callback?code=abc123&state=st-9"
    assert oauth_complete.extract_state_and_code(url) == ("st-9", "abc123")
    assert oauth_complete.extract_state_and_code(f'  "{url}" ') == ("st-9", "abc123")
    assert oauth_complete.extract_state_and_code("state=s&code=c") == ("s", "c")
    with pytest.raises(RuntimeError):
        oauth_complete.extract_state_and_code("http://localhost:3334/oauth/callback?error=denied")
    with pytest.raises(RuntimeError):
        oauth_complete.extract_state_and_code("just some text")


def _seed_pending(vault, monkeypatch, user_id="alice", state="st-1"):
    pending = {
        "state": state,
        "created_at": 2**33,
        "mcp_url": MCP_URL,
        "callback_url": oauth.PASTE_REDIRECT_URI,
        "code_verifier": "verif",
        "client_info": {"client_id": "cid-1"},
        "metadata": METADATA,
        "user_id": user_id,
        "provider_label": "Zepto MCP",
        "provider_id": "zepto",
        "completion": "paste",
    }
    vault[user_id] = {"oauth_pending": {state: pending}}
    monkeypatch.setattr(oauth_complete, "_root_secret", lambda: {"users": {user_id: {}}})
    monkeypatch.setattr(oauth_complete.time, "time", lambda: 2**33 + 5)
    return pending


def test_paste_completion_rejects_other_user(monkeypatch, vault):
    _seed_pending(vault, monkeypatch)
    with pytest.raises(RuntimeError, match="different Companion user"):
        oauth_complete.complete_web_oauth_from_text(
            text="http://localhost:3334/oauth/callback?code=c&state=st-1", user_id="bob"
        )


def test_paste_completion_exchanges_with_registered_redirect(monkeypatch, vault, tmp_path):
    _seed_pending(vault, monkeypatch)
    monkeypatch.setenv("MCP_REMOTE_CONFIG_DIR", str(tmp_path))
    exchanges = []

    def fake_post(url, data=None, **kwargs):
        exchanges.append({"url": url, "data": data})
        return FakeResponse(200, {"access_token": "at-1", "refresh_token": "rt-1"})

    monkeypatch.setattr(oauth_complete.requests, "post", fake_post)
    result = oauth_complete.complete_web_oauth_from_text(
        text="http://localhost:3334/oauth/callback?code=c0de&state=st-1", user_id="alice"
    )
    assert result["status"] == "connected_pending_test"
    assert result["provider_id"] == "zepto"
    assert exchanges[0]["url"] == "https://auth.example/token"
    assert exchanges[0]["data"]["redirect_uri"] == oauth.PASTE_REDIRECT_URI
    assert exchanges[0]["data"]["code"] == "c0de"
    files = vault["alice"]["files"]
    assert any(key.endswith("_tokens.json") for key in files)
    assert vault["alice"]["oauth_pending"] == {}


def test_authorization_url_carries_pkce_and_state(monkeypatch, vault):
    _post_script(monkeypatch, [_echo_client(CALLBACK)])
    result = oauth.start_web_oauth(mcp_url=MCP_URL, callback_url=CALLBACK, user_id="alice")
    params = parse_qs(urlsplit(result["authorization_url"]).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["response_type"] == ["code"]
    state = params["state"][0]
    assert state in vault["alice"]["oauth_pending"]
