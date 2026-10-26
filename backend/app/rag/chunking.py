from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.app.rag.ingest import load_catalog, load_registered


def create_chunks(specs=None):
    """Split each page independently, retaining ownership and source metadata."""
    pages = [page for spec in (load_catalog() if specs is None else specs) for page in load_registered(spec)]
    return split_pages(pages)


def split_pages(pages):
    """Used by catalog indexing and HR uploads, preserving all source metadata."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=300, chunk_overlap=50, length_function=len, add_start_index=True,
    )
    return splitter.split_documents(pages)


if __name__ == "__main__":
    chunks = create_chunks()
    print(f"Created {len(chunks)} chunks. Text omitted because documents may be private.")
    for chunk in chunks:
        print(chunk.metadata)
