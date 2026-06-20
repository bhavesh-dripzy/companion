"""Request-model contract fixes from the Zepto prod test (F11 history-null, F14 blank)."""

import pytest
from pydantic import ValidationError

from companion_api.models import AgentQueryRequest


def test_history_accepts_null_provider():
    # F11: the API emits routed_provider=null; echoing it back in history must not 422.
    req = AgentQueryRequest(
        message="hi",
        history=[
            {"role": "user", "content": "search zepto for milk"},
            {"role": "assistant", "content": "Here you go", "provider": None},
        ],
    )
    assert req.history[1]["provider"] is None


def test_blank_message_rejected():
    # F14: a whitespace-only message must not spin up the pipeline.
    with pytest.raises(ValidationError):
        AgentQueryRequest(message="   ")


def test_empty_message_rejected():
    with pytest.raises(ValidationError):
        AgentQueryRequest(message="")
