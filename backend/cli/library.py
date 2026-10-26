"""Add the fictional shared knowledge library without reindexing private uploads."""
import hashlib
import json
import logging
from pathlib import Path, PurePosixPath
import uuid

from filelock import FileLock

from backend.app.rag.chunking import split_pages
from backend.app.rag.ingest import CATALOG_PATH, DATA_DIR, DocumentSpec, load_catalog, load_registered

MANIFEST_PATH = DATA_DIR / "demo" / "company" / "catalog.json"
logger = logging.getLogger(__name__)


def load_library_specs(manifest_path=MANIFEST_PATH):
    """Read only the bundled, public demo manifest, never an employee catalog."""
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    specs = [DocumentSpec.model_validate(item) for item in payload]
    if not specs or len({spec.document_id for spec in specs}) != len(specs):
        raise ValueError("The company demo manifest must contain unique document IDs.")
    for spec in specs:
        relative = PurePosixPath(spec.path)
        if (not spec.document_id.startswith("company-demo-") or spec.visibility != "company"
                or spec.owner_id is not None or spec.sha256 is None
                or relative.is_absolute() or ".." in relative.parts
                or relative.parts[:2] != ("demo", "company") or relative.suffix != ".txt"):
            raise ValueError("The company demo manifest may register only its shared TXT documents.")
    return specs


def _save_catalog(path, specs):
    """Publish the complete registry by atomically replacing one file."""
    temporary = path.with_name(".catalog-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps([spec.model_dump(exclude_none=True) for spec in specs], indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _matches_index(records, chunks, expected_ids):
    if set(records["ids"]) != set(expected_ids):
        return False
    by_id = dict(zip(records["ids"], zip(records["metadatas"], records["documents"])))
    for identifier, chunk in zip(expected_ids, chunks):
        metadata, text = by_id[identifier]
        if text != chunk.page_content or metadata.get("upload_status") == "staging":
            return False
        if any(metadata.get(key) != value for key, value in chunk.metadata.items()):
            return False
    return True


def install_company_library(store=None, data_dir=DATA_DIR, catalog_path=CATALOG_PATH, manifest_path=MANIFEST_PATH):
    """Explicit, additive install; existing private and HR-uploaded records stay untouched.

    IDs or document content that conflict with the manifest abort before writes.
    Reruns skip complete entries. An identical registration with no index records
    can be indexed by this explicit command; partial indexes require reconciliation.
    Chroma and JSON are separate stores, so rollback is best effort, not a database
    transaction. New chunks stay hidden until the catalog has been saved.
    """
    root, catalog = Path(data_dir).resolve(), Path(catalog_path).resolve()
    specs = load_library_specs(manifest_path)
    prepared = []
    for spec in specs:
        source = spec.resolved_path(root)
        if hashlib.sha256(source.read_bytes()).hexdigest() != spec.sha256:
            raise ValueError("A bundled demo document differs from its manifest; no documents were changed.")
        chunks = split_pages(load_registered(spec, root))
        if not chunks or len(chunks) > 1000:
            raise ValueError("A bundled demo document contains no text or exceeds the chunk limit.")
        prepared.append((spec, chunks, [f"{spec.document_id}:{index}" for index in range(len(chunks))]))

    load_catalog(catalog)  # Initialize the default registry before taking its lock.
    with FileLock(str(catalog) + ".lock", timeout=10):
        existing = load_catalog(catalog)
        by_id = {spec.document_id: spec for spec in existing}
        for spec in specs:
            old = by_id.get(spec.document_id)
            if old is not None and old.model_dump() != spec.model_dump():
                raise ValueError(f"Demo document ID {spec.document_id} is already registered with different metadata; no documents were changed.")
            if any(old.document_id != spec.document_id and (old.path == spec.path or old.sha256 == spec.sha256) for old in existing):
                raise ValueError("A demo document is already registered under another ID; no documents were changed.")
        if store is None:
            from backend.app.rag.vector_store import get_vector_store
            store = get_vector_store()
        pending, skipped = [], 0
        for spec, chunks, ids in prepared:
            records = store.get(where={"document_id": spec.document_id}, include=["metadatas", "documents"])
            occupied_ids = store.get(ids=ids, include=[])["ids"]
            if set(occupied_ids) - set(records["ids"]):
                raise ValueError("A demo chunk ID is already used by another document; no documents were changed.")
            if records["ids"]:
                if spec.document_id not in by_id or not _matches_index(records, chunks, ids):
                    raise ValueError(f"Demo index {spec.document_id} needs reconciliation; no documents were changed.")
                skipped += 1
            else:
                pending.append((spec, chunks, ids))
        if not pending:
            return {"added": 0, "skipped": skipped, "repaired": 0, "chunks": 0}

        additions = [spec for spec, _, _ in pending if spec.document_id not in by_id]
        attempted_ids = []
        saved = False
        try:
            for _, chunks, ids in pending:
                for chunk in chunks:
                    chunk.metadata["upload_status"] = "staging"
                attempted_ids.extend(ids)  # A failed embedding batch may be only partly written.
                store.add_documents(chunks, ids=ids)
            if additions:
                _save_catalog(catalog, [*existing, *additions])
                saved = True
            for _, chunks, ids in pending:
                store._collection.update(ids=ids, metadatas=[
                    {**chunk.metadata, "upload_status": "published"} for chunk in chunks
                ])
        except Exception:
            rollback_failed = False
            try:
                store.delete(ids=attempted_ids)
            except Exception:
                rollback_failed = True
                # Hide any chunks published before a later batch failed.
                for _, chunks, ids in pending:
                    try:
                        store._collection.update(ids=ids, metadatas=[
                            {**chunk.metadata, "upload_status": "staging"} for chunk in chunks
                        ])
                    except Exception:
                        logger.error("Company demo index rollback needs reconciliation.")
            if saved:
                try:
                    _save_catalog(catalog, existing)
                except Exception:
                    rollback_failed = True
                    logger.error("Company demo catalog rollback needs reconciliation.")
            detail = " Reconciliation is required before retrying." if rollback_failed else " New records were rolled back."
            raise ValueError("The company demo library could not be indexed." + detail) from None
        return {"added": len(additions), "skipped": skipped,
                "repaired": len(pending) - len(additions),
                "chunks": sum(len(chunks) for _, chunks, _ in pending)}
