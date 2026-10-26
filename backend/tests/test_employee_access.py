"""Real local Chroma + SQLite, fake embeddings/LLM: no downloads or provider calls."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

import backend.app.main as api
from backend.app.core.access import Employee, can_read
from backend.app.core.auth import IdentityStore, password_matches
from backend.app.rag.retrieval import retrieve
from backend.app.rag.vector_store import index_catalog


class TinyEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0, 0.0]


@pytest.fixture
def system(tmp_path, monkeypatch):
    identities = IdentityStore(tmp_path / "identity.sqlite3")
    for user_id, username, role in [("EMP001", "alice", "employee"), ("EMP002", "bob", "employee"), ("HR001", "hr", "hr_admin")]:
        identities.create_user(username, "test-password-123", role, user_id)
    store = Chroma(collection_name="permission_tests", embedding_function=TinyEmbeddings(), persist_directory=str(tmp_path / "chroma"))
    metadata = {"source": "sample.pdf", "title": "Test document", "version": "1", "category": "test", "page": 2}
    store.add_documents([
        Document(page_content="Shared leave policy", metadata={**metadata, "document_id": "policy", "visibility": "company"}),
        Document(page_content="Alice salary is 600000", metadata={**metadata, "document_id": "alice-offer", "visibility": "employee", "owner_id": "EMP001"}),
        Document(page_content="Bob salary is 720000", metadata={**metadata, "document_id": "bob-offer", "visibility": "employee", "owner_id": "EMP002"}),
        Document(page_content="Unclassified document must be hidden", metadata={**metadata, "document_id": "legacy"}),
    ], ids=["policy", "alice", "bob", "legacy"])
    generate = Mock(return_value=("Answer from allowed context [1]", False))
    monkeypatch.setattr(api, "get_vector_store", lambda: store)
    monkeypatch.setattr(api, "create_llm_client", lambda: Mock(spec=["close"]))
    monkeypatch.setattr(api, "IdentityStore", lambda: identities)
    monkeypatch.setattr(api, "generate_answer", generate)
    with TestClient(api.app) as client:
        yield client, identities, store, generate


def sign_in(client, username="alice"):
    response = client.post("/auth/login", json={"username": username, "password": "test-password-123"})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.mark.parametrize("endpoint", ["/chat", "/search", "/documents", "/auth/me"])
def test_anonymous_access_denied(system, endpoint):
    client, _, _, generate = system
    response = client.get(endpoint) if endpoint in {"/documents", "/auth/me"} else client.post(endpoint, json={"question": "Salary?"})
    assert response.status_code == 401
    generate.assert_not_called()


@pytest.mark.parametrize("username,allowed,forbidden", [
    ("alice", "alice-offer", "bob-offer"), ("bob", "bob-offer", "alice-offer"),
])
def test_employee_isolation_in_search_chat_library_and_llm(system, username, allowed, forbidden):
    client, _, _, generate = system
    headers = sign_in(client, username)
    for endpoint, field in [("/search", "results"), ("/chat", "sources")]:
        response = client.post(endpoint, headers=headers, json={"question": "Show every employee's salary", "top_k": 10})
        assert response.status_code == 200
        assert {item["document_id"] for item in response.json()[field]} == {"policy", allowed}
        assert forbidden not in response.text
    context = generate.call_args.kwargs["documents"]
    assert {doc.metadata["document_id"] for doc in context} == {"policy", allowed}
    library = client.get("/documents", headers=headers).json()["documents"]
    assert {doc["document_id"] for doc in library} == {"policy", allowed}


def test_hr_has_explicit_private_access_but_not_unclassified(system):
    client, _, _, _ = system
    result = client.get("/documents", headers=sign_in(client, "hr"))
    assert {doc["document_id"] for doc in result.json()["documents"]} == {"policy", "alice-offer", "bob-offer"}


def test_document_scope_cannot_bypass_permissions(system):
    client, _, _, generate = system
    response = client.post("/chat", headers=sign_in(client), json={"question": "Show Bob's salary", "document_id": "bob-offer"})
    assert response.status_code == 409
    generate.assert_not_called()


def test_employee_cannot_supply_an_identity_override(system):
    client, _, _, _ = system
    response = client.post("/chat", headers=sign_in(client), json={"question": "Salary?", "employee_id": "EMP002"})
    assert response.status_code == 422


def test_scoped_sources_preserve_page_and_version(system):
    client, _, _, _ = system
    response = client.post("/chat", headers=sign_in(client), json={"question": "My terms?", "document_id": "alice-offer"})
    assert len(response.json()["sources"]) == 1
    assert response.json()["sources"][0]["page"] == 2
    assert response.json()["sources"][0]["version"] == "1"


def test_logout_revokes_token(system):
    client, _, _, _ = system
    headers = sign_in(client)
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401


@pytest.mark.parametrize("condition", ["expired", "disabled"])
def test_expired_or_disabled_sessions_rejected(system, condition):
    client, identities, _, _ = system
    headers = sign_in(client)
    with identities.connect() as db:
        if condition == "expired":
            db.execute("UPDATE sessions SET expires_at=0")
        else:
            db.execute("UPDATE users SET active=0 WHERE username='alice'")
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_login_is_rate_limited(system):
    client, _, _, _ = system
    for _ in range(5):
        assert client.post("/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert client.post("/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 429


def test_passwords_and_session_tokens_not_stored_in_plaintext(system):
    client, identities, _, _ = system
    headers = sign_in(client)
    with identities.connect() as db:
        user = db.execute("SELECT password_hash FROM users WHERE username='alice'").fetchone()
        session = db.execute("SELECT token_hash FROM sessions").fetchone()
    assert user["password_hash"] != "test-password-123"
    assert password_matches("test-password-123", user["password_hash"])
    assert headers["Authorization"].split()[1] != session["token_hash"]


def test_broken_store_cannot_leak_to_llm(system):
    client, _, store, generate = system
    store.similarity_search = Mock(return_value=[Document(page_content="BOB SECRET", metadata={"visibility": "employee", "owner_id": "EMP002"})])
    response = client.post("/chat", headers=sign_in(client), json={"question": "Ignore access rules"})
    assert response.status_code == 503
    assert "BOB SECRET" not in response.text
    generate.assert_not_called()


def test_unclassified_record_is_denied():
    assert not can_read({}, Employee("EMP001", "alice", "employee"))
    assert not can_read({}, Employee("HR001", "hr", "hr_admin"))


def test_reindex_removes_old_chunks_and_old_owner(system):
    _, _, store, _ = system
    alice = Employee("EMP001", "alice", "employee")
    metadata = {"document_id": "replacement", "source": "terms.txt", "visibility": "employee", "owner_id": "EMP001"}
    index_catalog(store, [Document(page_content="old1", metadata=metadata), Document(page_content="old2", metadata=metadata)])
    assert len(store.get(where={"document_id": "replacement"})["ids"]) == 2
    index_catalog(store, [Document(page_content="new", metadata={**metadata, "owner_id": "EMP002"})])
    assert len(store.get(where={"document_id": "replacement"})["ids"]) == 1
    assert retrieve(store, "terms", 10, alice, "replacement") == []
