"""Read explicitly registered TXT/PDF documents; never infer access from a filename."""
import json
from pathlib import Path, PureWindowsPath
from typing import Literal
from backend.app.core.publication import END_OF_TIME, metadata_for

from langchain_core.documents import Document
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.core.paths import DATA_DIR, PROJECT_ROOT, RUNTIME_DIR

PROJECT_DIR = PROJECT_ROOT
DOCUMENT_PATH = DATA_DIR / "leave_policy.txt"
SEED_CATALOG_PATH = DATA_DIR / "catalog.json"
CATALOG_PATH = RUNTIME_DIR / "catalog.json"


class DocumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    document_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    path: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    version: str = Field(min_length=1, max_length=30)
    visibility: Literal["company", "employee", "hr"]
    lifecycle: Literal["published", "draft", "archived"] = "published"
    replaces_id: str | None = Field(default=None, max_length=100)
    effective_from: int = Field(default=0, ge=0, le=END_OF_TIME)
    effective_until: int = Field(default=END_OF_TIME, ge=0, le=END_OF_TIME)
    created_by: str | None = None
    created_at: int = 0
    published_by: str | None = None
    published_at: int | None = None
    owner_id: str | None = None
    source_name: str | None = Field(default=None, min_length=1, max_length=250)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_owner(self):
        if self.effective_until < self.effective_from:
            raise ValueError("Policy dates are inconsistent.")
        if self.visibility == "employee" and not (self.owner_id and self.owner_id.strip()):
            raise ValueError("An employee document needs an explicit owner_id.")
        if self.visibility != "employee" and self.owner_id is not None:
            raise ValueError("Only an employee document may have an owner_id.")
        if self.source_name is not None and (PureWindowsPath(self.source_name).name != self.source_name or any(ord(char) < 32 for char in self.source_name)):
            raise ValueError("Source names must be filenames, not paths.")
        return self

    def resolved_path(self, root: Path = DATA_DIR) -> Path:
        path = (root / self.path).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Document paths must stay inside the data directory.")
        if path.suffix.lower() not in {".txt", ".pdf"}:
            raise ValueError("This version supports TXT and text-based PDF files.")
        if not path.is_file():
            raise FileNotFoundError(f"Registered document not found: {self.path}")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Documents must be no larger than 20 MiB.")
        return path


def load_document(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Document not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if "\x00" in text:
        raise ValueError("Binary data is not a supported text document.")
    if not text:
        raise ValueError(f"Document is empty: {path}")
    return text


def initialize_catalog():
    """Copy the fictional seed once; HR metadata stays in the ignored runtime dir."""
    from filelock import FileLock
    import uuid
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(CATALOG_PATH) + ".lock", timeout=10):
        if CATALOG_PATH.exists():
            return
        payload = SEED_CATALOG_PATH.read_text(encoding="utf-8")
        json.loads(payload)
        temporary = CATALOG_PATH.with_name(".catalog-" + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(CATALOG_PATH)
        finally:
            temporary.unlink(missing_ok=True)


def load_catalog(path: Path = CATALOG_PATH) -> list[DocumentSpec]:
    path = Path(path)
    if path == CATALOG_PATH and not path.exists():
        initialize_catalog()
    specs = [DocumentSpec.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    ids = [spec.document_id for spec in specs]
    if len(set(ids)) != len(ids):
        raise ValueError("The catalog must contain unique document IDs.")
    return specs


def load_registered(spec: DocumentSpec, root: Path = DATA_DIR) -> list[Document]:
    path = spec.resolved_path(root)
    metadata = {
        "document_id": spec.document_id, "source": spec.source_name or path.name, "title": spec.title,
        "category": spec.category, "version": spec.version, "visibility": spec.visibility,
    }
    if spec.lifecycle != "published" or spec.replaces_id or spec.effective_from != 0 or spec.effective_until != END_OF_TIME:
        metadata.update(metadata_for(spec))
    if spec.owner_id:
        metadata["owner_id"] = spec.owner_id
    if path.suffix.lower() == ".txt":
        return [Document(page_content=load_document(path), metadata=metadata)]
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError(f"Encrypted PDF is not supported: {path.name}")
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        # Fail rather than silently omit an image-only page from a contract.
        if not text:
            raise ValueError(f"No extractable text on {path.name}, page {number}. OCR or a text-based PDF is required.")
        pages.append(Document(page_content=text, metadata={**metadata, "page": number}))
    if not pages:
        raise ValueError(f"PDF has no readable pages: {path.name}")
    return pages


if __name__ == "__main__":
    for spec in load_catalog():
        pages = load_registered(spec)
        print(f"{spec.document_id}: {len(pages)} text unit(s), {spec.visibility}, version {spec.version}")
