"""Write-effect attestation tags mutating results; reads stay unclaimed."""

from core.contracts.tool import Tool
from core.contracts.tool_result import Effect, ProviderResult, ToolResult
from core.middleware.attestation import attest
from core.registry.tool_registry import ToolRegistry


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_all(
        [
            Tool(name="update_cart", provider="swiggy_instamart", mutating=True),
            Tool(name="search_products", provider="swiggy_instamart", mutating=False),
        ]
    )
    return reg


def _one(tool: str, ok: bool, provider: str = "swiggy_instamart") -> list[ProviderResult]:
    return [
        ProviderResult(
            provider=provider,
            ok=ok,
            results=[ToolResult(tool=tool, provider=provider, ok=ok)],
        )
    ]


def test_mutating_ok_is_applied():
    results = _one("update_cart", ok=True)
    attest(results, _registry())
    assert results[0].results[0].effect is Effect.APPLIED


def test_mutating_failure_is_not_applied():
    results = _one("update_cart", ok=False)
    attest(results, _registry())
    assert results[0].results[0].effect is Effect.NOT_APPLIED


def test_read_carries_no_claim():
    results = _one("search_products", ok=True)
    attest(results, _registry())
    assert results[0].results[0].effect is None


def test_unknown_tool_is_skipped():
    results = _one("who_knows", ok=True, provider="mystery")
    attest(results, _registry())
    assert results[0].results[0].effect is None
