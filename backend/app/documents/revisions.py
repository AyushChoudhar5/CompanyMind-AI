"""HR-only draft, impact-review and effective-date publication workflow.

The catalog and Chroma are separate stores. Publication stages both versions,
writes the catalog, then exposes the date-gated index. Failures restore the old
state where possible and otherwise leave records hidden for reconciliation.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import difflib
import hashlib
import json
from pathlib import Path
from threading import Event, RLock
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from groq import RateLimitError
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from backend.app.core import publication as pub
from backend.app.documents.admin import UploadDetails, require_hr
from backend.app.rag.ingest import load_catalog
from backend.app.rag.impact import compare_question

router = APIRouter(prefix="/admin", tags=["Policy revisions"])
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


class RevisionDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: str = Field(min_length=1, max_length=30)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[Question] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_questions(self):
        if len({question.casefold() for question in self.questions}) != len(self.questions):
            raise ValueError("Use distinct test questions.")
        return self


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: uuid.UUID
    acknowledge_review: bool
    effective_at: AwareDatetime | None = None


def find_spec(manager, identifier):
    item = next((spec for spec in load_catalog(manager.catalog_path) if spec.document_id == identifier), None)
    if item is None:
        raise HTTPException(404, "Document not found.")
    return item


def summary(spec):
    return {"document_id": spec.document_id, "title": spec.title, "version": spec.version,
            "visibility": spec.visibility, "owner_id": spec.owner_id, "status": pub.status(spec),
            "source": spec.source_name or Path(spec.path).name, "replaces_id": spec.replaces_id,
            "effective_from": spec.effective_from, "effective_until": spec.effective_until,
            "published_by": spec.published_by, "published_at": spec.published_at}


def signature(manager, spec):
    records = manager.store.get(where={"document_id": spec.document_id}, include=["documents", "metadatas"])
    if not records["ids"]:
        raise HTTPException(409, "A policy version is not indexed. Index it before reviewing.")
    ordered = sorted(zip(records["ids"], records["documents"], records["metadatas"]))
    payload = json.dumps({"spec": spec.model_dump(), "index": ordered}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def valid_pair(manager, revision_id):
    draft = find_spec(manager, revision_id)
    if not draft.replaces_id or draft.lifecycle != "draft":
        raise HTTPException(409, "Choose an unpublished draft revision.")
    base = find_spec(manager, draft.replaces_id)
    if pub.status(base) != "published" or base.effective_until != pub.END_OF_TIME:
        raise HTTPException(409, "The original version changed or already has a scheduled replacement. Create a revision of the current policy.")
    if (base.visibility, base.owner_id) != (draft.visibility, draft.owner_id):
        raise HTTPException(409, "The revision audience does not match the original document.")
    return base, draft


def suggestions(spec):
    if "remote" in (spec.title + " " + spec.category).lower():
        return ["How many days per week can employees work remotely?", "Who approves remote work?",
                "Does remote working apply during probation?", "How are exceptions to the standard arrangement approved?"]
    if "leave" in (spec.title + " " + spec.category).lower():
        return ["How many casual leave days do employees receive?", "Can annual leave carry forward?",
                "Who approves leave requests?", "When is a medical certificate required?"]
    return ["Who is covered by this policy?", "What are the main rules or obligations?",
            "What approvals are required?", "What exceptions or eligibility conditions apply?"]


class RevisionService:
    def __init__(self, comparison=None):
        self.comparison = comparison or compare_question
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="policy-review")
        self._jobs = {}
        self._guard = RLock()
        self._closed = Event()

    def close(self):
        self._closed.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _path(self, manager, identifier):
        # A route parameter never becomes a filesystem path.
        name = hashlib.sha256(identifier.encode()).hexdigest() + ".json"
        folder = manager.catalog_path.parent / "revision_reviews"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / name

    def _write(self, manager, identifier, report):
        path = self._path(manager, identifier)
        temporary = path.with_name("." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def report(self, manager, identifier):
        path = self._path(manager, identifier)
        if not path.exists():
            return None
        report = json.loads(path.read_text(encoding="utf-8"))
        # The single-process worker cannot resume a provider call after restart.
        with self._guard:
            job = self._jobs.get(str(path))
            if report["status"] == "running" and (job is None or job.done()):
                report = {**report, "status": "failed", "error": "Review was interrupted. Run it again before publishing."}
        return report

    def detail(self, manager, identifier):
        with manager.lock():
            draft = find_spec(manager, identifier)
            if not draft.replaces_id:
                raise HTTPException(404, "Revision not found.")
            base = find_spec(manager, draft.replaces_id)
            old = manager.store.get(where={"document_id": base.document_id}, include=["documents", "metadatas"])
            new = manager.store.get(where={"document_id": draft.document_id}, include=["documents", "metadatas"])
            def ordered_text(records):
                pairs = zip(records["metadatas"], records["documents"])
                return [text for meta, text in sorted(pairs, key=lambda pair: (pair[0].get("page", 0), pair[0].get("start_index", 0)))]
            changes = list(difflib.unified_diff("\n".join(ordered_text(old)).splitlines(), "\n".join(ordered_text(new)).splitlines(), fromfile="Current " + base.version, tofile="Proposed " + draft.version, lineterm=""))
            return {"base": summary(base), "revision": summary(draft), "review": self.report(manager, identifier),
                    "text_diff": "\n".join(changes[:300]), "diff_truncated": len(changes) > 300,
                    "suggested_questions": suggestions(base)}

    def start(self, manager, identifier, questions, employee, client):
        # Consistent lock order: catalog lock before worker-state lock.
        with manager.lock(), self._guard:
            if self._closed.is_set():
                raise HTTPException(503, "The review worker is shutting down.")
            self._jobs = {key: job for key, job in self._jobs.items() if not job.done()}
            if self._jobs:
                raise HTTPException(409, "Another policy review is running. Wait for it to finish.")
            base, draft = valid_pair(manager, identifier)
            report = {"review_id": str(uuid.uuid4()), "revision_id": identifier,
                      "status": "running", "questions": questions, "results": [],
                      "requested_by": employee.user_id, "started_at": pub.now_epoch(),
                      "base_signature": signature(manager, base), "draft_signature": signature(manager, draft)}
            self._write(manager, identifier, report)
            key = str(self._path(manager, identifier))
            self._jobs[key] = self._executor.submit(self._run, manager, base, draft, report, client)
            return {"review_id": report["review_id"], "status": "running"}

    def _evidence(self, manager, spec, question):
        docs = manager.store.similarity_search(question, k=5, filter={"document_id": spec.document_id})
        # This HR-only path deliberately reads a draft; it still checks identity
        # and audience on every returned record before sending it to the LLM.
        if not docs or any(doc.metadata.get("document_id") != spec.document_id
                           or doc.metadata.get("visibility") != spec.visibility
                           or doc.metadata.get("owner_id") != spec.owner_id
                           or doc.metadata.get("upload_status") == "staging" for doc in docs):
            raise ValueError("Version evidence was missing or did not match its audience.")
        return docs

    def _run(self, manager, base, draft, report, client):
        try:
            for question in report["questions"]:
                if self._closed.is_set():
                    raise RuntimeError("Review stopped")
                result = self.comparison(question, self._evidence(manager, base, question), self._evidence(manager, draft, question), client)
                report["results"].append(result)
                self._write(manager, draft.document_id, report)
            with manager.lock():
                current, candidate = valid_pair(manager, draft.document_id)
                if signature(manager, current) != report["base_signature"] or signature(manager, candidate) != report["draft_signature"]:
                    raise ValueError("Version changed during review")
                report["status"] = "complete"
                report["finished_at"] = pub.now_epoch()
                self._write(manager, draft.document_id, report)
        except Exception as error:
            report["status"] = "failed"
            report["error"] = ("AI provider rate limit reached. Wait and run the review again." if isinstance(error, RateLimitError)
                               else "The review could not finish, or a policy version changed. Check the documents and provider connection, then run it again.")
            self._write(manager, draft.document_id, report)

    def publish(self, manager, identifier, payload, employee):
        if not payload.acknowledge_review:
            raise HTTPException(422, "Confirm that you reviewed the answers and their evidence.")
        now = pub.now_epoch()
        effective = int(payload.effective_at.timestamp()) if payload.effective_at else now
        if effective < now or effective > now + int(timedelta(days=365).total_seconds()):
            raise HTTPException(422, "Choose Publish now or an effective time within the next year.")
        with manager.lock():
            base, draft = valid_pair(manager, identifier)
            report = self.report(manager, identifier)
            if not report or report["status"] != "complete" or report["review_id"] != str(payload.review_id):
                raise HTTPException(409, "Complete and review the latest impact report before publishing.")
            if signature(manager, base) != report["base_signature"] or signature(manager, draft) != report["draft_signature"]:
                raise HTTPException(409, "A policy version changed after this report. Run a fresh review.")
            specs = load_catalog(manager.catalog_path)
            old = base.model_copy(update={"effective_until": effective})
            new = draft.model_copy(update={"lifecycle": "published", "effective_from": effective,
                                           "published_by": employee.user_id, "published_at": now})
            snapshots = [manager.store.get(where={"document_id": item.document_id}, include=["metadatas"]) for item in (base, draft)]
            saved = False
            try:
                for records in snapshots:
                    manager.store._collection.update(ids=records["ids"], metadatas=[{**meta, "upload_status": "staging"} for meta in records["metadatas"]])
                manager._save([old if spec.document_id == old.document_id else new if spec.document_id == new.document_id else spec for spec in specs])
                saved = True
                for spec, records in zip((old, new), snapshots):
                    manager.store._collection.update(ids=records["ids"], metadatas=[{**meta, **pub.metadata_for(spec), "upload_status": "published"} for meta in records["metadatas"]])
            except Exception:
                restored = True
                if saved:
                    try:
                        manager._save(specs)
                    except Exception:
                        restored = False
                for records in snapshots:
                    try:
                        # Explicit legacy defaults also clear fields added by a partial update.
                        manager.store._collection.update(ids=records["ids"], metadatas=[{
                            **meta, "publication_control": meta.get("publication_control", "legacy"),
                            "lifecycle": meta.get("lifecycle", "published"),
                            "effective_from": meta.get("effective_from", 0),
                            "effective_until": meta.get("effective_until", pub.END_OF_TIME),
                            "upload_status": meta.get("upload_status", "published") if restored else "staging",
                        } for meta in records["metadatas"]])
                    except Exception:
                        restored = False
                if not restored:
                    # A failed rollback must not knowingly leave two active versions.
                    for records in snapshots:
                        try:
                            manager.store._collection.update(ids=records["ids"], metadatas=[{"upload_status": "staging"} for _ in records["ids"]])
                        except Exception:
                            pass  # Storage is unavailable; operator reconciliation is required.
                raise HTTPException(503, "Publication did not finish. Check both versions before retrying; a fresh review may be required.") from None
            return {"revision": summary(new), "previous": summary(old)}


@router.get("/documents/{document_id}/revisions")
def list_revisions(document_id: str, request: Request, employee=Depends(require_hr)):
    manager = request.app.state.document_manager
    with manager.lock():
        base = find_spec(manager, document_id)
        return {"base": summary(base), "revisions": [summary(spec) for spec in load_catalog(manager.catalog_path) if spec.replaces_id == document_id],
                "suggested_questions": suggestions(base)}


@router.post("/documents/{document_id}/revisions", status_code=201)
def upload_revision(document_id: str, request: Request, metadata: str = Form(max_length=4096),
                    file: UploadFile = File(), employee=Depends(require_hr)):
    manager = request.app.state.document_manager
    try:
        details = RevisionDetails.model_validate_json(metadata)
        base = find_spec(manager, document_id)
        inherited = UploadDetails(title=base.title, category=base.category, version=details.version,
                                  visibility=base.visibility, owner_id=base.owner_id)
        return manager.upload(file, inherited, revision_of=base, actor_id=employee.user_id)
    except ValidationError:
        raise HTTPException(422, "Enter a new version label. A revision inherits its original audience.") from None
    finally:
        file.file.close()


@router.get("/revisions/{revision_id}")
def revision_detail(revision_id: str, request: Request, employee=Depends(require_hr)):
    return request.app.state.revisions.detail(request.app.state.document_manager, revision_id)


@router.post("/revisions/{revision_id}/review", status_code=202)
def start_review(revision_id: str, payload: ReviewRequest, request: Request, employee=Depends(require_hr)):
    return request.app.state.revisions.start(request.app.state.document_manager, revision_id, payload.questions, employee, request.app.state.llm_client)


@router.post("/revisions/{revision_id}/publish")
def publish_revision(revision_id: str, payload: PublishRequest, request: Request, employee=Depends(require_hr)):
    return request.app.state.revisions.publish(request.app.state.document_manager, revision_id, payload, employee)
