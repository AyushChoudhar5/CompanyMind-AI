"""HR-only document upload and catalog management for the local prototype."""
from collections import Counter
import hashlib
import json
import logging
from pathlib import Path, PureWindowsPath
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from backend.app.core.access import Employee
from backend.app.core.auth import current_employee
from backend.app.rag.chunking import split_pages
from backend.app.rag.ingest import CATALOG_PATH, DATA_DIR, DocumentSpec, load_catalog, load_registered

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_FILE_BYTES + 64 * 1024
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["HR documents"])


def require_hr(employee: Employee = Depends(current_employee)):
    if employee.role != "hr_admin":
        raise HTTPException(403, "Only HR administrators can manage documents.")
    return employee


class UploadBodyLimit:
    """Authenticate before multipart parsing and bound even chunked requests.

    This local prototype buffers at most 20 MiB + form overhead per upload.
    Production also needs reverse-proxy limits, quotas and ingestion workers.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"].rstrip("/") != "/admin/documents":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])

        async def reject(code, detail):
            response = JSONResponse({"detail": detail}, status_code=code, headers={"Cache-Control": "no-store"})
            await response(scope, receive, send)

        authorization = headers.get(b"authorization", b"").decode("latin1").split()
        identities = scope["app"].state.identities
        employee = await run_in_threadpool(identities.resolve, authorization[1]) if len(authorization) == 2 and authorization[0].casefold() == "bearer" else None
        if employee is None:
            return await reject(401, "Sign in to access company documents.")
        if employee.role != "hr_admin":
            return await reject(403, "Only HR administrators can manage documents.")
        try:
            if int(headers.get(b"content-length", b"0")) > MAX_REQUEST_BYTES:
                return await reject(413, "Upload exceeds the 20 MiB file limit.")
        except ValueError:
            return await reject(400, "Invalid Content-Length.")

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                return await reject(413, "Upload exceeds the 20 MiB file limit.")
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


class UploadDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    version: str = Field(default="1.0", min_length=1, max_length=30)
    visibility: str
    owner_id: str | None = None


class DocumentManager:
    def __init__(self, store, identities, data_dir=DATA_DIR, catalog_path=CATALOG_PATH):
        self.store = store
        self.identities = identities
        self.data_dir = Path(data_dir).resolve()
        self.catalog_path = Path(catalog_path).resolve()

    def lock(self):
        return FileLock(str(self.catalog_path) + ".lock", timeout=10)

    def _save(self, specs):
        # Write a complete valid catalog, then atomically replace the old file.
        temporary = self.catalog_path.with_name(".catalog-" + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps([spec.model_dump(exclude_none=True) for spec in specs], indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.catalog_path)
        finally:
            temporary.unlink(missing_ok=True)

    def employees(self):
        with self.identities.connect() as db:
            rows = db.execute("SELECT user_id,username,role FROM users WHERE active=1 ORDER BY username").fetchall()
        return [dict(row) for row in rows]

    def list_documents(self):
        specs = load_catalog(self.catalog_path)
        indexed = self.store.get(include=["metadatas"])["metadatas"]
        counts = Counter(item.get("document_id") for item in indexed if item.get("upload_status") != "staging")
        owners = {item["user_id"]: item["username"] for item in self.employees()}
        return [{
            "document_id": spec.document_id, "title": spec.title, "category": spec.category,
            "version": spec.version, "source": spec.source_name or Path(spec.path).name,
            "visibility": spec.visibility, "owner_id": spec.owner_id,
            "owner_username": owners.get(spec.owner_id), "chunks": counts[spec.document_id],
        } for spec in specs]

    def upload(self, file: UploadFile, details: UploadDetails):
        filename = PureWindowsPath(file.filename or "").name.strip()
        extension = Path(filename).suffix.lower()
        if extension not in {".txt", ".pdf"} or len(filename) > 250 or any(ord(char) < 32 for char in filename):
            raise HTTPException(422, "Choose a TXT or text-based PDF file with a valid filename.")
        document_id = "doc_" + uuid.uuid4().hex
        # A client filename is display metadata, never a filesystem destination.
        relative = f"uploads/{document_id}{extension}"
        try:
            spec = DocumentSpec(document_id=document_id, path=relative, source_name=filename, **details.model_dump())
        except ValidationError as error:
            raise HTTPException(422, str(error.errors()[0]["msg"])) from None
        if spec.visibility == "employee" and not self.identities.user_exists(spec.owner_id):
            raise HTTPException(422, "Choose an existing active employee as the document owner.")
        destination = (self.data_dir / relative).resolve()
        uploads = (self.data_dir / "uploads").resolve()
        if not destination.is_relative_to(uploads):
            raise HTTPException(422, "Invalid upload destination.")
        uploads.mkdir(parents=True, exist_ok=True)
        indexed_ids = []
        complete = False
        created = False
        try:
            size = 0
            digest = hashlib.sha256()
            with destination.open("xb") as output:
                created = True
                while data := file.file.read(64 * 1024):
                    size += len(data)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(413, "Upload exceeds the 20 MiB file limit.")
                    digest.update(data)
                    output.write(data)
            if size == 0:
                raise HTTPException(422, "The uploaded file is empty.")
            if extension == ".pdf":
                with destination.open("rb") as saved:
                    if not saved.read(5).startswith(b"%PDF-"):
                        raise HTTPException(422, "The uploaded file is not a valid PDF.")
            try:
                pages = load_registered(spec, self.data_dir)
                chunks = split_pages(pages)
            except Exception:
                raise HTTPException(422, "Cannot extract this document. Use UTF-8 TXT or an unencrypted text-based PDF; scanned pages need OCR.") from None
            if not chunks:
                raise HTTPException(422, "The uploaded file contains no searchable text.")
            if len(chunks) > 1000:
                raise HTTPException(422, "This document is too long for the current upload workflow. Split it into smaller documents.")
            spec = spec.model_copy(update={"sha256": digest.hexdigest()})
            load_catalog(self.catalog_path)  # Initialize a live registry outside its operation lock.
            with self.lock():
                specs = load_catalog(self.catalog_path)
                if any(old.sha256 == spec.sha256 and old.visibility == spec.visibility
                       and old.owner_id == spec.owner_id and old.title == spec.title
                       and old.version == spec.version for old in specs):
                    raise HTTPException(409, "This document and version have already been uploaded for that audience.")
                indexed_ids = [f"{document_id}:{i}" for i in range(len(chunks))]
                for chunk in chunks:
                    chunk.metadata["upload_status"] = "staging"
                self.store.add_documents(chunks, ids=indexed_ids)
                self._save([*specs, spec])
                # Native metadata update avoids generating embeddings a second time.
                # Kept here as the isolated LangChain/Chroma adapter boundary.
                self.store._collection.update(ids=indexed_ids, metadatas=[
                    {**chunk.metadata, "upload_status": "published"} for chunk in chunks
                ])
                complete = True
            return {"document_id": document_id, "title": spec.title, "source": filename,
                    "version": spec.version, "visibility": spec.visibility,
                    "owner_id": spec.owner_id, "chunks": len(chunks)}
        except HTTPException:
            raise
        except Timeout:
            raise HTTPException(409, "Another document operation is in progress. Try again shortly.") from None
        except Exception:
            raise HTTPException(503, "Document indexing could not finish. Please retry or contact the administrator.") from None
        finally:
            if not complete:
                if indexed_ids:
                    # Best-effort rollback across Chroma and the file catalog.
                    # Production needs durable ingestion jobs and reconciliation.
                    try:
                        self.store.delete(ids=indexed_ids)
                    except Exception:
                        logger.error("Upload rollback needs reconciliation: document_id=%s", document_id)
                    try:
                        with self.lock():
                            current = load_catalog(self.catalog_path)
                            if any(item.document_id == document_id for item in current):
                                self._save([item for item in current if item.document_id != document_id])
                    except Exception:
                        logger.error("Catalog rollback needs reconciliation: document_id=%s", document_id)
                if created:
                    destination.unlink(missing_ok=True)

    def delete(self, document_id):
        try:
            load_catalog(self.catalog_path)
            with self.lock():
                specs = load_catalog(self.catalog_path)
                spec = next((item for item in specs if item.document_id == document_id), None)
                if spec is None:
                    raise HTTPException(404, "Document not found.")
                # Keep source files, including the original demos, for recovery.
                # Removing the catalog entry prevents a later CLI reindex revival.
                records = self.store.get(where={"document_id": document_id}, include=["metadatas"])
                ids = records["ids"]
                if ids:
                    self.store._collection.update(ids=ids, metadatas=[{**item, "upload_status": "staging"} for item in records["metadatas"]])
                try:
                    self._save([item for item in specs if item.document_id != document_id])
                except Exception:
                    if ids:
                        self.store._collection.update(ids=ids, metadatas=[{**item, "upload_status": item.get("upload_status", "published")} for item in records["metadatas"]])
                    raise
                cleanup_pending = False
                if ids:
                    try:
                        self.store.delete(ids=ids)
                    except Exception:
                        cleanup_pending = True
                        logger.error("Removal index cleanup pending: document_id=%s", document_id)
            return {"document_id": document_id, "removed_chunks": 0 if cleanup_pending else len(ids), "cleanup_pending": cleanup_pending}
        except HTTPException:
            raise
        except Timeout:
            raise HTTPException(409, "Another document operation is in progress. Try again shortly.") from None
        except Exception:
            raise HTTPException(503, "Document removal did not finish. Contact the administrator before reindexing.") from None


@router.get("/employees")
def employees(request: Request, employee: Employee = Depends(require_hr)):
    return {"employees": request.app.state.document_manager.employees()}


@router.get("/documents")
def documents(request: Request, employee: Employee = Depends(require_hr)):
    return {"documents": request.app.state.document_manager.list_documents()}


@router.post("/documents", status_code=201)
def upload_document(request: Request, metadata: str = Form(max_length=4096), file: UploadFile = File(), employee: Employee = Depends(require_hr)):
    try:
        details = UploadDetails.model_validate_json(metadata)
        return request.app.state.document_manager.upload(file, details)
    except ValidationError:
        raise HTTPException(422, "Check the document title, version, audience and owner.") from None
    finally:
        file.file.close()


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, request: Request, employee: Employee = Depends(require_hr)):
    return request.app.state.document_manager.delete(document_id)
