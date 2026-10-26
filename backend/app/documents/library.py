"""Permission-checked previews of indexed passages, not direct filesystem access."""
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.core.access import Employee, can_read, document_filter
from backend.app.core.auth import current_employee

router = APIRouter(prefix="/documents", tags=["Document library"])
PREVIEW_LIMIT = 20


@router.get("/{document_id}/preview")
def preview_document(document_id: str, request: Request, employee: Employee = Depends(current_employee)):
    # Filter BEFORE reading text. A guessed private ID behaves like a missing ID.
    if not document_id or len(document_id) > 100:
        raise HTTPException(404, "Document not found or unavailable to your account.")
    records = request.app.state.vector_store.get(
        where={"$and": [document_filter(employee), {"document_id": document_id}]},
        include=["metadatas", "documents"], limit=PREVIEW_LIMIT + 1,
    )
    if not records["ids"]:
        raise HTTPException(404, "Document not found or unavailable to your account.")
    metadata = records["metadatas"]
    if any(not can_read(item, employee) or item.get("document_id") != document_id for item in metadata):
        raise HTTPException(503, "Document permission check failed. Contact the administrator.")
    if len(records["documents"]) != len(metadata):
        raise HTTPException(503, "Document preview is unavailable. Try again later.")
    pairs = list(zip(metadata, records["documents"]))
    pairs.sort(key=lambda pair: (pair[0].get("page", 0), pair[0].get("start_index", 0)))
    item = metadata[0]
    return {
        "document": {key: item.get(key) for key in
                     ("document_id", "title", "source", "category", "version", "visibility")},
        "passages": [{"text": text, "page": meta.get("page")} for meta, text in pairs[:PREVIEW_LIMIT]],
        "truncated": len(pairs) > PREVIEW_LIMIT,
    }
