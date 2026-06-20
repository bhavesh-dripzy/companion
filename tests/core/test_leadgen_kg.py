"""Unit tests for the KG/leadgen two-tier additions (issue #64).

Covers the request-path logic that runs WITHOUT AWS or Neo4j: personal-fact
extraction + normalization, the Tier-1 writer adapter, the cache merge-write
(reserved-word aliasing + None-skipping), the planner user-context block, the
Persist-stage wiring, and the new schema models. The graph methods
(upsert_vendor / match_leads) need a live Neo4j and are exercised in deploy.
"""

import asyncio
from types import SimpleNamespace

import pytest

from companion_api.personal_fact_extractor import (
    extract_personal_facts,
    normalize_personal_facts,
)
from core.boundaries.null_impls import NoopPersonalFactWriter
from core.boundaries.personal_facts import LlmPersonalFactWriter
from core.contracts.profile import Profile
from core.contracts.turn import TurnRequest
from core.pipeline.context import PipelineContext, PipelineDeps
from core.pipeline.stages.plan import _user_context
from core.pipeline.stages.persist import Persist
from leadgen.schema import Lead, Vendor

pytestmark = pytest.mark.unit

_LLM = SimpleNamespace(provider="sarvam", model="sarvam-30b", api_key="k", base_url=None)


# ── personal-fact normalization (pure) ──────────────────────────────────────

def test_normalize_keeps_known_columns_and_drops_junk():
    raw = {
        "occupation": "Doctor",
        "city": "Gurugram",
        "favourite_colour": "blue",  # unknown column -> dropped
        "phone": "",                  # empty -> dropped
        "name": None,                 # None -> dropped
    }
    assert normalize_personal_facts(raw) == {"occupation": "Doctor", "city": "Gurugram"}


def test_normalize_joins_family_composition_list():
    out = normalize_personal_facts({"family_composition": ["spouse", " child ", ""]})
    assert out == {"family_composition": "spouse, child"}


def test_normalize_handles_non_dict():
    assert normalize_personal_facts(["not", "a", "dict"]) == {}


# ── extract_personal_facts (fake LLM) ───────────────────────────────────────

def test_extract_personal_facts_normalizes_llm_output():
    async def fake_complete(*, user_message, system_prompt, llm):
        assert "DURABLE PERSONAL FACTS" in system_prompt
        return {"occupation": "teacher", "city": "Pune", "unknown": "x"}

    out = asyncio.run(
        extract_personal_facts(
            user_message="I teach in Pune",
            assistant_message="Nice!",
            complete_json=fake_complete,
            llm=_LLM,
        )
    )
    assert out == {"occupation": "teacher", "city": "Pune"}


def test_extract_personal_facts_fail_open_on_llm_error():
    async def boom(*, user_message, system_prompt, llm):
        raise RuntimeError("llm down")

    out = asyncio.run(
        extract_personal_facts(
            user_message="hi", assistant_message="hello", complete_json=boom, llm=_LLM
        )
    )
    assert out == {}


def test_extract_personal_facts_skips_empty_message():
    called = False

    async def fake(*, user_message, system_prompt, llm):
        nonlocal called
        called = True
        return {}

    out = asyncio.run(
        extract_personal_facts(user_message="  ", assistant_message="x", complete_json=fake, llm=_LLM)
    )
    assert out == {} and called is False


# ── Tier-1 writer adapter ───────────────────────────────────────────────────

def test_personal_fact_writer_writes_when_facts_found(monkeypatch):
    written = {}
    monkeypatch.setattr(
        LlmPersonalFactWriter, "_write", staticmethod(lambda uid, facts: written.update({uid: facts}))
    )

    async def fake(*, user_message, system_prompt, llm):
        return {"occupation": "doctor"}

    asyncio.run(
        LlmPersonalFactWriter().capture(
            user_id="u1", user_message="I'm a doctor", assistant_message="ok",
            complete_json=fake, llm=_LLM,
        )
    )
    assert written == {"u1": {"occupation": "doctor"}}


def test_personal_fact_writer_no_write_when_empty(monkeypatch):
    written = []
    monkeypatch.setattr(
        LlmPersonalFactWriter, "_write", staticmethod(lambda uid, facts: written.append(facts))
    )

    async def fake(*, user_message, system_prompt, llm):
        return {}

    asyncio.run(
        LlmPersonalFactWriter().capture(
            user_id="u1", user_message="hello", assistant_message="hi", complete_json=fake, llm=_LLM
        )
    )
    assert written == []


def test_personal_fact_writer_fail_open(monkeypatch):
    async def fake(*, user_message, system_prompt, llm):
        return {"occupation": "doctor"}

    def boom(uid, facts):
        raise RuntimeError("dynamo down")

    monkeypatch.setattr(LlmPersonalFactWriter, "_write", staticmethod(boom))
    # must not raise
    asyncio.run(
        LlmPersonalFactWriter().capture(
            user_id="u1", user_message="x", assistant_message="y", complete_json=fake, llm=_LLM
        )
    )


# ── cache merge-write (reserved words + None skipping), no AWS ───────────────

class _FakeTable:
    def __init__(self):
        self.updates = []

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


def _repo_with_fake_table(monkeypatch):
    import companion_api.profile_cache as pc

    table = _FakeTable()
    monkeypatch.setattr(pc, "_resource", lambda: SimpleNamespace(Table=lambda name: table))
    return pc.ProfileCacheRepository(), table


def test_update_fields_aliases_reserved_words_and_skips_none(monkeypatch):
    repo, table = _repo_with_fake_table(monkeypatch)
    repo.update_fields("u1", {"name": "Rahul", "status": "matched", "city": None})

    assert len(table.updates) == 1
    call = table.updates[0]
    assert call["Key"] == {"PK": "USER#u1", "SK": "PROFILE"}
    # both attrs are DynamoDB reserved words -> must be aliased via #k placeholders
    assert set(call["ExpressionAttributeNames"].values()) == {"name", "status"}
    assert "city" not in call["ExpressionAttributeNames"].values()  # None skipped
    assert call["UpdateExpression"].startswith("SET ")


def test_update_fields_noop_when_all_none(monkeypatch):
    repo, table = _repo_with_fake_table(monkeypatch)
    repo.update_fields("u1", {"name": None})
    assert table.updates == []


def test_merge_personal_stamps_updated_at_personal(monkeypatch):
    repo, table = _repo_with_fake_table(monkeypatch)
    repo.merge_personal("u1", {"occupation": "doctor"})
    names = table.updates[0]["ExpressionAttributeNames"].values()
    assert "occupation" in names and "updated_at_personal" in names


# ── planner user-context block ──────────────────────────────────────────────

def test_user_context_renders_identity_and_leads():
    profile = Profile(
        user_id="u1",
        name="Rahul",
        occupation="Doctor",
        city="Gurugram",
        open_leads=[{"category": "electronics", "product": "1.5 ton split AC"}],
        matched_leads=[{"category": "furniture", "product": "sofa", "vendor_name": "Woodcraft"}],
    )
    block = _user_context(SimpleNamespace(profile=profile))
    assert "WHAT YOU KNOW ABOUT THIS USER" in block  # shared render_user_memory seam
    assert "Rahul" in block and "Doctor" in block
    assert "split AC" in block and "don't re-pitch" in block
    assert "Woodcraft" in block


def test_user_context_empty_when_no_profile():
    assert _user_context(SimpleNamespace(profile=None)) == ""


def test_user_context_empty_when_nothing_known():
    assert _user_context(SimpleNamespace(profile=Profile(user_id="u1"))) == ""


# ── Persist stage wiring ────────────────────────────────────────────────────

def test_persist_invokes_personal_fact_writer():
    writer = NoopPersonalFactWriter()

    async def fake(*, user_message, system_prompt, llm):
        return {}

    deps = PipelineDeps(
        registry=None, profile_service=None, publisher=None,
        complete_json=fake, personal_fact_writer=writer,
    )
    ctx = PipelineContext(
        request=TurnRequest(message="I'm a doctor", user_id="u1"), llm=_LLM, deps=deps
    )
    asyncio.run(Persist._capture_personal_facts(ctx))
    assert writer.captured and writer.captured[0]["user_id"] == "u1"


def test_persist_capture_noop_when_writer_absent():
    deps = PipelineDeps(
        registry=None, profile_service=None, publisher=None,
        complete_json=None, personal_fact_writer=None,
    )
    ctx = PipelineContext(request=TurnRequest(message="hi", user_id="u1"), llm=_LLM, deps=deps)
    # must not raise even though writer is None
    asyncio.run(Persist._capture_personal_facts(ctx))


# ── schema models ───────────────────────────────────────────────────────────

def test_vendor_defaults():
    v = Vendor(id="v1", name="Cool Air", category="electronics")
    assert v.status == "active" and v.service_area == [] and v.metadata == {}


def test_lead_lifecycle_fields_default_none():
    lead = Lead(category="electronics", product="AC")
    assert lead.status is None and lead.vendor_id is None and lead.matched_at is None
