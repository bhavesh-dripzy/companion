from __future__ import annotations
from unittest.mock import MagicMock, patch
from companion_api.messages_repo import MessageRepository
from companion_api.profile_cache import ProfileCacheRepository
from companion_api.leadgen_queue import send_turn_event


def test_messages_repo_create():
    with patch("companion_api.messages_repo._resource") as mock_resource:
        mock_table = MagicMock()
        mock_resource.return_value.Table.return_value = mock_table

        repo = MessageRepository(table_name="test-messages")
        repo.create(
            member_id="user-123",
            message_id="msg-456",
            role="user",
            text="hello",
            task_type="food_delivery",
            provider="zomato",
        )

        mock_table.put_item.assert_called_once()
        _, kwargs = mock_table.put_item.call_args
        item = kwargs["Item"]
        assert item["member_id"] == "user-123"
        assert item["role"] == "user"
        assert item["text"] == "hello"
        assert item["task_type"] == "food_delivery"
        assert item["provider"] == "zomato"
        assert "created_at_message_id" in item


def test_messages_repo_list_recent_returns_chronological_items():
    with patch("companion_api.messages_repo._resource") as mock_resource:
        mock_table = MagicMock()
        mock_resource.return_value.Table.return_value = mock_table
        mock_table.query.return_value = {
            "Items": [
                {
                    "member_id": "user-123",
                    "created_at_message_id": "2026-06-01T10:01:00Z#assistant",
                    "role": "assistant",
                    "text": "hi",
                },
                {
                    "member_id": "user-123",
                    "created_at_message_id": "2026-06-01T10:00:00Z#user",
                    "role": "user",
                    "text": "hello",
                },
            ]
        }

        repo = MessageRepository(table_name="test-messages")
        items = repo.list_recent("user-123", limit=2)

        assert [item["role"] for item in items] == ["user", "assistant"]
        mock_table.query.assert_called_once()


def test_profile_cache_repo_get_and_put():
    with patch("companion_api.profile_cache._resource") as mock_resource:
        mock_table = MagicMock()
        mock_resource.return_value.Table.return_value = mock_table

        # Test Put
        repo = ProfileCacheRepository(table_name="test-profile")
        repo.put(user_id="user-123", profile={"name": "Aashish", "city": "Gurugram"})

        mock_table.put_item.assert_called_once()
        _, kwargs = mock_table.put_item.call_args
        item = kwargs["Item"]
        assert item["PK"] == "USER#user-123"
        assert item["SK"] == "PROFILE"
        assert item["name"] == "Aashish"
        assert item["city"] == "Gurugram"

        # Test Get
        mock_table.get_item.return_value = {
            "Item": {
                "PK": "USER#user-123",
                "SK": "PROFILE",
                "name": "Aashish",
                "city": "Gurugram",
            }
        }
        res = repo.get(user_id="user-123")
        assert res["name"] == "Aashish"
        assert res["city"] == "Gurugram"


def test_send_turn_event():
    with patch("companion_api.leadgen_queue.get_sqs_client") as mock_sqs, patch(
        "companion_api.settings.LEADGEN_QUEUE_URL", "https://sqs.mock/queue"
    ):
        mock_client = MagicMock()
        mock_sqs.return_value = mock_client
        mock_client.send_message.return_value = {"MessageId": "mock-msg-id"}

        msg_id = send_turn_event(
            user_id="user-123",
            message_id="turn-456",
            user_message="hello",
            assistant_message="hi",
            task_type="food_delivery",
            routed_provider="zomato",
            member_id="user-123",
        )

        assert msg_id == "mock-msg-id"
        mock_client.send_message.assert_called_once()


@patch("app.services.agents.pipeline.classify_agent_routing")
@patch("companion_api.profile_cache.ProfileCacheRepository")
def test_run_agent_turn_personalization(mock_repo, mock_classify):
    # Mock profile cache lookup
    mock_instance = MagicMock()
    mock_repo.return_value = mock_instance
    mock_instance.get.return_value = {
        "user_id": "user-123",
        "addresses": {
            "office": {
                "provider_address_id": "addr_office_99",
                "address_line": "Sector 13, DLF, Gurugram"
            },
            "home": {
                "provider_address_id": "addr_home_11",
                "address_line": "Sector 56, Gurugram"
            }
        },
        "interests": [{"category": "food", "weight": 0.8, "details": ["ramen"]}],
        "open_leads": [],
        "name": "Aashish"
    }

    # Mock router to return normal query to avoid MCP/Tool execution overhead
    mock_classify.return_value = {
        "task_type": "normal_query",
        "reasoning": "testing-profile-hook"
    }

    from app.services.agents.pipeline import run_agent_turn
    from app.services.llm_config import ResolvedLlm

    mock_llm = ResolvedLlm("gemini", "mock-key", "gemini-2.5-flash-lite")

    with patch("app.services.agents.pipeline.plan_normal_query") as mock_plan, \
         patch("app.services.agents.pipeline.general_chat_responder.reply_general_chat") as mock_reply:
         
        mock_plan.return_value = {}
        mock_reply.return_value = "Mocked reply"

        # Execute turn referencing 'office' without passing address_id
        result = run_agent_turn(
            user_message="Order lunch to my office",
            address_id="",
            execute_tools=False,
            user_id="user-123",
            llm=mock_llm,
            session_addresses=[]
        )

        # Assertions
        # 1. Profile was loaded
        mock_instance.get.assert_called_once_with("user-123")
        
        # 2. Address index seeded from profile (both home and office)
        seeded = result["session_addresses"]
        assert len(seeded) == 2
        assert any(e["address_id"] == "addr_office_99" for e in seeded)
        assert any(e["address_id"] == "addr_home_11" for e in seeded)
        
        # 3. address_context source is set to profile
        assert result["address_context"]["source"] == "profile"
        assert result["address_context"]["address_id"] == "addr_office_99"


def test_lead_cache_repo_get_put_query():
    from companion_api.lead_cache_repo import LeadCacheRepository
    with patch("companion_api.lead_cache_repo._resource") as mock_resource:
        mock_table = MagicMock()
        mock_resource.return_value.Table.return_value = mock_table

        # Test put
        repo = LeadCacheRepository(table_name="test-leads")
        lead_data = {
            "lead_id": "lead-111",
            "category": "electronics",
            "status": "open",
            "product": "MacBook Pro",
            "attrs": {"color": "space gray"},
            "budget_signal": "high",
            "intent_score": 0.95,
            "confidence": 1.0,
            "last_seen_at": "2026-05-31T09:00:00Z"
        }
        repo.put(lead_data)

        mock_table.put_item.assert_called_once()
        _, kwargs = mock_table.put_item.call_args
        item = kwargs["Item"]
        assert item["lead_id"] == "lead-111"
        assert item["category"] == "electronics"
        assert item["status"] == "open"
        assert item["status_last_seen"] == "open#2026-05-31T09:00:00Z"
        assert item["product"] == "MacBook Pro"
        assert item["attrs"] == {"color": "space gray"}
        assert "updated_at" in item

        # Test get
        mock_table.get_item.return_value = {
            "Item": item
        }
        fetched = repo.get("lead-111")
        assert fetched["lead_id"] == "lead-111"
        assert fetched["category"] == "electronics"

        # Test query/list_by_category
        mock_table.query.return_value = {
            "Items": [item]
        }
        results = repo.list_by_category("electronics", status_prefix="open")
        assert len(results) == 1
        assert results[0]["lead_id"] == "lead-111"

        # Verify query call args
        mock_table.query.assert_called_once()
        _, query_kwargs = mock_table.query.call_args
        assert query_kwargs["IndexName"] == "category-status-index"

