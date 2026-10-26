from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from dataclasses import asdict
from backend.app.core.access import Employee
from backend.app.documents.library import router as library_router
from backend.app.core.conversations import ConversationError, ConversationStore, session_owner
from backend.app.documents.admin import DocumentManager, UploadBodyLimit, router as document_router
from backend.app.core.auth import IdentityStore, SESSION_SECONDS, bearer, current_employee
from backend.app.rag.retrieval import AccessViolation, retrieve
from pydantic import BaseModel, ConfigDict, Field

from backend.app.rag.vector_store import accessible_documents, get_vector_store

from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from backend.app.rag.generation import create_llm_client, generate_answer, rephrase_question


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.conversations = ConversationStore()
    app.state.vector_store = get_vector_store()
    app.state.llm_client = create_llm_client()
    app.state.identities = IdentityStore()
    app.state.document_manager = DocumentManager(app.state.vector_store, app.state.identities)

    try:
        yield
    finally:
        app.state.llm_client.close()
        app.state.vector_store = None

app = FastAPI(
    title="CompanyMind AI API",
    version="0.5.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def private_response_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


app.add_middleware(UploadBodyLimit)
app.include_router(document_router)
app.include_router(library_router)


class SearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)
    document_id: str | None = Field(default=None, min_length=1, max_length=100)


class ChatRequest(SearchRequest):
    conversation_id: UUID | None = None


class SearchResult(BaseModel):
    reference: int
    source: str
    text: str
    document_id: str | None = None
    title: str | None = None
    version: str | None = None
    page: int | None = None


class SearchResponse(BaseModel):
    question: str
    results: list[SearchResult]

class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    sources: list[SearchResult]
    truncated: bool


@app.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
def chat(payload: ChatRequest, request: Request, employee: Employee = Depends(current_employee),
         credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    memory = request.app.state.conversations
    turn = None
    committed = False
    try:
        turn, history = memory.begin(
            session_owner(employee, credentials), payload.conversation_id, payload.document_id,
        )
        # History resolves the topic only. It cannot supply facts for the answer.
        question = rephrase_question(question=payload.question, history=history, client=request.app.state.llm_client) if history else payload.question
        documents = permitted_passages(request, payload, employee, question=question)
        if not documents:
            raise HTTPException(409, "No indexed passages found. Ask HR to index or upload documents.")
        answer, truncated = generate_answer(
            question=question, documents=documents, client=request.app.state.llm_client,
        )
        memory.commit(turn, question)
        committed = True
    except ConversationError as error:
        raise HTTPException(error.status_code, error.detail) from None
    except RateLimitError:
        raise HTTPException(429, "AI provider rate limit reached. Try again later.") from None
    except AuthenticationError:
        raise HTTPException(503, "AI provider credentials need attention.") from None
    except APITimeoutError:
        raise HTTPException(504, "The AI provider took too long to respond.") from None
    except APIConnectionError:
        raise HTTPException(503, "Could not connect to the AI provider.") from None
    except APIStatusError:
        raise HTTPException(502, "The AI provider returned an error.") from None
    except ValueError:
        raise HTTPException(502, "The AI provider returned an empty answer.") from None
    finally:
        if turn is not None and not committed:
            memory.abort(turn)

    sources = [
        SearchResult(
            reference=number, source=str(document.metadata.get("source", "Unknown")),
            text=document.page_content, document_id=document.metadata.get("document_id"),
            title=document.metadata.get("title"), version=document.metadata.get("version"),
            page=document.metadata.get("page"),
        )
        for number, document in enumerate(documents, start=1)
    ]
    return ChatResponse(
        conversation_id=turn.conversation_id, answer=answer, sources=sources, truncated=truncated,
    )


@app.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, request: Request,
                        employee: Employee = Depends(current_employee),
                        credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    try:
        request.app.state.conversations.delete(session_owner(employee, credentials), conversation_id)
    except ConversationError as error:
        raise HTTPException(error.status_code, error.detail) from None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse, response_model_exclude_none=True)
def search(payload: SearchRequest, request: Request, employee: Employee = Depends(current_employee)):
    documents = permitted_passages(request, payload, employee)

    if not documents:
        raise HTTPException(
            status_code=409,
            detail="No indexed passages found. Ask HR to index or upload documents.",
        )

    results = [
        SearchResult(
            reference=number,
            source=str(document.metadata.get("source", "Unknown")),
            text=document.page_content,
            document_id=document.metadata.get('document_id'),
            title=document.metadata.get('title'),
            version=document.metadata.get('version'),
            page=document.metadata.get('page'),
        )
        for number, document in enumerate(documents, start=1)
    ]

    return SearchResponse(
        question=payload.question,
        results=results,
    )

                                   

def permitted_passages(request, payload, employee, question=None):
    try:
        return retrieve(request.app.state.vector_store, question or payload.question, payload.top_k, employee, payload.document_id)
    except AccessViolation:
        raise HTTPException(503, "Document permission check failed. Contact the administrator.") from None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=128)


@app.post("/auth/login")
def login(payload: LoginRequest, request: Request):
    address = request.client.host if request.client else "unknown"
    token, employee = request.app.state.identities.login(payload.username, payload.password, address)
    return {"access_token": token, "token_type": "bearer", "expires_in": SESSION_SECONDS,
            "user": asdict(employee)}


@app.get("/auth/me")
def me(employee: Employee = Depends(current_employee)):
    return asdict(employee)


@app.post("/auth/logout", status_code=204)
def logout(request: Request, employee: Employee = Depends(current_employee),
           credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    request.app.state.identities.logout(credentials.credentials)
    request.app.state.conversations.discard_owner(session_owner(employee, credentials))


@app.get("/documents")
def documents(request: Request, employee: Employee = Depends(current_employee)):
    return {"documents": accessible_documents(request.app.state.vector_store, employee)}
