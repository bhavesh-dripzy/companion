"""serp web_search: lean extraction from a real-shaped google payload, plus the
citation card the frontend renders. Mirrors the other serp engine golden tests."""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.plugins.serp.provider import SerpProvider
from core.providers.plugins.serp.web_search import extract_web_search, web_search_params

pytestmark = pytest.mark.component

_PAYLOAD = json.loads((Path(__file__).parent / "fixtures/serp/web_search.json").read_text())


def test_params_target_google_india():
    assert web_search_params({"query": "tallest mountain"}) == {
        "engine": "google",
        "q": "tallest mountain",
        "gl": "in",
        "hl": "en",
        "num": 10,
    }


def test_extract_pulls_answer_knowledge_and_cited_results():
    out = extract_web_search(_PAYLOAD)
    assert out["answer"] == "Narendra Modi"
    assert out["knowledge"]["title"] == "Narendra Modi"
    assert out["knowledge"]["description"]
    # lean: bounded, every result carries a citation link + a flattened source name
    assert 0 < len(out["results"]) <= 6
    first = out["results"][0]
    assert first["link"].startswith("http")
    assert first["source"] == "Wikipedia"  # nested {"name": ...} flattened


def test_build_attachments_emits_web_search_card():
    data = extract_web_search(_PAYLOAD)
    result = ProviderResult(
        provider="serp",
        ok=True,
        results=[
            ToolResult(
                tool="web_search",
                provider="serp",
                ok=True,
                arguments={"query": "who is the prime minister of india"},
                data={"is_error": False, "data": data},
            )
        ],
    )
    cards = SerpProvider().build_attachments(result)
    web = [c for c in cards if c.kind == "web_search_cards"]
    assert web, [c.kind for c in cards]
    wire = web[0].to_wire()
    assert wire["search_results"] and wire["search_results"][0]["link"].startswith("http")
    assert wire.get("keyword") == "who is the prime minister of india"
