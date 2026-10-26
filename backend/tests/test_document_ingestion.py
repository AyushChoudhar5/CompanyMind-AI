import json

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app.rag.ingest import DocumentSpec, load_catalog, load_registered
from langchain_text_splitters import RecursiveCharacterTextSplitter


def spec(**changes):
    return DocumentSpec(**{"document_id": "policy", "path": "policy.txt", "title": "Policy", "category": "hr", "version": "1", "visibility": "company", **changes})


def test_private_document_requires_owner():
    with pytest.raises(ValueError):
        spec(visibility="employee")


def test_shared_document_rejects_owner():
    with pytest.raises(ValueError):
        spec(owner_id="EMP001")


def test_paths_cannot_escape_data_directory(tmp_path):
    with pytest.raises(ValueError, match="inside"):
        spec(path="../outside.txt").resolved_path(tmp_path)


def test_empty_document_rejected(tmp_path):
    (tmp_path / "policy.txt").write_text("   ", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_registered(spec(), tmp_path)


def test_duplicate_ids_rejected(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([spec().model_dump(), spec().model_dump()]))
    with pytest.raises(ValueError, match="unique"):
        load_catalog(catalog)


def test_txt_chunks_retain_private_metadata(tmp_path):
    (tmp_path / "policy.txt").write_text("Private term. " * 100, encoding="utf-8")
    pages = load_registered(spec(visibility="employee", owner_id="EMP001"), tmp_path)
    chunks = RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=20).split_documents(pages)
    assert len(chunks) > 1
    assert all(chunk.metadata["owner_id"] == "EMP001" for chunk in chunks)
    assert all("page" not in chunk.metadata for chunk in chunks)


def test_image_only_pdf_is_rejected(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(tmp_path / "policy.pdf")
    with pytest.raises(ValueError, match="OCR"):
        load_registered(spec(path="policy.pdf"), tmp_path)


def test_pdf_preserves_real_page_numbers(tmp_path):
    writer = PdfWriter()
    for text in ["First page policy", "Second page terms"]:
        page = writer.add_blank_page(width=300, height=300)
        font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 250 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(tmp_path / "policy.pdf")
    pages = load_registered(spec(path="policy.pdf"), tmp_path)
    assert [page.metadata["page"] for page in pages] == [1, 2]
    assert "Second page terms" in pages[1].page_content
