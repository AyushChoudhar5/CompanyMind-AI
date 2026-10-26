from langchain_huggingface import HuggingFaceEmbeddings

from backend.app.rag.chunking import create_chunks


def get_embedding_model():

    return HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


if __name__ == "__main__":
  
    chunks = create_chunks()

    
    texts = [chunk.page_content for chunk in chunks]

    print("Loading the embedding model...")
    embedding_model = get_embedding_model()

    print("Creating document embeddings...")
    vectors = embedding_model.embed_documents(texts)

    print(f"\nChunks: {len(chunks)}")
    print(f"Vectors: {len(vectors)}")
    print(f"Numbers per vector: {len(vectors[0])}")

    print("\nFirst chunk:")
    print(texts[0])

    print("\nFirst 5 numbers in its vector:")
    print(vectors[0][:5])

    question = "Can I carry unused annual leave into next year?"
    question_vector = embedding_model.embed_query(question)

    print(f"\nQuestion: {question}")
    print(f"Numbers in question vector: {len(question_vector)}")