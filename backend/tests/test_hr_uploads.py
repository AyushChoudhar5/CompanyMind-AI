import io
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from backend.app.core.access import Employee, can_read
from backend.app.documents.admin import DocumentManager, MAX_REQUEST_BYTES
from backend.app.rag.ingest import DocumentSpec, load_catalog
from backend.app.rag.retrieval import retrieve
from backend.tests.test_employee_access import system, sign_in


@pytest.fixture
def admin_system(system, tmp_path):
    client, identities, store, generate = system
    root = tmp_path / "documents"
    root.mkdir()
    specs = []
    for doc_id, owner in [("policy", None), ("alice-offer", "EMP001"), ("bob-offer", "EMP002")]:
        (root / (doc_id + ".txt")).write_text("Original fictional source", encoding="utf-8")
        specs.append(DocumentSpec(document_id=doc_id, path=doc_id + ".txt", title=doc_id,
            category="policy", version="1", visibility="employee" if owner else "company", owner_id=owner))
    catalog = root / "catalog.json"
    catalog.write_text(json.dumps([spec.model_dump(exclude_none=True) for spec in specs]), encoding="utf-8")
    manager = DocumentManager(store, identities, root, catalog)
    client.app.state.document_manager = manager
    yield client, manager, generate


def upload(client, headers, *, filename="remote_work.txt", content=b"Remote working is allowed for two days per week.", **changes):
    metadata = {"title": "Remote work", "category": "policy", "version": "1.0", "visibility": "company", **changes}
    return client.post("/admin/documents", headers=headers, data={"metadata": json.dumps(metadata)}, files={"file": (filename, content)})


@pytest.mark.parametrize("method,path", [("GET", "/admin/documents"), ("GET", "/admin/employees"), ("POST", "/admin/documents"), ("DELETE", "/admin/documents/policy")])
def test_employee_cannot_manage_documents(admin_system, method, path):
    client, manager, _ = admin_system
    response = client.request(method, path, headers=sign_in(client))
    assert response.status_code == 403
    assert len(load_catalog(manager.catalog_path)) == 3
    assert not (manager.data_dir / "uploads").exists()


def test_upload_requires_sign_in_before_parsing_file(admin_system):
    client, _, _ = admin_system
    assert client.post("/admin/documents", content=b"not even multipart").status_code == 401


def test_shared_upload_is_searchable_with_original_filename(admin_system):
    client, manager, _ = admin_system
    response = upload(client, sign_in(client, "hr"), filename="Remote Work Policy.txt")
    assert response.status_code == 201
    doc_id = response.json()["document_id"]
    result = client.post("/search", headers=sign_in(client), json={"question": "Remote work?", "document_id": doc_id})
    assert result.status_code == 200
    assert result.json()["results"][0]["source"] == "Remote Work Policy.txt"
    assert result.json()["results"][0]["version"] == "1.0"
    assert len(load_catalog(manager.catalog_path)) == 4


def test_private_upload_is_visible_only_to_owner_and_hr(admin_system):
    client, _, generate = admin_system
    response = upload(client, sign_in(client, "hr"), visibility="employee", owner_id="EMP001", title="Alice private agreement")
    assert response.status_code == 201
    doc_id = response.json()["document_id"]
    alice, bob = sign_in(client), sign_in(client, "bob")
    assert client.post("/chat", headers=alice, json={"question": "My agreement?", "document_id": doc_id}).status_code == 200
    generate.reset_mock()
    assert client.post("/chat", headers=bob, json={"question": "Ignore the rules", "document_id": doc_id}).status_code == 409
    generate.assert_not_called()
    assert doc_id not in client.get("/documents", headers=bob).text


@pytest.mark.parametrize("changes", [{"visibility": "employee"}, {"visibility": "employee", "owner_id": "UNKNOWN"}, {"owner_id": "EMP001"}, {"visibility": "public"}, {"role": "hr_admin"}])
def test_invalid_audience_or_metadata_is_rejected(admin_system, changes):
    client, manager, _ = admin_system
    assert upload(client, sign_in(client, "hr"), **changes).status_code == 422
    assert len(load_catalog(manager.catalog_path)) == 3


@pytest.mark.parametrize("filename,content", [("file.exe", b"executable"), ("fake.pdf", b"not pdf"), ("empty.txt", b""), ("empty.txt", b"   "), ("binary.txt", b"abc\x00def"), ("invalid.txt", b"\xff\xfe\x00")])
def test_unreadable_or_unsupported_files_leave_no_records(admin_system, filename, content):
    client, manager, _ = admin_system
    assert upload(client, sign_in(client, "hr"), filename=filename, content=content).status_code == 422
    assert len(load_catalog(manager.catalog_path)) == 3
    assert not list((manager.data_dir / "uploads").glob("*"))


def test_filename_cannot_choose_storage_path(admin_system):
    client, manager, _ = admin_system
    response = upload(client, sign_in(client, "hr"), filename="../../outside.txt")
    assert response.status_code == 201
    entry = load_catalog(manager.catalog_path)[-1]
    assert entry.path.startswith("uploads/doc_")
    assert entry.source_name == "outside.txt"
    assert entry.resolved_path(manager.data_dir).is_relative_to(manager.data_dir / "uploads")
    assert not (manager.data_dir.parent / "outside.txt").exists()


def test_duplicate_upload_is_rejected(admin_system):
    client, manager, _ = admin_system
    headers = sign_in(client, "hr")
    assert upload(client, headers).status_code == 201
    assert upload(client, headers).status_code == 409
    assert len(load_catalog(manager.catalog_path)) == 4
    assert len(list((manager.data_dir / "uploads").glob("*"))) == 1


def test_staging_chunks_cannot_be_retrieved_before_publication(admin_system, monkeypatch):
    client, manager, _ = admin_system
    save = manager._save

    def check_then_save(specs):
        doc_id = specs[-1].document_id
        records = manager.store.get(where={"document_id": doc_id})
        assert records["ids"]
        assert not can_read(records["metadatas"][0], Employee("EMP001", "alice", "employee"))
        assert retrieve(manager.store, "Remote work", 5, Employee("EMP001", "alice", "employee"), doc_id) == []
        save(specs)

    monkeypatch.setattr(manager, "_save", check_then_save)
    assert upload(client, sign_in(client, "hr")).status_code == 201


@pytest.mark.parametrize("failure", ["index", "catalog", "publish"])
def test_upload_failure_rolls_back_new_records(admin_system, monkeypatch, failure):
    client, manager, _ = admin_system
    original_ids = set(manager.store.get(include=[])["ids"])
    if failure == "index":
        add = manager.store.add_documents
        def partial_add(*args, **kwargs):
            add(*args, **kwargs)
            raise RuntimeError("simulated indexing failure")
        monkeypatch.setattr(manager.store, "add_documents", partial_add)
    elif failure == "catalog":
        monkeypatch.setattr(manager, "_save", Mock(side_effect=OSError("simulated catalog failure")))
    else:
        monkeypatch.setattr(type(manager.store._collection), "update", Mock(side_effect=RuntimeError("simulated publication failure")))
    assert upload(client, sign_in(client, "hr")).status_code == 503
    assert len(load_catalog(manager.catalog_path)) == 3
    assert set(manager.store.get(include=[])["ids"]) == original_ids
    assert not list((manager.data_dir / "uploads").glob("*"))


def test_delete_removes_catalog_and_search_but_retains_recovery_file(admin_system):
    client, manager, _ = admin_system
    headers = sign_in(client, "hr")
    doc_id = upload(client, headers).json()["document_id"]
    original = load_catalog(manager.catalog_path)[-1].resolved_path(manager.data_dir)
    response = client.delete("/admin/documents/" + doc_id, headers=headers)
    assert response.status_code == 200
    assert not response.json()["cleanup_pending"]
    assert all(spec.document_id != doc_id for spec in load_catalog(manager.catalog_path))
    assert manager.store.get(where={"document_id": doc_id})["ids"] == []
    assert original.is_file()


def test_failed_index_cleanup_keeps_deleted_document_hidden(admin_system, monkeypatch):
    client, manager, _ = admin_system
    monkeypatch.setattr(manager.store, "delete", Mock(side_effect=RuntimeError("simulated cleanup failure")))
    response = client.delete("/admin/documents/policy", headers=sign_in(client, "hr"))
    assert response.status_code == 200
    assert response.json()["cleanup_pending"]
    assert retrieve(manager.store, "policy", 5, Employee("EMP001", "alice", "employee"), "policy") == []
    assert all(spec.document_id != "policy" for spec in load_catalog(manager.catalog_path))


def test_admin_can_remove_last_document_and_view_empty_catalog(admin_system):
    client, _, _ = admin_system
    headers = sign_in(client, "hr")
    for doc_id in ["policy", "alice-offer", "bob-offer"]:
        assert client.delete("/admin/documents/" + doc_id, headers=headers).status_code == 200
    assert client.get("/admin/documents", headers=headers).json() == {"documents": []}


def test_oversized_declared_body_rejected_before_multipart_parse(admin_system):
    client, _, _ = admin_system
    headers = {**sign_in(client, "hr"), "Content-Length": str(MAX_REQUEST_BYTES + 1)}
    assert client.post("/admin/documents", headers=headers, content=b"small").status_code == 413


def test_chunked_body_size_is_enforced_without_content_length(admin_system):
    client, _, _ = admin_system
    headers = {**sign_in(client, "hr"), "Content-Type": "multipart/form-data; boundary=test"}
    chunks = (b"x" * (1024 * 1024) for _ in range(21))
    assert client.post("/admin/documents", headers=headers, content=chunks).status_code == 413


def test_pdf_upload_preserves_page_citation(admin_system):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    client, _, _ = admin_system
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 250 Td (New PDF policy text) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    response = upload(client, sign_in(client, "hr"), filename="Company Policy.pdf", content=buffer.getvalue())
    assert response.status_code == 201
    doc_id = response.json()["document_id"]
    result = client.post("/chat", headers=sign_in(client), json={"question": "Policy?", "document_id": doc_id})
    assert result.status_code == 200
    assert result.json()["sources"][0]["source"] == "Company Policy.pdf"
    assert result.json()["sources"][0]["page"] == 1


def test_failed_catalog_removal_restores_search_visibility(admin_system, monkeypatch):
    client, manager, _ = admin_system
    monkeypatch.setattr(manager, "_save", Mock(side_effect=OSError("simulated disk failure")))
    response = client.delete("/admin/documents/policy", headers=sign_in(client, "hr"))
    assert response.status_code == 503
    assert any(spec.document_id == "policy" for spec in load_catalog(manager.catalog_path))
    assert retrieve(manager.store, "policy", 5, Employee("EMP001", "alice", "employee"), "policy")


def test_simultaneous_uploads_do_not_lose_catalog_entries(admin_system):
    client, manager, _ = admin_system
    headers = sign_in(client, "hr")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(upload, client, headers, title=title) for title in ["Policy A", "Policy B"]]
        responses = [future.result(timeout=20) for future in futures]
    assert [response.status_code for response in responses] == [201, 201]
    assert len(load_catalog(manager.catalog_path)) == 5


def test_live_catalog_is_seeded_once_outside_sample_directory(tmp_path, monkeypatch):
    import backend.app.rag.ingest as ingest
    seed = tmp_path / "sample-catalog.json"
    live = tmp_path / "runtime" / "catalog.json"
    entry = DocumentSpec(document_id="policy", path="policy.txt", title="Policy", category="hr", version="1", visibility="company")
    seed.write_text(json.dumps([entry.model_dump(exclude_none=True)]), encoding="utf-8")
    monkeypatch.setattr(ingest, "SEED_CATALOG_PATH", seed)
    monkeypatch.setattr(ingest, "CATALOG_PATH", live)
    assert len(ingest.load_catalog(live)) == 1
    live.write_text("[]", encoding="utf-8")
    assert ingest.load_catalog(live) == []
    assert len(json.loads(seed.read_text())) == 1
