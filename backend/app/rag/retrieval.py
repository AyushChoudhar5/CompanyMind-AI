"""The one retrieval entry point used by employee-facing API routes."""
from backend.app.core.access import Employee, can_read, document_filter


class AccessViolation(RuntimeError):
    pass


def retrieve(store, question: str, top_k: int, employee: Employee, document_id: str | None = None):
    permitted = document_filter(employee)
    if document_id:
        permitted = {"$and": [permitted, {"document_id": document_id}]}
    documents = store.similarity_search(query=question, k=top_k, filter=permitted)
    # Defense in depth: a broken adapter must never pass another user's text
    # to the LLM or expose it in the API response.
    if any(not can_read(document.metadata, employee) for document in documents):
        raise AccessViolation("Retrieval returned a passage outside the user's permissions.")
    if document_id and any(document.metadata.get("document_id") != document_id for document in documents):
        raise AccessViolation("Retrieval returned a passage outside the selected document.")
    return documents
