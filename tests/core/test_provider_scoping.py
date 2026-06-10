"""Provider-scoped session state — regression tests for the cross-platform
comparison bugs (admin_bhavesh falooda session, 2026-06-10).

One root cause, four expressions: a Swiggy ad's res_id substituted into Zomato
tools ("restaurant ID valid nahi mil raha"), one platform's search evicting the
other's restaurants from the shared LRU, sparse re-fetches nulling out known
prices, and the narrator attributing one platform's menu to the other. These
tests pin the scoped layers that fix all four generically.
"""

import pytest

from core.contracts.attachment import Attachment
from core.providers.mcp.address import merge_saved_addresses
from core.providers.mcp.food_session import lru_merge as restaurants_merge
from core.providers.mcp.lru import backfill_fields, scoped_lru_merge
from core.providers.mcp.placeholders import fold_learned, provider_view
from core.providers.mcp.planner_context import placeholders_block, rows_for_provider
from core.providers.plugins.swiggy_food.placeholders import SwiggyFoodResolver

pytestmark = pytest.mark.unit


def test_provider_view_isolates_scoped_keys():
    values = {
        "zomato:__RESTAURANT_ID__": "20540806",
        "swiggy_food:__RESTAURANT_ID__": "662426",
        "__CART_ID__": "bare-legacy",
    }
    zomato = provider_view(values, "zomato")
    assert zomato["__RESTAURANT_ID__"] == "20540806"
    assert zomato["__CART_ID__"] == "bare-legacy"
    assert provider_view(values, "swiggy_food")["__RESTAURANT_ID__"] == "662426"
    assert "__RESTAURANT_ID__" not in provider_view(values, "zepto")


def test_provider_view_scoped_wins_over_bare():
    values = {"__RESTAURANT_ID__": "999580", "zomato:__RESTAURANT_ID__": "20540806"}
    assert provider_view(values, "zomato")["__RESTAURANT_ID__"] == "20540806"


def test_fold_learned_scopes_and_retires_bare_key():
    # The bhavesh bug: Swiggy learned an ad's res_id into the global slot and the
    # next Zomato menu fetch substituted it. Folding must scope and retire bare.
    values = {"__RESTAURANT_ID__": "999580"}
    fold_learned(values, "swiggy_food", {"__RESTAURANT_ID__": "662426"})
    assert values == {"swiggy_food:__RESTAURANT_ID__": "662426"}
    fold_learned(values, "zomato", {"__RESTAURANT_ID__": "20540806"})
    assert provider_view(values, "zomato")["__RESTAURANT_ID__"] == "20540806"
    assert provider_view(values, "swiggy_food")["__RESTAURANT_ID__"] == "662426"


def test_scoped_lru_merge_never_evicts_other_provider():
    prior = [{"res_id": "z1", "name": "Gianis", "provider": "zomato"}]
    fresh = [{"res_id": f"s{i}", "name": f"Shop {i}"} for i in range(8)]
    merged = scoped_lru_merge(
        prior, fresh, provider="swiggy_food", key=lambda e: e["res_id"], cap=8
    )
    assert {"res_id": "z1", "name": "Gianis", "provider": "zomato"} in merged
    assert sum(1 for e in merged if e["provider"] == "swiggy_food") == 8


def test_scoped_lru_merge_caps_and_dedups_within_provider():
    prior = [{"res_id": "s0", "name": "Old", "provider": "swiggy_food"}]
    fresh = [{"res_id": f"s{i}", "name": f"Shop {i}"} for i in range(10)]
    merged = scoped_lru_merge(
        prior, fresh, provider="swiggy_food", key=lambda e: e["res_id"], cap=8
    )
    assert len(merged) == 8
    assert merged[0]["name"] == "Shop 0", "fresh row supersedes the prior duplicate"


def test_scoped_lru_merge_adopts_untagged_prior_rows():
    prior = [{"res_id": "legacy", "name": "Pre-scoping row"}]
    merged = scoped_lru_merge(prior, [], provider="zomato", key=lambda e: e["res_id"], cap=8)
    assert merged == [{"res_id": "legacy", "name": "Pre-scoping row", "provider": "zomato"}]


def test_scoped_lru_merge_backfills_fields_from_duplicate():
    # Royal Faluda regression: a category listing without prices re-fetched the
    # item and nulled out the ₹138 already learned — comparisons went blind.
    prior = [{"variant_id": "v1", "item_name": "Royal Faluda", "price": 138, "provider": "zomato"}]
    fresh = [{"variant_id": "v1", "item_name": "Royal Faluda", "price": None}]
    merged = scoped_lru_merge(
        prior,
        fresh,
        provider="zomato",
        key=lambda e: e["variant_id"],
        cap=35,
        combine=backfill_fields("price"),
    )
    assert merged[0]["price"] == 138


def test_restaurant_lru_two_platform_round_trip():
    session = restaurants_merge(None, [{"res_id": "20540806", "name": "Gianis"}], provider="zomato")
    session = restaurants_merge(
        session,
        [{"res_id": str(i), "name": f"Swiggy shop {i}"} for i in range(8)],
        provider="swiggy_food",
    )
    assert rows_for_provider(session, "zomato") == [
        {"res_id": "20540806", "name": "Gianis", "provider": "zomato"}
    ]
    assert len(rows_for_provider(session, "swiggy_food")) == 8


def test_rows_for_provider_hides_foreign_keeps_untagged():
    rows = [
        {"res_id": "1", "provider": "zomato"},
        {"res_id": "2", "provider": "swiggy_food"},
        {"res_id": "3"},
    ]
    assert [r["res_id"] for r in rows_for_provider(rows, "zomato")] == ["1", "3"]


def test_placeholders_block_renders_own_slice_only():
    values = {"swiggy_food:__RESTAURANT_ID__": "x", "zomato:__CART_ID__": "y", "__ORDER_ID__": "z"}
    block = placeholders_block(values, "zomato")
    assert "__CART_ID__" in block and "__ORDER_ID__" in block
    assert "__RESTAURANT_ID__" not in block
    assert "zomato:" not in block, "planner sees bare keys it can emit verbatim"


def test_swiggy_resolver_learns_first_organic_hit_not_ad():
    # The falooda search led with "Thalaiva Biryani (Ad)" (999580); learning it
    # pointed every follow-up menu fetch at a biryani place.
    payload = {
        "data": {
            "restaurants": [
                {"id": "999580", "name": "Thalaiva Biryani (Ad)", "rating": 4.3},
                {"id": "842257", "name": "Cool Scoop (Ad)", "promoted": True, "rating": 4.2},
                {"id": "662426", "name": "Gianis - Ice Cream, Shakes & Sundaes", "rating": 4.5},
            ]
        }
    }
    values: dict[str, str] = {}
    SwiggyFoodResolver().learn(values, payload)
    assert values["__RESTAURANT_ID__"] == "662426"


def test_swiggy_resolver_falls_back_to_ad_when_page_is_all_ads():
    payload = {
        "data": {"restaurants": [{"id": "999580", "name": "Thalaiva Biryani (Ad)", "rating": 4.3}]}
    }
    values: dict[str, str] = {}
    SwiggyFoodResolver().learn(values, payload)
    assert values["__RESTAURANT_ID__"] == "999580"


def test_merge_saved_addresses_keeps_other_platform_rows():
    from types import SimpleNamespace

    tr = SimpleNamespace(
        tool="get_addresses",
        ok=True,
        data={"data": {"addresses": [{"id": "swg-addr-1", "addressTag": "Home"}]}},
    )
    merged = {
        "session_addresses": [{"address_id": "zom-addr-1", "label": "Home", "provider": "zomato"}]
    }
    merge_saved_addresses(merged, [tr], provider="swiggy_food")
    rows = merged["session_addresses"]
    assert rows[0]["address_id"] == "swg-addr-1" and rows[0]["provider"] == "swiggy_food"
    assert {"address_id": "zom-addr-1", "label": "Home", "provider": "zomato"} in rows


def test_narrate_card_lines_name_platform_and_entries():
    from types import SimpleNamespace

    from core.pipeline.stages.narrate import _results_summary

    ctx = SimpleNamespace(
        provider_results=[],
        blocked=[],
        attachments=[
            Attachment(
                kind="restaurant_cards",
                source="swiggy_food.search_restaurants",
                provider="swiggy",
                restaurants=[{"res_id": "662426", "name": "Gianis"}],
            ),
            Attachment(
                kind="menu_cards",
                source="zomato.get_restaurant_menu_by_categories",
                provider="zomato",
                items=[{"item_name": "Royal Faluda", "price": 138}],
            ),
        ],
    )
    summary = _results_summary(ctx)
    assert "[swiggy_food] restaurant_cards (1 entries): Gianis" in summary
    assert "[zomato] menu_cards (1 entries): Royal Faluda" in summary


def test_pipeline_fanout_keeps_placeholders_provider_scoped():
    """End-to-end bhavesh regression: a two-provider comparison turn, then a
    follow-up — each provider's executor must see only its own learned ids."""
    import asyncio

    from core.app_factory import build_runner
    from core.boundaries.null_impls import NoopPublisher
    from core.contracts.session import SessionState
    from core.contracts.tool import AuthMode
    from core.contracts.tool_result import ToolResult
    from core.contracts.turn import TurnRequest
    from core.llm.config import ResolvedLlm
    from core.providers.runner import run_calls
    from core.registry import ProviderRegistry, ToolRegistry
    from core.registry.seed import builtin_tools

    seen: dict[str, list[dict]] = {"zomato": [], "swiggy_food": []}

    def _fake(pid: str, learned_id: str):
        class _P:
            id = pid
            label = pid
            auth_mode = AuthMode.OAUTH
            session_cls = None

            async def discover_tools(self):
                return []

            async def run(self, calls, ctx):
                seen[pid].append(dict(ctx.placeholders))

                async def execute_one(call):
                    return ToolResult(
                        tool=call.tool,
                        provider=pid,
                        data={"ran": call.tool},
                        learned_placeholders={"__RESTAURANT_ID__": learned_id},
                    )

                return await run_calls(execute_one, calls=calls, provider_id=pid, ctx=ctx)

            def build_attachments(self, result):
                return []

            def merge(self, slice_, result):
                return slice_

        return _P()

    async def complete(*, user_message, system_prompt, llm):
        if "machine-ready tool plan" in system_prompt:
            return {
                "intent": "compare",
                "summary": "x",
                "tool_plan": [{"tool": "search_restaurants", "arguments": {}}],
            }
        if "You are Companion" in system_prompt:
            return {"message": "ok"}
        return {
            "task_type": "food_delivery",
            "food_providers": ["zomato", "swiggy_food"],
            "reasoning": "compare",
        }

    tools = ToolRegistry()
    tools.register_all(builtin_tools())
    registry = ProviderRegistry(tools=tools)
    registry.register(_fake("zomato", "zom-1"))
    registry.register(_fake("swiggy_food", "swg-1"))

    class _Profile:
        async def get(self, user_id):
            return None

    runner = build_runner(
        registry=registry,
        profile_service=_Profile(),
        publisher=NoopPublisher(),
        complete_json=complete,
    )
    llm = ResolvedLlm(provider="deepseek", api_key="x", model="m")

    first = asyncio.run(runner.run(TurnRequest(message="compare falooda", user_id="u1"), llm))
    assert first.session.placeholders == {
        "zomato:__RESTAURANT_ID__": "zom-1",
        "swiggy_food:__RESTAURANT_ID__": "swg-1",
    }

    follow_up = TurnRequest(
        message="now the menus",
        user_id="u1",
        session=SessionState(placeholders=dict(first.session.placeholders)),
    )
    asyncio.run(runner.run(follow_up, llm))
    assert seen["zomato"][-1]["__RESTAURANT_ID__"] == "zom-1"
    assert seen["swiggy_food"][-1]["__RESTAURANT_ID__"] == "swg-1"
