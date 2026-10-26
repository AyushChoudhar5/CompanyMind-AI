from collections import defaultdict

from langchain_chroma import Chroma

from backend.app.core.access import can_read, document_filter
from backend.app.rag.embeddings import get_embedding_model

from backend.app.core.paths import CHROMA_DIR

DATABASE_DIR = CHROMA_DIR
# A new collection deliberately excludes unclassified records in the old demo.
COLLECTION_NAME = "company_documents_v2"


def get_vector_store():
    return Chroma(collection_name=COLLECTION_NAME, embedding_function=get_embedding_model(),
                  persist_directory=str(DATABASE_DIR))


def index_catalog(store, chunks):
    grouped = defaultdict(list)
    for chunk in chunks:
        grouped[chunk.metadata["document_id"]].append(chunk)
    for document_id, documents in grouped.items():
        # Delete first: failed replacement must not keep an obsolete private ACL.
        # This is synchronous and not atomic; production needs staged index versions.
        old_ids = store.get(where={"document_id": document_id}, include=[])["ids"]
        if old_ids:
            store.delete(ids=old_ids)
        store.add_documents(documents, ids=[f"{document_id}:{i}" for i in range(len(documents))])
    return sum(len(documents) for documents in grouped.values())


def accessible_documents(store, employee):
    records = store.get(where=document_filter(employee), include=["metadatas"])
    documents = {}
    for metadata in records["metadatas"]:
        if not can_read(metadata, employee):
            raise RuntimeError("Document list contained an unauthorized record.")
        doc_id = metadata["document_id"]
        documents[doc_id] = {key: metadata[key] for key in
            ("document_id", "source", "title", "category", "version", "visibility")}
    return sorted(documents.values(), key=lambda doc: doc["title"])


if __name__ == "__main__":
    from backend.cli.manage import index_documents
    index_documents()
