from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document

import backend.app.main as api
from backend.app.core.access import Employee, document_filter
from backend.app.core.auth import IdentityStore


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    """Prepare an isolated API with controlled external dependencies."""

    document = Document(
        page_content="Employees receive 12 casual leave days per year.",
        metadata={"source": "leave_policy.txt", "visibility": "company"},
    )

    fake_store = Mock(spec=["similarity_search"])
    fake_store.similarity_search.return_value = [document]

    fake_llm_client = Mock(spec=["close"])

    fake_generate = Mock(
        return_value=("Employees receive 12 casual leave days. [1]", False)
    )

    # Replace dependencies before application startup.
    monkeypatch.setattr(api, "get_vector_store", lambda: fake_store)
    monkeypatch.setattr(api, "create_llm_client", lambda: fake_llm_client)
    monkeypatch.setattr(api, "generate_answer", fake_generate)

    monkeypatch.setattr(api, "IdentityStore", lambda: IdentityStore(tmp_path / "identity.sqlite3"))
    api.app.dependency_overrides[api.current_employee] = lambda: Employee("EMP001", "alice", "employee")

    # Entering this block runs the application's lifespan startup.
    try:
        with TestClient(api.app) as client:
            yield client, fake_store, fake_generate
    finally:
        api.app.dependency_overrides.clear()


def test_chat_returns_answer_and_source(api_client):
    client, store, generate = api_client
    question = "How many casual leave days do employees receive?"

    response = client.post(
        "/chat",
        json={"question": question, "top_k": 2},
    )

    assert response.status_code == 200

    body = response.json()
    assert body["answer"] == "Employees receive 12 casual leave days. [1]"
    assert body["truncated"] is False
    assert body["sources"] == [
        {
            "reference": 1,
            "source": "leave_policy.txt",
            "text": "Employees receive 12 casual leave days per year.",
        }
    ]

    # Check that the API passed the correct inputs to each component.
    store.similarity_search.assert_called_once_with(
        query=question,
        k=2,
        filter=document_filter(Employee('EMP001', 'alice', 'employee')),
    )
    generate.assert_called_once()
    assert generate.call_args.kwargs["question"] == question
    assert (
        generate.call_args.kwargs["documents"]
        == store.similarity_search.return_value
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "   ", "top_k": 3},
        {"question": "Leave policy?", "top_k": 0},
        {"question": "Leave policy?", "top_k": 11},
    ],
)
def test_invalid_input_is_rejected(api_client, payload):
    client, store, generate = api_client

    response = client.post("/chat", json=payload)

    assert response.status_code == 422
    store.similarity_search.assert_not_called()
    generate.assert_not_called()


@pytest.mark.parametrize("endpoint", ["/search", "/chat"])
def test_empty_database_returns_clear_error(api_client, endpoint):
    client, store, generate = api_client
    store.similarity_search.return_value = []

    response = client.post(
        endpoint,
        json={"question": "What is the leave policy?"},
    )

    assert response.status_code == 409
    assert "No indexed passages" in response.json()["detail"]
    generate.assert_not_called()


def test_generation_failure_returns_safe_error(api_client):
    client, store, generate = api_client
    generate.side_effect = ValueError("Internal provider details")

    response = client.post(
        "/chat",
        json={"question": "What is the leave policy?"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "The AI provider returned an empty answer."
    )
    assert "Internal provider details" not in response.text


def test_search_does_not_call_llm(api_client):
    client, store, generate = api_client

    response = client.post(
        "/search",
        json={"question": "What is the leave policy?"},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["source"] == "leave_policy.txt"
    generate.assert_not_called()