import os
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

from query import get_answer  # noqa: E402 — import after env load

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

    try:
        answer = get_answer(question)
        return jsonify({"answer": answer})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False)
