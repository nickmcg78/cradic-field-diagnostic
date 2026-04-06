import os
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_DEFAULT_CHROMA_PATH = str(Path(__file__).parent / "chroma_db")
CHROMA_PERSIST_PATH = os.environ.get("CHROMA_PERSIST_PATH", _DEFAULT_CHROMA_PATH)
DOCS_DIR = Path(__file__).parent / "docs"
COLLECTION_NAME = "select_equip_kb"
CHUNK_SIZE = 500      # tokens (approx words)
CHUNK_OVERLAP = 50


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by approximate token count (words)."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        if end >= len(words):
            break
        start = end - overlap
    return chunks


def read_pdf(path: Path) -> list[tuple[str, int]]:
    """Returns list of (page_text, page_number) tuples."""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append((text, i))
    return pages


def read_md(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def read_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def ingest():
    import chromadb
    from chromadb.utils import embedding_functions

    client = chromadb.PersistentClient(path=CHROMA_PERSIST_PATH)

    # Skip if collection already has data (e.g. on restart with persistent disk)
    try:
        existing = client.get_collection(COLLECTION_NAME)
        count = existing.count()
        if count > 0:
            print(f"Collection '{COLLECTION_NAME}' already has {count} chunks — skipping ingest.")
            return
    except Exception:
        pass  # Collection doesn't exist yet — proceed with ingest

    # Delete existing collection so we get a clean ingest
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing collection '{COLLECTION_NAME}'")
    except Exception:
        pass

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    all_files = list(DOCS_DIR.iterdir())

    # Sort so KNOWLEDGE_BASE_CONTEXT.md is first
    def sort_key(p: Path):
        if p.name == "KNOWLEDGE_BASE_CONTEXT.md":
            return (0, p.name)
        return (1, p.name)

    all_files.sort(key=sort_key)

    total_docs = 0
    total_chunks = 0
    chunk_id = 0

    ids = []
    documents = []
    metadatas = []
    BATCH_SIZE = 100

    def flush_batch():
        nonlocal ids, documents, metadatas
        if documents:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            ids, documents, metadatas = [], [], []

    for file_path in all_files:
        suffix = file_path.suffix.lower()
        source = file_path.name

        try:
            if suffix == ".md":
                text = read_md(file_path)
                chunks = chunk_text(text)
                for i, chunk in enumerate(chunks):
                    ids.append(f"chunk_{chunk_id}")
                    documents.append(chunk)
                    metadatas.append({"source_file": source, "chunk_index": i})
                    chunk_id += 1
                    total_chunks += 1
                    if len(documents) >= BATCH_SIZE:
                        flush_batch()
                total_docs += 1

            elif suffix == ".pdf":
                pages = read_pdf(file_path)
                for page_text, page_num in pages:
                    chunks = chunk_text(page_text)
                    for i, chunk in enumerate(chunks):
                        ids.append(f"chunk_{chunk_id}")
                        documents.append(chunk)
                        metadatas.append({
                            "source_file": source,
                            "page_number": page_num,
                            "chunk_index": i,
                        })
                        chunk_id += 1
                        total_chunks += 1
                        if len(documents) >= BATCH_SIZE:
                            flush_batch()
                total_docs += 1

            elif suffix == ".docx":
                text = read_docx(file_path)
                chunks = chunk_text(text)
                for i, chunk in enumerate(chunks):
                    ids.append(f"chunk_{chunk_id}")
                    documents.append(chunk)
                    metadatas.append({"source_file": source, "chunk_index": i})
                    chunk_id += 1
                    total_chunks += 1
                    if len(documents) >= BATCH_SIZE:
                        flush_batch()
                total_docs += 1

            else:
                print(f"  Skipping unsupported file type: {source}")
                continue

            print(f"  Ingested: {source}")

        except Exception as e:
            print(f"  ERROR ingesting {source}: {e}")

    flush_batch()

    print(f"\n=== Ingestion complete ===")
    print(f"Documents processed: {total_docs}")
    print(f"Total chunks stored: {total_chunks}")


if __name__ == "__main__":
    print(f"ChromaDB path: {CHROMA_PERSIST_PATH}")
    print(f"Docs directory: {DOCS_DIR}")
    ingest()
