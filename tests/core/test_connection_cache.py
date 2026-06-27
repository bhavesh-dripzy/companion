"""Unit tests for the MCP connection-status cache (version-stamp + local fallback).

No AWS: version-stamp mode is exercised with a tiny in-memory fake DynamoDB Table; fallback
mode (no table) is exercised directly. Each test resets the module's process-global state.
"""

from __future__ import annotations

import importlib

import pytest

import core.providers.mcp.connection_cache as cc

URL_A = "https://mcp.swiggy.com/im"
URL_Z = "https://mcp.zepto.co.in/mcp"
USER = "user-1"


class _FakeTable:
    """Minimal stand-in for boto3's DynamoDB Table: GetItem + atomic `ADD v :one`."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.gets = 0

    def get_item(self, Key, ProjectionExpression=None):  # boto3 kwarg names (CapCase)
        self.gets += 1
        item = self.items.get(Key["user_id"])
        return {"Item": item} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ReturnValues):
        item = self.items.setdefault(Key["user_id"], {"user_id": Key["user_id"], "v": 0})
        item["v"] = int(item.get("v", 0)) + int(ExpressionAttributeValues[":one"])
        return {"Attributes": {"v": item["v"]}}


@pytest.fixture
def fresh():
    """Reload the module so each test starts with clean process-global caches."""
    importlib.reload(cc)
    return cc


def _use_table(mod, table) -> None:
    """Force the module onto a fake shared backend with no version-read TTL (strict)."""
    mod._table_handle = table
    mod._TABLE_NAME = "fake-table"
    mod._VERSION_TTL_S = 0.0


# --------------------------- version-stamp (shared) mode ---------------------------


def test_miss_then_hit(fresh):
    table = _FakeTable()
    _use_table(fresh, table)
    assert fresh.get(USER, URL_A) is None  # cold miss
    fresh.set(USER, URL_A, True)
    assert fresh.get(USER, URL_A) is True  # served from cache


def test_invalidate_busts_the_entry(fresh):
    table = _FakeTable()
    _use_table(fresh, table)
    fresh.set(USER, URL_A, True)
    assert fresh.get(USER, URL_A) is True
    fresh.invalidate(USER)  # bump version
    assert fresh.get(USER, URL_A) is None  # stale → recompute


def test_stale_after_external_version_bump(fresh):
    """An entry is rejected once the SHARED version moves under it — i.e. another container
    (or endpoint) did a connect/disconnect. This is the cross-container convergence guarantee."""
    table = _FakeTable()
    _use_table(fresh, table)
    fresh.set(USER, URL_Z, True)  # cached under the current version
    assert fresh.get(USER, URL_Z) is True
    # Another container bumps the shared version directly (our local result cache is untouched).
    table.update_item(
        Key={"user_id": USER},
        UpdateExpression="ADD v :one",
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    assert fresh.get(USER, URL_Z) is None  # version moved → our entry is stale


def test_providers_are_independent_keys(fresh):
    table = _FakeTable()
    _use_table(fresh, table)
    fresh.set(USER, URL_A, True)
    fresh.set(USER, URL_Z, False)
    assert fresh.get(USER, URL_A) is True
    assert fresh.get(USER, URL_Z) is False


# ------------------------------- local fallback mode -------------------------------


def test_fallback_without_table(fresh):
    fresh._table_handle = None
    fresh._TABLE_NAME = ""  # no shared backend
    fresh.set(USER, URL_A, True)
    assert fresh.get(USER, URL_A) is True  # served from the safety-TTL local cache


def test_fallback_invalidate_clears_local(fresh):
    fresh._table_handle = None
    fresh._TABLE_NAME = ""
    fresh.set(USER, URL_A, True)
    fresh.invalidate(USER)  # single-process connect/disconnect still clears locally
    assert fresh.get(USER, URL_A) is None


def test_fallback_safety_ttl_expires(fresh):
    fresh._table_handle = None
    fresh._TABLE_NAME = ""
    fresh._FALLBACK_TTL_S = 0.0  # immediately stale
    fresh.set(USER, URL_A, True)
    assert fresh.get(USER, URL_A) is None  # not trusted past the safety TTL
