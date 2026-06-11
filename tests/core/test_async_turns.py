"""Tracer listener seam, progress narration, and async-turn plumbing."""

import json

import pytest

import companion_api.agent_jobs as agent_jobs
import companion_api.turn_persistence as turn_persistence
from companion_api.turn_progress import ProgressNarrator, provider_label
from core.middleware.tracing import Tracer

pytestmark = pytest.mark.unit


# ── tracer listener ───────────────────────────────────────────────────────


def test_tracer_notifies_listener_and_keeps_trace_shape():
    seen = []
    t = Tracer(listener=seen.append)
    t.event("route", providers=["zomato"])
    t.progress("stage", name="execute")
    assert t.to_list() == [{"event": "route", "providers": ["zomato"]}]
    assert seen == [
        {"event": "route", "providers": ["zomato"]},
        {"event": "stage", "progress": True, "name": "execute"},
    ]


def test_tracer_listener_errors_are_swallowed():
    def boom(_):
        raise RuntimeError("listener exploded")

    t = Tracer(listener=boom)
    t.event("route", providers=[])
    assert t.to_list() == [{"event": "route", "providers": []}]


# ── progress narration ────────────────────────────────────────────────────


def _narrate(events):
    lines = []
    narrator = ProgressNarrator(lines.append)
    for e in events:
        narrator(e)
    return lines


def test_narrator_tool_turn_storyline():
    lines = _narrate(
        [
            {"event": "stage", "progress": True, "name": "personalize"},
            {"event": "stage", "progress": True, "name": "route"},
            {"event": "route", "task": "food_order", "providers": ["zomato", "swiggy_food"]},
            {"event": "stage", "progress": True, "name": "plan"},
            {"event": "stage", "progress": True, "name": "execute"},
            {"event": "stage", "progress": True, "name": "collect"},
            {"event": "collect", "attachments": 7},
            {"event": "stage", "progress": True, "name": "narrate"},
        ]
    )
    assert lines == [
        "Figuring out the best way to help…",
        "Checking Zomato and Swiggy Food…",
        "Searching Zomato and Swiggy Food — this can take a few seconds…",
        "Found 7 results — comparing them…",
        "Putting your answer together…",
    ]


def test_narrator_chat_turn_stays_quiet_about_tools():
    lines = _narrate(
        [
            {"event": "stage", "progress": True, "name": "route"},
            {"event": "route", "task": "normal_query", "providers": []},
            {"event": "stage", "progress": True, "name": "plan"},
            {"event": "stage", "progress": True, "name": "execute"},
            {"event": "stage", "progress": True, "name": "narrate"},
        ]
    )
    assert lines == ["Figuring out the best way to help…", "Putting your answer together…"]


def test_provider_label():
    assert provider_label("swiggy_food") == "Swiggy Food"
    assert provider_label("zomato") == "Zomato"


# ── async job flow (in-memory table) ──────────────────────────────────────


class FakeTable:
    def __init__(self):
        self.items: dict[str, dict] = {}

    def put_item(self, Item):
        self.items[Item["job_id"]] = dict(Item)

    def get_item(self, Key, **_):
        item = self.items.get(Key["job_id"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kwargs):
        item = self.items[Key["job_id"]]
        if "list_append" in UpdateExpression:
            item["progress"] = list(item.get("progress") or []) + list(
                ExpressionAttributeValues[":p"]
            )
        if ":s" in ExpressionAttributeValues:
            item["status"] = ExpressionAttributeValues[":s"]
        if ":r" in ExpressionAttributeValues:
            item["result_json"] = ExpressionAttributeValues[":r"]
        if ":e" in ExpressionAttributeValues:
            item["error"] = ExpressionAttributeValues[":e"]


@pytest.fixture
def fake_table(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(agent_jobs, "_table", lambda: table)
    monkeypatch.setenv("AGENT_JOBS_TABLE", "fake")
    return table


def test_job_lifecycle_and_owner_scoping(fake_table):
    job_id = agent_jobs.create_job("alice")
    agent_jobs.update_progress(job_id, "Searching Zomato…")
    agent_jobs.complete_job(job_id, {"status": "ok", "assistant_message": "done", "x": 1.5})

    view = agent_jobs.get_job(job_id, "alice")
    assert view["status"] == "done"
    assert view["progress"] == ["Thinking…", "Searching Zomato…"]
    assert view["result"]["assistant_message"] == "done"
    assert view["result"]["x"] == 1.5
    assert agent_jobs.get_job(job_id, "mallory") is None


def test_worker_event_runs_turn_and_finalizes(fake_table, monkeypatch):
    def fake_run(data, user, *, reply_language=None, trace_listener=None):
        trace_listener({"event": "stage", "progress": True, "name": "route"})
        return {"status": "ok", "assistant_message": "namaste", "task_type": "normal_query"}

    monkeypatch.setattr(
        "companion_api.core_shadow.run_core_turn_sync", fake_run, raising=True
    )
    monkeypatch.setattr(
        agent_jobs,
        "persist_turn",
        lambda **kwargs: "chat-123",
    )
    job_id = agent_jobs.create_job("alice")
    agent_jobs.run_async_turn_event(
        {
            "companion_async": "agent_turn",
            "job_id": job_id,
            "user": "alice",
            "payload": {"message": "hello", "chat_id": None},
        }
    )
    item = fake_table.items[job_id]
    assert item["status"] == "done"
    envelope = json.loads(item["result_json"])
    assert envelope["assistant_message"] == "namaste"
    assert envelope["chat_id"] == "chat-123"
    assert "Figuring out the best way to help…" in item["progress"]


def test_worker_failure_marks_job_error(fake_table, monkeypatch):
    def fake_run(*a, **k):
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr("companion_api.core_shadow.run_core_turn_sync", fake_run, raising=True)
    job_id = agent_jobs.create_job("alice")
    agent_jobs.run_async_turn_event(
        {"job_id": job_id, "user": "alice", "payload": {"message": "hi"}}
    )
    item = fake_table.items[job_id]
    assert item["status"] == "error"
    assert "pipeline blew up" in item["error"]


# ── turn persistence (chat lazy-create + auto-title) ──────────────────────


def test_persist_turn_lazy_creates_titled_chat(monkeypatch):
    chats = {}
    messages = []

    class FakeChatRepo:
        def get(self, member_id, chat_id):
            return chats.get(chat_id)

        def create(self, *, member_id, chat_id, title=None):
            chats[chat_id] = {"member_id": member_id, "chat_id": chat_id, "title": title}
            return chats[chat_id]

        def touch(self, **kwargs):
            chats[kwargs["chat_id"]].update(
                {k: v for k, v in kwargs.items() if v is not None}
            )

    class FakeMsgRepo:
        def create(self, **kwargs):
            messages.append(kwargs)

    monkeypatch.setattr(turn_persistence, "ChatRepository", FakeChatRepo)
    monkeypatch.setattr(turn_persistence, "MessageRepository", FakeMsgRepo)
    monkeypatch.setattr(turn_persistence, "send_turn_event", lambda **k: None)

    chat_id = turn_persistence.persist_turn(
        user="alice",
        chat_id=None,
        user_text="order me a margherita pizza from anywhere good",
        assistant_text="Here are some options…",
        task_type="food_order",
        provider="zomato",
    )
    assert chat_id in chats
    assert chats[chat_id]["title"].startswith("order me a margherita pizza")
    assert chats[chat_id]["title"] != "New chat"
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant"]
