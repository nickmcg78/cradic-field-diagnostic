import os
import re
import io
import sys
import base64
import hashlib
from pathlib import Path
from dotenv import load_dotenv

from model_config import VISION_MODEL

# Ensure stdout handles Unicode on all platforms (avoids charmap errors on Windows)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

# Pinecone config
PINECONE_INDEX_NAME = "cradic-field-diagnostic"
PINECONE_REGION = "us-east-1"
PINECONE_CLOUD = "aws"
CHUNK_SIZE = 500      # tokens (approx words)
CHUNK_OVERLAP = 50
MIN_IMAGE_BYTES = 15 * 1024  # 15KB — skip logos / decorative elements

# Files to SKIP for image extraction (manuals, Lenze/B&R drive docs)
_SKIP_IMAGE_EXTRACTION = {
    "User Trave 340.pdf",
    "User Trave 350.pdf",
    "User Trave 367.pdf",
    "User Trave 590.pdf",
    "User Trave 1000.pdf",
    "User Trave 1200.pdf",
    "User Trave 1400.pdf",
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


# -----------------------------------------------------------------------------
# Document classification — drives the per-chunk metadata so the frontend
# machine selector can strictly scope retrieval. The eventual query filter is:
#   machine_model == selected OR drive_platform == platform OR doc_class == "context"
# -----------------------------------------------------------------------------

# Mondini Trave manuals: filename -> (machine_model, drive_platform).
# Drive platform was determined from control-system markers inside each manual:
# B&R machines carry ACOPOS + X20 servo/IO markers; Lenze machines do not and
# instead reference a LENZE PLC. (340/350/367 = B&R; 590/1000/1200/1400 = Lenze.)
MANUAL_SPECS = {
    "User Trave 340.pdf":  ("Trave 340",  "B&R"),
    "User Trave 350.pdf":  ("Trave 350",  "B&R"),
    "User Trave 367.pdf":  ("Trave 367",  "B&R"),
    "User Trave 590.pdf":  ("Trave 590",  "Lenze"),
    "User Trave 1000.pdf": ("Trave 1000", "Lenze"),
    "User Trave 1200.pdf": ("Trave 1200", "Lenze"),
    "User Trave 1400.pdf": ("Trave 1400", "Lenze"),
}

# Lenze / B&R drive-system documentation: filename -> drive_platform.
DRIVE_DOC_SPECS = {
    "Commissioning_8400_HighLine_frequency_inverter_EN.pdf": "Lenze",
    "Commissioning_i700_cabinet_servo_inverter__EN.pdf": "Lenze",
    "Inbetriebnahme_Frequenzumrichter 8400 HighLine_EN.pdf": "Lenze",
    "Operating_instructions_i550_cabinet_frequency_inverter_Frequenzumrichter_i550_cabinet_EN.pdf": "Lenze",
    "9400Faults (1).pdf": "Lenze",
    "i700Faults (1).pdf": "Lenze",
}

# Any file whose name starts with this stem is the always-retrievable KB context
# doc (handles both "KNOWLEDGE_BASE_CONTEXT.md" and "KNOWLEDGE_BASE_CONTEXT (1).md").
_CONTEXT_PREFIX = "KNOWLEDGE_BASE_CONTEXT"

# Matches "Trave 1000" style model numbers (3-4 digits) in a report header.
_MODEL_RE = re.compile(r"Trave\s*(\d{3,4})", re.IGNORECASE)

# Known machine models — used as a conservative whitelist for the lowest-
# confidence first-page fallback (avoids grabbing stray 3-4 digit numbers).
_KNOWN_MODELS = {model for model, _ in MANUAL_SPECS.values()}


def _find_models(text: str) -> list[str]:
    """Return ordered, de-duplicated 'Trave NNNN' models found in text."""
    models = []
    for m in _MODEL_RE.finditer(text):
        model = f"Trave {int(m.group(1))}"
        if model not in models:
            models.append(model)
    return models


def _subject_line(text: str) -> str:
    """Return the text of the report's 'Subject:' line (up to end of line)."""
    m = re.search(r"Subject\s*:(.*)", text, re.IGNORECASE)
    if not m:
        return ""
    return m.group(1).splitlines()[0] if m.group(1) else ""


def parse_machine_models_traced(header_text: str, first_page_text: str = None):
    """Tiered, precision-first parse of the machines a service report covers.
    Returns (models, source) where source is one of:
      'table'      — found in the 'Equipment Type / Serial Number' table region
      'subject'    — table empty; found on the 'Subject:' line (trusted)
      'first_page' — table+subject empty; conservative first-page scan, models
                     restricted to the known set (lowest confidence)
      'none'       — nothing found
    """
    # Tier 1 — equipment table region (highest confidence).
    start = re.search(r"Equipment\s*Type", header_text, re.IGNORECASE)
    end = re.search(r"Equipment\s*condition", header_text, re.IGNORECASE)
    region = ""
    if start:
        s = start.end()
        e = end.start() if (end and end.start() > s) else len(header_text)
        region = header_text[s:e]
    models = _find_models(region)
    if models:
        return models, "table"

    # Tier 2 — Subject line (trusted source).
    models = _find_models(_subject_line(header_text))
    if models:
        return models, "subject"

    # Tier 3 — conservative first-page scan (lower confidence). Restrict to
    # known models so incidental numbers aren't mistaken for a machine.
    fp = first_page_text if first_page_text is not None else header_text
    models = [m for m in _find_models(fp) if m in _KNOWN_MODELS]
    if models:
        return models, "first_page"

    return [], "none"


def parse_machine_models(header_text: str, first_page_text: str = None) -> list[str]:
    """Parse distinct machine models a service report covers, precision-first:
    equipment table -> Subject line -> conservative first-page scan.
    Returns an ordered, de-duplicated list e.g. ['Trave 1000', 'Trave 1200'].
    """
    return parse_machine_models_traced(header_text, first_page_text)[0]


def classify_document(source: str, header_text: str = "", first_page_text: str = None) -> dict:
    """Return the document-level tags applied to every chunk of this file:
    doc_class, machine_model, machine_models, drive_platform.
    header_text / first_page_text are only consulted for service reports.
    """
    if source.startswith(_CONTEXT_PREFIX):
        return {"doc_class": "context", "machine_model": None,
                "machine_models": None, "drive_platform": None}
    if source in MANUAL_SPECS:
        model, platform = MANUAL_SPECS[source]
        return {"doc_class": "manual", "machine_model": model,
                "machine_models": None, "drive_platform": platform}
    if source in DRIVE_DOC_SPECS:
        return {"doc_class": "drive_doc", "machine_model": None,
                "machine_models": None, "drive_platform": DRIVE_DOC_SPECS[source]}
    models = parse_machine_models(header_text, first_page_text)
    return {"doc_class": "service_report", "machine_model": None,
            "machine_models": models or None, "drive_platform": None}


def make_chunk_id(source: str, page, chunk_index) -> str:
    """Stable per-chunk ID namespaced by source file, so incremental upserts of
    new files never collide with chunks already in the index. (The old global
    'chunk_{n}' counter reset to 0 every run, which on an incremental run would
    have overwritten unrelated existing vectors.)
    """
    return hashlib.md5(f"{source}#p{page}#c{chunk_index}".encode("utf-8")).hexdigest()


def _chroma_safe_meta(meta: dict) -> dict:
    """ChromaDB metadata accepts only str/int/float/bool — no None, no lists.
    Drop None values and join list values (e.g. machine_models) into a
    comma-separated string. (Pinecone keeps the native list for $in filtering.)
    """
    out = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, list):
            if not v:
                continue
            out[k] = ", ".join(map(str, v))
        else:
            out[k] = v
    return out


def _pinecone_source_files(index, dim: int, candidate_names: list[str]) -> set[str]:
    """Return the subset of candidate source filenames that already have at least
    one vector in the index. Pinecone has no 'list distinct metadata' API, so we
    probe each candidate with a metadata-filtered top-1 query.
    """
    probe = [0.0] * dim
    probe[0] = 1.0  # non-zero vector — Pinecone rejects all-zero query vectors
    present = set()
    for name in candidate_names:
        try:
            res = index.query(
                vector=probe,
                top_k=1,
                filter={"source_file": {"$eq": name}},
                include_metadata=False,
            )
            if res.matches:
                present.add(name)
        except Exception:
            pass
    return present


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
        model=VISION_MODEL,
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

    ef = embedding_functions.DefaultEmbeddingFunction()

    force = bool(os.environ.get("FORCE_INGEST"))

    # Discover candidate files first (needed to probe the index for incremental).
    all_files = list(DOCS_DIR.iterdir())

    def sort_key(p: Path):
        if p.name.startswith(_CONTEXT_PREFIX):
            return (0, p.name)
        return (1, p.name)

    all_files.sort(key=sort_key)
    all_source_names = [p.name for p in all_files]

    # -------------------------------------------------------------------------
    # ChromaDB path
    #   FORCE_INGEST=1 -> wipe and rebuild the whole collection
    #   normal run     -> incremental: keep collection, add only new source files
    # -------------------------------------------------------------------------
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_PATH)
    run_chroma = True
    chroma_existing_sources = set()

    if force:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"ChromaDB: FORCE_INGEST set — deleted existing collection '{COLLECTION_NAME}'")
        except Exception:
            pass

    chroma_collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    if not force:
        try:
            existing = chroma_collection.get(include=["metadatas"])
            for m in existing.get("metadatas", []) or []:
                sf = (m or {}).get("source_file")
                if sf:
                    chroma_existing_sources.add(sf)
            print(f"ChromaDB: incremental — {len(chroma_existing_sources)} source file(s) already indexed.")
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Pinecone path
    #   FORCE_INGEST=1 -> wipe (delete_all) and rebuild
    #   normal run     -> incremental: add only source files with no existing vectors
    # -------------------------------------------------------------------------
    pinecone_index = None
    run_pinecone = False
    pinecone_existing_sources = set()
    pinecone_api_key = os.environ.get("PINECONE_API_KEY")
    if pinecone_api_key:
        try:
            from pinecone import Pinecone
            pc = Pinecone(api_key=pinecone_api_key)
            pinecone_index = pc.Index(PINECONE_INDEX_NAME)
            stats = pinecone_index.describe_index_stats()
            existing_vectors = stats.total_vector_count
            dim = stats.dimension or 384
            run_pinecone = True
            if force:
                if existing_vectors > 0:
                    print(f"Pinecone: FORCE_INGEST set — deleting {existing_vectors} existing vectors...")
                    pinecone_index.delete(delete_all=True)
                print(f"Pinecone: will rebuild '{PINECONE_INDEX_NAME}' ({PINECONE_CLOUD}/{PINECONE_REGION})")
            else:
                pinecone_existing_sources = _pinecone_source_files(
                    pinecone_index, dim, all_source_names)
                print(f"Pinecone: incremental — {len(pinecone_existing_sources)} source file(s) already indexed.")
        except Exception as e:
            print(f"Pinecone init failed: {e}")

    # -------------------------------------------------------------------------
    # Decide which files to ingest. FORCE wipes both stores, so the existing
    # sets are empty and everything is (re)ingested. On an incremental run a
    # file is processed if it is missing from EITHER active store; upserts make
    # re-writing a file already present in the other store harmless.
    # -------------------------------------------------------------------------
    files_to_ingest = []
    for fp in all_files:
        src = fp.name
        need = (run_chroma and src not in chroma_existing_sources) or \
               (run_pinecone and src not in pinecone_existing_sources)
        if need:
            files_to_ingest.append(fp)

    if not files_to_ingest:
        print("No new files to ingest. Set FORCE_INGEST=1 to wipe and rebuild.")
        return

    print(f"Files to ingest: {len(files_to_ingest)} of {len(all_files)}")

    total_docs = 0
    total_chunks = 0
    total_images = 0
    vision_client = None  # lazy-init only if needed

    ids = []
    documents = []
    metadatas = []
    BATCH_SIZE = 100

    def flush_batch():
        nonlocal ids, documents, metadatas
        if not documents:
            return

        # Compute embeddings once — used by both ChromaDB and Pinecone
        embeddings = ef(documents)

        if run_chroma and chroma_collection is not None:
            # upsert (not add) so re-writing a file already present is idempotent;
            # _chroma_safe_meta drops None and flattens list values (machine_models)
            chroma_collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=[_chroma_safe_meta(m) for m in metadatas],
            )

        if run_pinecone and pinecone_index is not None:
            vectors = []
            for vid, vec, meta, doc in zip(ids, embeddings, metadatas, documents):
                # Pinecone metadata: strip None values, store text for retrieval
                clean_meta = {k: v for k, v in meta.items() if v is not None}
                clean_meta["chunk_index"] = str(clean_meta.get("chunk_index", ""))
                clean_meta["text"] = doc
                vectors.append({"id": vid, "values": vec.tolist(), "metadata": clean_meta})
            # Upsert in sub-batches of 100 (Pinecone limit)
            for i in range(0, len(vectors), 100):
                pinecone_index.upsert(vectors=vectors[i:i + 100])

        ids.clear()
        documents.clear()
        metadatas.clear()

    for file_path in files_to_ingest:
        suffix = file_path.suffix.lower()
        source = file_path.name

        try:
            if suffix == ".md":
                text = read_md(file_path)
                tags = classify_document(source, text[:4000], text[:4000])
                chunks = chunk_text(text)
                for i, chunk in enumerate(chunks):
                    ids.append(make_chunk_id(source, "na", i))
                    documents.append(chunk)
                    metadatas.append({"source_file": source, "chunk_index": i, **tags})
                    total_chunks += 1
                    if len(documents) >= BATCH_SIZE:
                        flush_batch()
                total_docs += 1

            elif suffix == ".pdf":
                # --- Text extraction (pypdf) ---
                pages = read_pdf(file_path)
                # Header (first 2 pages) feeds table/subject parsing; the first
                # page alone feeds the conservative first-page fallback.
                header_text = "\n".join(t for t, _ in pages[:2]) if pages else ""
                first_page_text = pages[0][0] if pages else ""
                tags = classify_document(source, header_text, first_page_text)
                for page_text, page_num in pages:
                    chunks = chunk_text(page_text)
                    for i, chunk in enumerate(chunks):
                        ids.append(make_chunk_id(source, page_num, i))
                        documents.append(chunk)
                        metadatas.append({
                            "source_file": source,
                            "page_number": page_num,
                            "chunk_index": i,
                            **tags,
                        })
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
                            page_img_counter = {}
                            for png_bytes, page_num in img_list:
                                page_img_counter[page_num] = page_img_counter.get(page_num, 0) + 1
                                img_n = page_img_counter[page_num]
                                try:
                                    desc = _describe_image(vision_client, png_bytes)
                                    chunk_text_str = f"\U0001f4f7 Image (Page {page_num}, Photo {img_n}): {desc}"
                                    img_chunk_index = f"img_{page_num}_{img_n}"
                                    ids.append(make_chunk_id(source, page_num, img_chunk_index))
                                    documents.append(chunk_text_str)
                                    metadatas.append({
                                        "source_file": source,
                                        "page_number": page_num,
                                        "chunk_index": img_chunk_index,
                                        "doc_type": "image_description",
                                        **tags,
                                    })
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
                tags = classify_document(source, text[:4000], text[:4000])
                chunks = chunk_text(text)
                for i, chunk in enumerate(chunks):
                    ids.append(make_chunk_id(source, "na", i))
                    documents.append(chunk)
                    metadatas.append({"source_file": source, "chunk_index": i, **tags})
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
