import os
from pathlib import Path
import anthropic
import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

_DEFAULT_CHROMA_PATH = str(Path(__file__).parent / "chroma_db")
CHROMA_PERSIST_PATH = os.environ.get("CHROMA_PERSIST_PATH", _DEFAULT_CHROMA_PATH)
COLLECTION_NAME = "select_equip_kb"
TOP_K = 8

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

HARD RULES:
- Do not guess fault causes without citing a source.
- Do not contradict or modify Mondini manual content — layer intelligence on top of it.
- Do not fabricate service report numbers, alarm codes, or page references.
- Do not give confident answers about a specific machine serial number without checking if service history exists for that serial.
- If the retrieved context does not contain enough information to answer confidently, say so and suggest what to check next."""

# Singletons — loaded once per process
_client = None
_collection = None
_claude = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        _client = chromadb.PersistentClient(path=CHROMA_PERSIST_PATH)
        try:
            _collection = _client.get_collection(
                name=COLLECTION_NAME,
                embedding_function=ef,
            )
        except Exception:
            # Collection not ready yet (ingest still running)
            raise RuntimeError(
                "Knowledge base is still loading — this takes about 10 minutes on first startup. "
                "Please try again shortly."
            )
    return _collection


def _get_claude():
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _claude


def get_answer(question: str) -> str:
    collection = _get_collection()

    results = collection.query(
        query_texts=[question],
        n_results=TOP_K,
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0]
    metas = results["metadatas"][0]

    # Build context blocks
    context_blocks = []
    for i, (doc, meta) in enumerate(zip(docs, metas), start=1):
        source = meta.get("source_file", "unknown")
        page = meta.get("page_number")
        ref = f"{source}, page {page}" if page else source
        context_blocks.append(f"[{i}] Source: {ref}\n{doc}")

    context_text = "\n\n---\n\n".join(context_blocks)

    user_message = f"""RETRIEVED CONTEXT:

{context_text}

---

TECHNICIAN QUESTION: {question}"""

    claude = _get_claude()
    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    return message.content[0].text
