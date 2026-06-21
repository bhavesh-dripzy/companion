"""The claim guard overrides a narrator reply that asserts an unapplied mutation."""

from core.contracts.tool_result import Effect, ProviderResult, ToolResult
from core.middleware.claim_guard import honest_message, unsupported_mutation_claim

_CONFAB = "I've added 2 of the 1 litre Amul Taaza toned milk to your cart. The total is ₹154."


def _pr(effect=None, ok=True):
    return [
        ProviderResult(
            provider="swiggy_instamart",
            ok=ok,
            results=[ToolResult(tool="update_cart", provider="swiggy_instamart", ok=ok, effect=effect)],
        )
    ]


def test_confab_with_no_tools_is_unsupported():
    # The headline B1 case: 0 tools ran, narrator fabricated a successful add.
    assert unsupported_mutation_claim(_CONFAB, []) is True


def test_confab_with_applied_mutation_is_ok():
    assert unsupported_mutation_claim(_CONFAB, _pr(Effect.APPLIED)) is False


def test_confab_with_failed_mutation_is_unsupported():
    assert unsupported_mutation_claim(_CONFAB, _pr(Effect.NOT_APPLIED, ok=False)) is True


def test_non_claim_message_passes():
    assert unsupported_mutation_claim("Here are the milk options — which would you like?", []) is False


def test_can_add_is_not_a_completion_claim():
    assert unsupported_mutation_claim("I can add whichever one you pick — just say the word.", []) is False


def test_honest_message_prefers_cards():
    assert "options above" in honest_message([], has_cards=True)


def test_honest_message_failed_tool():
    assert "error" in honest_message(_pr(Effect.NOT_APPLIED, ok=False), has_cards=False)
