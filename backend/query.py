import os
from pathlib import Path
import anthropic
import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

from model_config import ANSWER_MODEL

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
COLLECTION_NAME = "select_equip_kb"
PINECONE_INDEX_NAME = "cradic-field-diagnostic"
TOP_K = 15
MAX_CONTEXT_CHUNKS = 20
MAX_IMAGES_PER_FILE = 5
_LOGO_FILTERS = ["uptimeequip", "selectequip logo", "logo and tagline"]

# Machine model -> drive platform. MUST stay in sync with ingest.MANUAL_SPECS:
# 340/350/367 = B&R; 590/1000/1200/1400 = Lenze.
_MACHINE_PLATFORM = {
    "Trave 340": "B&R",
    "Trave 350": "B&R",
    "Trave 367": "B&R",
    "Trave 590": "Lenze",
    "Trave 1000": "Lenze",
    "Trave 1200": "Lenze",
    "Trave 1400": "Lenze",
}


def _machine_filter(machine: str):
    """Build the Pinecone metadata filter scoping retrieval to a selected machine.
    Matches a chunk if ANY of:
      machine_model == machine            (that machine's manual)
      machine in machine_models           (service reports covering it)
      drive_platform == machine's platform (drive docs for its platform)
      doc_class == "context"              (always-retrievable KB context)
    Returns (filter_dict, platform).
    """
    clauses = [
        {"machine_model": {"$eq": machine}},
        {"machine_models": {"$in": [machine]}},
        {"doc_class": {"$eq": "context"}},
    ]
    platform = _MACHINE_PLATFORM.get(machine)
    if platform:
        # Scope the platform clause to drive_doc only. Manuals ALSO carry
        # drive_platform, so a bare drive_platform==platform match would pull in
        # every other same-platform machine's manual (e.g. 1000/1200/1400 leaking
        # into a 590 query). We only want the platform's drive documentation here.
        clauses.append({"$and": [
            {"drive_platform": {"$eq": platform}},
            {"doc_class": {"$eq": "drive_doc"}},
        ]})
    return {"$or": clauses}, platform


def _no_machine_results_message(machine: str) -> str:
    """Returned (instead of an unfiltered fallback) when scoped retrieval finds
    nothing specific to the selected machine."""
    return (
        f"⚠️ No machine-specific information found for **{machine}**.\n\n"
        f"I couldn't find any {machine} manual content, service reports, or drive "
        f"documentation in the knowledge base that matches this question. I'm not "
        f"answering from other machines' sources, since that could be misleading for "
        f"a {machine}.\n\nTry rephrasing the question, or check the {machine} manual "
        f"directly."
    )


def _has_machine_specific(combined: list) -> bool:
    """True if any retrieved chunk is machine-specific (i.e. not the generic
    'context' KB doc) — used to detect 'nothing specific found'."""
    return any((meta or {}).get("doc_class") != "context" for _, _, meta in combined)

SYSTEM_PROMPT = """You are the Cradic AI Field Diagnostic Assistant, built for Select Equip field service technicians servicing G. Mondini tray sealer machines on factory floors in Australia.

Your job is to give fast, accurate, safety-aware diagnostic help based on:
- G. Mondini Trave series manuals
- Select Equip service history (indexed service reports)
- Lenze drive system documentation (EtherCAT, 3200C, i550, i700, 8400, 9400HL)

RESPONSE RULES:
1. Safety warning FIRST if the procedure involves electrical isolation, pneumatic systems, moving parts, or hot components.
2. Historical context second — if a relevant past fault pattern exists for this machine or site, surface it.
3. Step-by-step procedure — numbered, clear, in order.
4. References last — cite manual section and page number, or service report number (e.g. SR #5937).

TONE: Plain English. Direct. No waffle. These are experienced technicians under time pressure.

FORMAT: Use markdown with headers, numbered steps, and tables where useful. Use these emoji markers:
- ⚠️ Safety warnings
- 🔧 Procedures and fixes
- 📋 Historical fault patterns
- 📖 Manual/document references

MANUAL PAGE LINKS:
When referencing the Mondini manual, always format page references as a clickable markdown link using this exact format:
- For Trave 340: [Trave 340 Manual p.{page}](https://cradic-field-diagnostic.vercel.app/manuals/User Trave 340.pdf#page={page})
- For Trave 350: [Trave 350 Manual p.{page}](https://cradic-field-diagnostic.vercel.app/manuals/User Trave 350.pdf#page={page})
- For Trave 367: [Trave 367 Manual p.{page}](https://cradic-field-diagnostic.vercel.app/manuals/User Trave 367.pdf#page={page})
- For Trave 590: [Trave 590 Manual p.{page}](https://cradic-field-diagnostic.vercel.app/manuals/User Trave 590.pdf#page={page})
- For Trave 1000: [Trave 1000 Manual p.{page}](https://cradic-field-diagnostic.vercel.app/manuals/User Trave 1000.pdf#page={page})
- For Trave 1200: [Trave 1200 Manual p.{page}](https://cradic-field-diagnostic.vercel.app/manuals/User Trave 1200.pdf#page={page})
- For Trave 1400: [Trave 1400 Manual p.{page}](https://cradic-field-diagnostic.vercel.app/manuals/User Trave 1400.pdf#page={page})
Always include the page number link when citing the manual. Match the link to the machine the technician selected. If you don't know the exact page, link to page 1.

PHOTO EVIDENCE:
When the context includes chunks prefixed with 📷 PHOTO EVIDENCE:, explicitly reference them in your response under a section called 📷 Photo Evidence from Job. Describe what the photos show and how it relates to the fault or procedure.

HARD RULES:
- Do not guess fault causes without citing a source.
- Do not contradict or modify Mondini manual content — layer intelligence on top of it.
- Do not fabricate service report numbers, alarm codes, or page references.
- Do not give confident answers about a specific machine serial number without checking if service history exists for that serial.
- If the retrieved context does not contain enough information to answer confidently, say so and suggest what to check next."""

# Singletons — loaded once per process
_chroma_client = None
_chroma_collection = None
_pinecone_index = None
_ef = None
_claude = None


def _get_ef():
    global _ef
    if _ef is None:
        _ef = embedding_functions.DefaultEmbeddingFunction()
    return _ef


def _get_chroma_collection():
    global _chroma_client, _chroma_collection
    if _chroma_collection is None:
        ef = _get_ef()
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_PATH)
        try:
            _chroma_collection = _chroma_client.get_collection(
                name=COLLECTION_NAME,
                embedding_function=ef,
            )
        except Exception:
            raise RuntimeError(
                "Knowledge base is still loading — this takes about 10 minutes on first startup. "
                "Please try again shortly."
            )
    return _chroma_collection


def _get_pinecone_index():
    global _pinecone_index
    if _pinecone_index is None:
        api_key = os.environ.get("PINECONE_API_KEY")
        if not api_key:
            raise RuntimeError("PINECONE_API_KEY not set.")
        from pinecone import Pinecone
        pc = Pinecone(api_key=api_key)
        _pinecone_index = pc.Index(PINECONE_INDEX_NAME)
    return _pinecone_index


def _get_claude():
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _claude


def _build_context_and_answer(combined: list, question: str) -> str:
    """Shared context formatting and Claude call used by both retrieval paths."""
    combined = combined[:MAX_CONTEXT_CHUNKS]
    context_blocks = []
    for i, (chunk_id, doc, meta) in enumerate(combined, start=1):
        source = meta.get("source_file", "unknown")
        page = meta.get("page_number")
        ref = f"{source}, page {page}" if page else source
        prefix = "\U0001f4f7 PHOTO EVIDENCE: " if meta.get("doc_type") == "image_description" else ""
        context_blocks.append(f"[{i}] Source: {ref}\n{prefix}{doc}")

    context_text = "\n\n---\n\n".join(context_blocks)
    user_message = f"""RETRIEVED CONTEXT:

{context_text}

---

TECHNICIAN QUESTION: {question}"""

    claude = _get_claude()
    message = claude.messages.create(
        model=ANSWER_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text


def _get_answer_pinecone(question: str, machine: str = None) -> str:
    index = _get_pinecone_index()
    ef = _get_ef()

    # Embed the query with the same ONNX model used at ingest time
    query_embedding = ef([question])[0].tolist()

    # Scope retrieval to the selected machine (manuals/reports/drive-docs/context)
    machine_filter = _machine_filter(machine)[0] if machine else None

    # Primary retrieval — top 15 chunks by cosine similarity (machine-scoped)
    results = index.query(
        vector=query_embedding,
        top_k=TOP_K,
        include_metadata=True,
        filter=machine_filter,
    )

    seen_ids = set()
    combined = []
    source_rank = {}

    for pos, match in enumerate(results.matches):
        meta = match.metadata or {}
        doc = meta.pop("text", "")  # text was stored in metadata at ingest time
        seen_ids.add(match.id)
        combined.append((match.id, doc, meta))
        sf = meta.get("source_file")
        if sf and sf not in source_rank:
            source_rank[sf] = pos

    # If a machine is selected but nothing machine-specific came back, say so
    # plainly rather than answer from unfiltered (other-machine) sources.
    if machine and not _has_machine_specific(combined):
        return _no_machine_results_message(machine)

    # Secondary retrieval — image_description chunks from the same source files
    # (already machine-scoped, since source_files come from scoped primary hits)
    source_files = list(source_rank.keys())
    if source_files:
        try:
            img_results = index.query(
                vector=query_embedding,
                top_k=50,
                filter={"$and": [
                    {"doc_type": {"$eq": "image_description"}},
                    {"source_file": {"$in": source_files}},
                ]},
                include_metadata=True,
            )
            img_candidates = []
            for match in img_results.matches:
                if match.id in seen_ids:
                    continue
                meta = match.metadata or {}
                doc = meta.pop("text", "")
                doc_lower = doc.lower()
                if any(logo in doc_lower for logo in _LOGO_FILTERS):
                    continue
                img_candidates.append((match.id, doc, meta))

            img_candidates.sort(key=lambda x: source_rank.get(x[2].get("source_file", ""), 999))

            img_count_per_file = {}
            for img_id, img_doc, img_meta in img_candidates:
                sf = img_meta.get("source_file", "")
                img_count_per_file[sf] = img_count_per_file.get(sf, 0) + 1
                if img_count_per_file[sf] > MAX_IMAGES_PER_FILE:
                    continue
                seen_ids.add(img_id)
                combined.append((img_id, img_doc, img_meta))
        except Exception:
            pass  # Don't fail the query if image lookup fails

    return _build_context_and_answer(combined, question)


def _chroma_machine_keep(meta: dict, machine: str, platform: str) -> bool:
    """Python-side equivalent of _machine_filter for ChromaDB, which stores
    machine_models as a comma-joined string (no native $in)."""
    meta = meta or {}
    if meta.get("doc_class") == "context":
        return True
    if meta.get("machine_model") == machine:
        return True
    mm = meta.get("machine_models")
    if mm and machine in [s.strip() for s in str(mm).split(",")]:
        return True
    # drive docs only (manuals also carry drive_platform — see _machine_filter)
    if platform and meta.get("drive_platform") == platform \
            and meta.get("doc_class") == "drive_doc":
        return True
    return False


def _get_answer_chroma(question: str, machine: str = None) -> str:
    collection = _get_chroma_collection()

    # Pull a wider set when scoping so the post-filter still yields ~TOP_K.
    n_results = 60 if machine else TOP_K
    results = collection.query(
        query_texts=[question],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    ids = results["ids"][0]

    rows = list(zip(ids, docs, metas))
    if machine:
        platform = _MACHINE_PLATFORM.get(machine)
        rows = [r for r in rows if _chroma_machine_keep(r[2], machine, platform)][:TOP_K]
        if not _has_machine_specific(rows):
            return _no_machine_results_message(machine)
        ids = [r[0] for r in rows]

    seen_ids = set(ids)
    combined = list(rows)

    source_rank = {}
    for pos, (_, _, m) in enumerate(combined):
        sf = (m or {}).get("source_file")
        if sf and sf not in source_rank:
            source_rank[sf] = pos

    # Secondary retrieval — image_description chunks from the same source files
    source_files = list(source_rank.keys())
    if source_files:
        try:
            img_results = collection.get(
                where={"$and": [
                    {"doc_type": {"$eq": "image_description"}},
                    {"source_file": {"$in": source_files}},
                ]},
                include=["documents", "metadatas"],
            )
            img_candidates = []
            for img_id, img_doc, img_meta in zip(
                img_results["ids"], img_results["documents"], img_results["metadatas"]
            ):
                if img_id in seen_ids:
                    continue
                doc_lower = img_doc.lower()
                if any(logo in doc_lower for logo in _LOGO_FILTERS):
                    continue
                img_candidates.append((img_id, img_doc, img_meta))

            img_candidates.sort(key=lambda x: source_rank.get(x[2].get("source_file", ""), 999))

            img_count_per_file = {}
            for img_id, img_doc, img_meta in img_candidates:
                sf = img_meta.get("source_file", "")
                img_count_per_file[sf] = img_count_per_file.get(sf, 0) + 1
                if img_count_per_file[sf] > MAX_IMAGES_PER_FILE:
                    continue
                seen_ids.add(img_id)
                combined.append((img_id, img_doc, img_meta))
        except Exception:
            pass  # Don't fail the query if image lookup fails

    return _build_context_and_answer(combined, question)


def get_answer(question: str, machine: str = None) -> str:
    """Route to Pinecone if PINECONE_API_KEY is set, otherwise fall back to ChromaDB.
    When `machine` is set, retrieval is strictly scoped to that machine
    (manuals + its service reports + its drive-platform docs + KB context)."""
    if os.environ.get("PINECONE_API_KEY"):
        return _get_answer_pinecone(question, machine)
    return _get_answer_chroma(question, machine)
