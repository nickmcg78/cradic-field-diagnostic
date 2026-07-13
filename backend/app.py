import os
import traceback
from pathlib import Path
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

from query import get_answer, get_answer_stream  # noqa: E402 — import after env load
import notes  # noqa: E402 — technician-notes storage + email

# Point ONNX model to bundled copy in repo — no network download needed at runtime
from pathlib import Path as _Path
try:
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2 as _ONNX
    _ONNX.DOWNLOAD_PATH = _Path(__file__).parent / "onnx_models" / "all-MiniLM-L6-v2"
except Exception:
    pass

# Pre-warm ONNX embedding model at startup so first query is instant
try:
    from chromadb.utils import embedding_functions as _ef
    _ef.DefaultEmbeddingFunction()(["warmup"])
    print("Embedding model ready.")
except Exception as _e:
    print(f"Embedding warmup skipped: {_e}")

app = Flask(__name__)
CORS(app)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


def check_auth():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[len("Bearer "):]
    return token == APP_PASSWORD


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def _streaming_enabled():
    # Streaming on by default; set STREAMING=0 to fall back to the JSON path.
    return os.environ.get("STREAMING", "1") != "0"


@app.route("/query", methods=["POST"])
def handle_query():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": "Missing 'question' field"}), 400

    question = data["question"].strip()
    if not question:
        return jsonify({"error": "Question cannot be empty"}), 400

    # Optional structured machine scope (e.g. "Trave 590") — drives strict
    # retrieval filtering in query.py.
    machine = (data.get("machine") or "").strip() or None

    if _streaming_enabled():
        # Prime the generator so retrieval runs (and can raise) BEFORE we commit
        # to a 200 streamed response — retrieval errors still return proper JSON.
        gen = get_answer_stream(question, machine)
        try:
            first_chunk = next(gen)
        except StopIteration:
            first_chunk = ""
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500

        def produce():
            yield first_chunk
            try:
                for chunk in gen:
                    yield chunk
            except Exception:
                # Mid-stream failure: status is already 200, so just stop.
                traceback.print_exc()

        return Response(
            stream_with_context(produce()),
            mimetype="text/plain",  # Flask appends "; charset=utf-8"
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    try:
        answer = get_answer(question, machine)
        return jsonify({"answer": answer})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/notes", methods=["POST"])
def handle_notes():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    tech_name = (data.get("tech_name") or "").strip()
    note_text = (data.get("note_text") or "").strip()
    if not tech_name or not note_text:
        return jsonify({"error": "tech_name and note_text are required"}), 400

    customer = (data.get("customer") or "").strip() or None
    machine = (data.get("machine") or "").strip() or None
    serial = (data.get("serial") or "").strip() or None

    if not notes.database_configured():
        # No storage target — refuse rather than pretend. Tech must copy the note.
        return jsonify({
            "error": "Notes storage is not configured (DATABASE_URL unset). "
                     "Your note was NOT saved — please copy it before leaving the page."
        }), 503

    # Store first — a note must never be silently lost.
    try:
        note_id = notes.store_note(tech_name, customer, machine, serial, note_text)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Failed to store note: {e}"}), 500

    # Then attempt email (best-effort); update emailed_ok on success.
    emailed, reason = False, None
    try:
        emailed, reason = notes.send_note_email(
            tech_name, customer, machine, serial, note_text
        )
        if emailed:
            try:
                notes.mark_emailed(note_id)
            except Exception:
                traceback.print_exc()  # stored + emailed; flag update is non-critical
    except Exception as e:
        traceback.print_exc()
        emailed, reason = False, f"email send failed: {e}"

    result = {"stored": True, "emailed": emailed}
    if not emailed and reason:
        result["reason"] = reason
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False)
