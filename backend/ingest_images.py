"""
Incremental image ingestion script.

Opens the existing ChromaDB collection (does NOT delete or rebuild it),
scans service report PDFs for embedded images, and adds vision-described
chunks for any PDF that doesn't already have image_description entries.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

# Re-use shared config and helpers from ingest.py
from ingest import (
    CHROMA_PERSIST_PATH,
    COLLECTION_NAME,
    DOCS_DIR,
    _SKIP_IMAGE_EXTRACTION,
    _is_service_report,
    _get_vision_client,
    _describe_image,
    extract_images_from_pdf,
)

# Point ONNX model to bundled copy (same as ingest.py)
_ONNX_PATH = Path(__file__).parent / "onnx_models" / "all-MiniLM-L6-v2"
try:
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = _ONNX_PATH
except Exception:
    pass

BATCH_SIZE = 100


def _has_image_chunks(collection, source_file: str) -> bool:
    """Check if image_description chunks already exist for this file."""
    results = collection.get(
        where={"$and": [
            {"source_file": {"$eq": source_file}},
            {"doc_type": {"$eq": "image_description"}},
        ]},
        limit=1,
    )
    return len(results["ids"]) > 0


def ingest_images():
    import chromadb
    from chromadb.utils import embedding_functions

    client = chromadb.PersistentClient(path=CHROMA_PERSIST_PATH)
    ef = embedding_functions.DefaultEmbeddingFunction()

    try:
        collection = client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
        )
    except Exception:
        print(f"ERROR: Collection '{COLLECTION_NAME}' not found. Run ingest.py first.")
        return

    existing_count = collection.count()
    print(f"Collection '{COLLECTION_NAME}' has {existing_count} existing chunks.")

    # Get the next chunk_id by finding the max existing id
    # Chunk ids are formatted as "chunk_{n}" — find the highest n
    all_ids = collection.get(include=[])["ids"]
    max_id = 0
    for cid in all_ids:
        if cid.startswith("chunk_"):
            try:
                n = int(cid.split("_", 1)[1])
                if n > max_id:
                    max_id = n
            except ValueError:
                pass
    chunk_id = max_id + 1

    # Gather service report PDFs
    pdf_files = sorted(
        f for f in DOCS_DIR.iterdir()
        if f.suffix.lower() == ".pdf" and _is_service_report(f.name)
    )

    pdfs_checked = 0
    pdfs_skipped = 0
    pdfs_processed = 0
    new_image_chunks = 0
    vision_client = None

    ids = []
    documents = []
    metadatas = []

    def flush_batch():
        nonlocal ids, documents, metadatas
        if documents:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            ids, documents, metadatas = [], [], []

    for file_path in pdf_files:
        source = file_path.name
        pdfs_checked += 1

        # Check if image chunks already exist for this file
        if _has_image_chunks(collection, source):
            print(f"  ⏭️  Already done: {source}")
            pdfs_skipped += 1
            continue

        # Extract images
        try:
            img_list = extract_images_from_pdf(file_path)
        except Exception as e:
            print(f"  ⚠️  Image extraction error for {source}: {e}")
            continue

        if not img_list:
            print(f"  ⬚  No images: {source}")
            pdfs_checked  # still counted as checked
            continue

        # Lazy-init vision client
        if vision_client is None:
            vision_client = _get_vision_client()

        page_img_counter = {}
        file_image_count = 0

        for png_bytes, page_num in img_list:
            page_img_counter[page_num] = page_img_counter.get(page_num, 0) + 1
            img_n = page_img_counter[page_num]

            try:
                desc = _describe_image(vision_client, png_bytes)
                chunk_text = f"\U0001f4f7 Image (Page {page_num}, Photo {img_n}): {desc}"
                img_chunk_index = f"img_{page_num}_{img_n}"

                ids.append(f"chunk_{chunk_id}")
                documents.append(chunk_text)
                metadatas.append({
                    "source_file": source,
                    "page_number": page_num,
                    "chunk_index": img_chunk_index,
                    "doc_type": "image_description",
                })
                chunk_id += 1
                new_image_chunks += 1
                file_image_count += 1

                if len(documents) >= BATCH_SIZE:
                    flush_batch()

                print(f"    \U0001f4f7 Described: {source} p.{page_num} #{img_n}")

            except Exception as img_err:
                print(f"    ⚠️  Vision API error for {source} p.{page_num} #{img_n}: {img_err}")

        if file_image_count > 0:
            pdfs_processed += 1
            print(f"  ✅ {source}: {file_image_count} images added")

    # Flush any remaining
    flush_batch()

    print(f"\n=== Image ingestion complete ===")
    print(f"PDFs checked:       {pdfs_checked}")
    print(f"PDFs skipped:       {pdfs_skipped} (already done)")
    print(f"PDFs processed:     {pdfs_processed}")
    print(f"New image chunks:   {new_image_chunks}")
    print(f"Total collection:   {collection.count()} chunks")


if __name__ == "__main__":
    print(f"ChromaDB path: {CHROMA_PERSIST_PATH}")
    print(f"Docs directory: {DOCS_DIR}")
    print()
    ingest_images()
