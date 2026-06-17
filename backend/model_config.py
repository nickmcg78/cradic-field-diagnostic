"""Single source of truth for Claude model IDs used across the backend.

When a model is retired (a stale ID returns HTTP 404), update it here — or
override at deploy time via the ANSWER_MODEL / VISION_MODEL env vars — instead
of hunting through query.py / ingest.py.

Verified current IDs (Anthropic Models API, 2026-06-17):
  - claude-sonnet-4-6  -> Claude Sonnet 4.6  (answer generation)
  - claude-haiku-4-5   -> Claude Haiku 4.5   (image descriptions; cheapest tier)
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present (override=False so real environment vars win). Safe to
# call again — app.py / query.py / ingest.py also load it.
load_dotenv(Path(__file__).parent / ".env", override=False)

# Answer-generation model (query.py). Sonnet 4.6: strong quality at lower cost.
ANSWER_MODEL = os.environ.get("ANSWER_MODEL", "claude-sonnet-4-6")

# Vision model for image descriptions (ingest.py). Haiku 4.5: cheapest tier.
VISION_MODEL = os.environ.get("VISION_MODEL", "claude-haiku-4-5")
