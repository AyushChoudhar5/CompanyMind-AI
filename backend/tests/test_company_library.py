"""Real Chroma with tiny local embeddings; no models, network or private files."""
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import Mock

import pytest
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from backend.app.core.access import Employee, can_read
from backend.app.rag.ingest import DocumentSpec, load_catalog
import backend.cli.library as library


class LocalEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0, 0.0]


@pytest.fixture
def demo(tmp_path):
    data = tmp_path / "data"
    bundled = data / "demo" / "company"
    shutil.copytree(library.MANIFEST_PATH.parent, bundled)
    catalog = tmp_path / "catalog.json"
    private = DocumentSpec(document_id="private-existing", path="uploads/not-read.txt",
        title="Existing private test document", category="offer_letter", version="7",
        visibility="employee", owner_id="EMP002", source_name="original.txt")
    catalog.write_text(json.dumps([private.model_dump(exclude_none=True)]), encoding="utf-8")
    store = Chroma(collection_name="company_library_tests", embedding_function=LocalEmbeddings(),
        persist_directory=str(tmp_path / "chroma"))
    store.add_documents([Document(page_content="Existing private test text", metadata={
        "document_id": private.document_id, "visibility": "employee", "owner_id": "EMP002"
    })], ids=["private-existing:0"])
    return store, data, catalog, bundled / "catalog.json", private


def install(demo):
    store, data, catalog, manifest, _ = demo
    return library.install_company_library(store, data, catalog, manifest)


def test_install_and_rerun_preserve_existing_private_records(demo, monkeypatch):
    store, data, catalog, manifest, private = demo
    original = store.get(ids=["private-existing:0"], include=["documents", "metadatas"])
    registered = library.load_registered
    called = []

    def record(spec, root):
        called.append(spec.document_id)
        return registered(spec, root)

    monkeypatch.setattr(library, "load_registered", record)
    count = len(library.load_library_specs(manifest))
    first = install(demo)
    ids = set(store.get(include=[])["ids"])
    assert first["added"] == count and first["chunks"] > count
    second = install(demo)
    assert second == {"added": 0, "skipped": count, "repaired": 0, "chunks": 0}
    assert set(store.get(include=[])["ids"]) == ids
    assert store.get(ids=["private-existing:0"], include=["documents", "metadatas"]) == original
    assert load_catalog(catalog)[0] == private
    assert len(load_catalog(catalog)) == count + 1
    assert all(identifier.startswith("company-demo-") for identifier in called)
    assert not (data / private.path).exists()  # Its content was never read or reindexed.


def test_conflicting_catalog_id_fails_before_any_index_write(demo, monkeypatch):
    store, _, catalog, manifest, private = demo
    spec = library.load_library_specs(manifest)[0]
    conflict = private.model_copy(update={"document_id": spec.document_id})
    original = json.dumps([conflict.model_dump(exclude_none=True)])
    catalog.write_text(original, encoding="utf-8")
    add = Mock(side_effect=AssertionError("must not write"))
    monkeypatch.setattr(store, "add_documents", add)
    with pytest.raises(ValueError, match="different metadata"):
        install(demo)
    add.assert_not_called()
    assert catalog.read_text(encoding="utf-8") == original


def test_duplicate_registration_under_another_id_is_not_added(demo):
    _, _, catalog, manifest, private = demo
    duplicate = library.load_library_specs(manifest)[0].model_copy(update={"document_id": "another-id"})
    catalog.write_text(json.dumps([private.model_dump(exclude_none=True), duplicate.model_dump(exclude_none=True)]), encoding="utf-8")
    with pytest.raises(ValueError, match="another ID"):
        install(demo)
    assert len(load_catalog(catalog)) == 2


def test_preexisting_chunk_id_cannot_be_overwritten(demo):
    store, _, catalog, manifest, _ = demo
    spec = library.load_library_specs(manifest)[0]
    store.add_documents([Document(page_content="Unrelated test record", metadata={"document_id": "another-document"})], ids=[spec.document_id + ":0"])
    before = store.get(include=["metadatas", "documents"])
    with pytest.raises(ValueError, match="already used"):
        install(demo)
    assert store.get(include=["metadatas", "documents"]) == before
    assert len(load_catalog(catalog)) == 1


def test_unregistered_or_partial_demo_index_requires_reconciliation(demo):
    store, _, catalog, manifest, _ = demo
    spec = library.load_library_specs(manifest)[0]
    store.add_documents([Document(page_content="Orphan test record", metadata={"document_id": spec.document_id})], ids=[spec.document_id + ":orphan"])
    with pytest.raises(ValueError, match="reconciliation"):
        install(demo)
    assert len(load_catalog(catalog)) == 1


def test_explicit_install_can_index_identical_seed_registrations(demo):
    _, _, catalog, manifest, private = demo
    specs = library.load_library_specs(manifest)
    catalog.write_text(json.dumps([item.model_dump(exclude_none=True) for item in [private, *specs]]), encoding="utf-8")
    result = install(demo)
    assert result["added"] == 0 and result["repaired"] == len(specs)
    assert result["chunks"] > 0
    assert len(load_catalog(catalog)) == len(specs) + 1


def test_chunks_stay_hidden_until_catalog_is_saved(demo, monkeypatch):
    store, _, _, _, _ = demo
    save = library._save_catalog

    def inspect(path, specs):
        metadata = store.get(where={"document_id": {"$ne": "private-existing"}}, include=["metadatas"])["metadatas"]
        assert metadata and all(item["upload_status"] == "staging" for item in metadata)
        assert all(not can_read(item, Employee("EMP001", "alice", "employee")) for item in metadata)
        save(path, specs)

    monkeypatch.setattr(library, "_save_catalog", inspect)
    install(demo)
    metadata = store.get(where={"document_id": {"$ne": "private-existing"}}, include=["metadatas"])["metadatas"]
    assert all(item["upload_status"] == "published" for item in metadata)


@pytest.mark.parametrize("failure", ["index", "catalog", "publish"])
def test_failure_rolls_back_only_new_demo_records(demo, monkeypatch, failure):
    store, _, catalog, _, private = demo
    original = store.get(include=["metadatas", "documents"])
    if failure == "index":
        add = store.add_documents

        def partial(*args, **kwargs):
            add(*args, **kwargs)
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(store, "add_documents", partial)
    elif failure == "catalog":
        monkeypatch.setattr(library, "_save_catalog", Mock(side_effect=OSError("simulated failure")))
    else:
        monkeypatch.setattr(type(store._collection), "update", Mock(side_effect=RuntimeError("simulated failure")))
    with pytest.raises(ValueError, match="rolled back"):
        install(demo)
    assert load_catalog(catalog) == [private]
    assert store.get(include=["metadatas", "documents"]) == original


def test_modified_bundled_file_is_rejected_before_changes(demo):
    _, data, catalog, manifest, _ = demo
    first = library.load_library_specs(manifest)[0]
    (data / first.path).write_text("Modified sample", encoding="utf-8")
    with pytest.raises(ValueError, match="differs from its manifest"):
        install(demo)
    assert len(load_catalog(catalog)) == 1


def test_bundled_library_is_shared_and_uses_short_sections():
    specs = library.load_library_specs()
    assert 6 <= len(specs) <= 8
    for spec in specs:
        content = spec.resolved_path().read_text(encoding="utf-8")
        assert "FICTIONAL LEARNING" in content
        assert spec.visibility == "company" and spec.owner_id is None
        assert hashlib.sha256(spec.resolved_path().read_bytes()).hexdigest() == spec.sha256
        assert all(len(section) <= 300 for section in content.strip().split("\n\n"))
