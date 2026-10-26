"""Memory lifecycle and provider boundary checks, with no external calls."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.core.conversations import (
    ConversationError, ConversationStore, MAX_PER_SESSION,
)
from backend.app.rag.generation import rephrase_question


def test_logout_during_generation_cannot_resurrect_memory():
    memory = ConversationStore()
    turn, _ = memory.begin("alice-session")
    memory.discard_owner("alice-session")
    with pytest.raises(ConversationError):
        memory.commit(turn, "What is our policy?")
    memory.abort(turn)
    with pytest.raises(ConversationError):
        memory.begin("alice-session", turn.conversation_id)


def test_parallel_turn_is_rejected_without_affecting_reserved_turn():
    memory = ConversationStore()
    initial, _ = memory.begin("alice-session")
    memory.commit(initial, "Where is the office?")
    pending, history = memory.begin("alice-session", initial.conversation_id)
    with pytest.raises(ConversationError, match="still being prepared"):
        memory.begin("alice-session", initial.conversation_id)
    assert history == ["Where is the office?"]
    memory.commit(pending, "Where is the Bengaluru office?")
    resumed, history = memory.begin("alice-session", initial.conversation_id)
    assert history == ["Where is the office?", "Where is the Bengaluru office?"]
    memory.abort(resumed)


def test_session_limit_evicts_old_idle_topic():
    moment = [0.0]
    memory = ConversationStore(clock=lambda: moment[0])
    ids = []
    for index in range(MAX_PER_SESSION + 1):
        turn, _ = memory.begin("alice-session")
        memory.commit(turn, f"Question {index}")
        ids.append(turn.conversation_id)
        moment[0] += 1
    with pytest.raises(ConversationError):
        memory.begin("alice-session", ids[0])
    turn, history = memory.begin("alice-session", ids[-1])
    assert history == [f"Question {MAX_PER_SESSION}"]
    memory.abort(turn)


def provider_response(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason=finish_reason,
    )])


def test_rephrase_sends_bounded_questions_as_data_not_messages():
    client = Mock()
    client.chat.completions.create.return_value = provider_response("Does remote work apply during probation?")
    history = [f"Prior question {number}" for number in range(7)]
    result = rephrase_question("Does it apply during probation?", history, client)
    assert result == "Does remote work apply during probation?"
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert json.loads(messages[-1]["content"]) == {
        "recent_questions": history[-4:], "latest_question": "Does it apply during probation?",
    }


@pytest.mark.parametrize("content,reason", [(None, "stop"), ("  ", "stop"), ("x" * 2001, "stop"), ("Incomplete rewrite", "length")])
def test_invalid_or_truncated_rewrite_does_not_become_a_search(content, reason):
    client = Mock()
    client.chat.completions.create.return_value = provider_response(content, reason)
    with pytest.raises(ValueError):
        rephrase_question("Does it apply?", ["Remote policy?"], client)
