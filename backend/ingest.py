import os
import re
import io
import base64
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

# Point ONNX model to bundled copy in repo — no network download needed at runtime
_ONNX_PATH = Path(__file__).parent / "onnx_models" / "all-MiniLM-L6-v2"
try:
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = _ONNX_PATH
except Exception:
    pass

_DEFAULT_CHROMA_PATH = str(Path(__file__).parent / "chroma_db")
CHROMA_PERSIST_PATH = os.environ.get("CHROMA_PERSIST_PATH", _DEFAULT_CHROMA_PATH)
DOCS_DIR = Path(__file__).parent / "docs"
COLLECTION_NAME = "select_equip_kb"
CHUNK_SIZE = 500      # tokens (approx words)
CHUNK_OVERLAP = 50
MIN_IMAGE_BYTES = 5 * 1024  # 5KB — skip logos / decorative elements

# Files to SKIP for image extraction (manuals, Lenze/B&R drive docs)
_SKIP_IMAGE_EXTRACTION = {
    "User Trave 340.pdf",
    "User Trave 590.pdf",
    "Commissioning_8400_HighLine_frequency_inverter_EN.pdf",
    "Commissioning_i700_cabinet_servo_inverter__EN.pdf",
    "Inbetriebnahme_Frequenzumrichter 8400 HighLine_EN.pdf",
    "Operating_instructions_i550_cabinet_frequency_inverter_Frequenzumrichter_i550_cabinet_EN.pdf",
    "9400Faults (1).pdf",
    "i700Faults (1).pdf",
}

IMAGE_VISION_PROMPT = (
    "You are a field service assistant for G. Mondini tray sealer machines. "
    "Describe this image in detail — focus on any visible faults, damage, "
    "components, connections, or conditions relevant to a field technician "
    "diagnosing a problem. Be specific and technical. If the image shows a "
    "machine part, name it if you can identify it."
)


def _is_service_report(filename: str) -> bool:
    """Return True if this PDF is a service report (i.e. not a manual/drive doc)."""
    return filename not in _SKIP_IMAGE_EXTRACTION


def _get_vision_client():
    import anthropic
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def _describe_image(vision_client, png_bytes: bytes) -> str:
    """Send a PNG image to Claude vision and return a text description."""
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    message = vision_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                },
                {
                    "type": "text",
                    "text": IMAGE_VISION_PROMPT,
                },
            ],
        }],
    )
    return message.content[0].text


def extract_images_from_pdf(path: Path) -> list[tuple[bytes, int]]:
    """Extract embedded images from a PDF using PyMuPDF (fitz).
    Returns list of (png_bytes, page_number) for images >= MIN_IMAGE_BYTES.
    """
    import fitz
    doc = fitz.open(str(path))
    images = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            try:
                base_image = doc.extract_image(xref)
                raw_bytes = base_image["image"]
                if len(raw_bytes) < MIN_IMAGE_BYTES:
                    continue
                # Convert to PNG if not already
                ext = base_image.get("ext", "png")
                if ext.lower() != "png":
                    from PIL import Image
                    pil_img = Image.open(io.BytesIO(raw_bytes))
                    buf = io.BytesIO()
                    pil_img.save(buf, format="PNG")
                    raw_bytes = buf.getvalue()
                images.append((raw_bytes, page_num + 1))  # 1-indexed pages
            except Exception:
                continue
    doc.close()
    return images


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

    ef = embedding_functions.DefaultEmbeddingFunction()
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
    total_images = 0
    chunk_id = 0
    vision_client = None  # lazy-init only if needed

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
                # --- Text extraction (pypdf) ---
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

                # --- Image extraction (fitz + Claude vision) ---
                if _is_service_report(source):
                    try:
                        img_list = extract_images_from_pdf(file_path)
                        if img_list:
                            if vision_client is None:
                                vision_client = _get_vision_client()
                            # Track images per page for numbering
                            page_img_counter = {}
                            for png_bytes, page_num in img_list:
                                page_img_counter[page_num] = page_img_counter.get(page_num, 0) + 1
                                img_n = page_img_counter[page_num]
                                try:
                                    desc = _describe_image(vision_client, png_bytes)
                                    chunk_text_str = f"\U0001f4f7 Image (Page {page_num}, Photo {img_n}): {desc}"
                                    img_chunk_index = f"img_{page_num}_{img_n}"
                                    ids.append(f"chunk_{chunk_id}")
                                    documents.append(chunk_text_str)
                                    metadatas.append({
                                        "source_file": source,
                                        "page_number": page_num,
                                        "chunk_index": img_chunk_index,
                                        "doc_type": "image_description",
                                    })
                                    chunk_id += 1
                                    total_chunks += 1
                                    total_images += 1
                                    if len(documents) >= BATCH_SIZE:
                                        flush_batch()
                                    print(f"    📷 Described image: {source} p.{page_num} #{img_n}")
                                except Exception as img_err:
                                    print(f"    ⚠️ Vision API error for {source} p.{page_num} #{img_n}: {img_err}")
                    except Exception as extract_err:
                        print(f"    ⚠️ Image extraction error for {source}: {extract_err}")

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
    print(f"Images described:    {total_images}")


if __name__ == "__main__":
    print(f"ChromaDB path: {CHROMA_PERSIST_PATH}")
    print(f"Docs directory: {DOCS_DIR}")
    ingest()
