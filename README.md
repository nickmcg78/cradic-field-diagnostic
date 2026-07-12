# Cradic Field Diagnostic

AI diagnostic assistant for Select Equip field technicians servicing Mondini Trave tray-sealing machines. Answers fault/procedure questions grounded in ~8,900 real service reports and the OEM manuals, with strict anti-hallucination and safety-first rules.

## Live URLs

| What | URL |
|------|-----|
| Frontend (Vercel) | https://cradic-field-diagnostic.vercel.app (password-protected) |
| Backend (Render) | https://cradic-field-diagnostic-api.onrender.com |
| Backend health check | https://cradic-field-diagnostic-api.onrender.com/health → `{"status":"ok"}` |

Both auto-deploy from `main` — Render builds the backend (`backend/render.yaml`), Vercel builds the frontend. Render dashboard service: `cradic-field-diagnostic-api` (srv-d79l6ik50q8c73fkv0sg, Singapore).

## Architecture

- **Backend:** Flask + gunicorn (`backend/app.py`). `POST /query` with `Authorization: Bearer <APP_PASSWORD>`, JSON body `{"question": "...", "machine": "Trave 590"}` (machine optional but scopes retrieval).
- **Retrieval:** **Pinecone is the production vector store.** `get_answer()` routes to Pinecone when `PINECONE_API_KEY` is set (it is set on Render), and falls back to ChromaDB only when it isn't. Embeddings: ONNX all-MiniLM-L6-v2 (via Git LFS, `backend/onnx_models/`).
- **Models:** `backend/model_config.py` is the single source of truth (overridable via `ANSWER_MODEL` / `VISION_MODEL` env vars).
- **Frontend:** React + Vite (`frontend/`). Backend URL comes from `VITE_API_URL` (set in Vercel; defaults to `http://localhost:5000` locally).
- **Drive platforms:** Trave 340/350/367 = B&R; Trave 590/1000/1200/1400 = Lenze. Encoded in `query.py` (`_MACHINE_PLATFORM`), the system prompt, and `ingest.MANUAL_SPECS` — keep all three in sync.

## Environment variables

Backend (`backend/.env` locally; Render dashboard in prod): `ANTHROPIC_API_KEY`, `APP_PASSWORD`, `PINECONE_API_KEY`.
Frontend (`frontend/.env` locally; Vercel in prod): `VITE_API_URL`, `VITE_APP_PASSWORD`.

## Data locations

- **Service report corpus:** `backend/docs/` — ~8,900 files, flat, **gitignored** (3.9 GB; source of truth is SharePoint). Search by SR number.
- **Manuals (served to app):** `frontend/public/manuals/`
- **ChromaDB (`backend/chroma_db/`):** legacy/local fallback only — gitignored for new changes; not used in production.
- Ignore `Select_equip/sharepoint_export/extracted/` — failed split-archive unzip, incomplete. `backend/docs/` is the full corpus.

## Git hazards — read before committing

1. **Never `git add -A` / `git add .` / `commit -a`.** Thousands of gitignored-or-WIP files sit in the working tree.
2. `backend/onnx_models/**/*.onnx` is **Git LFS**. Verify `git lfs install` is active before committing anything.
3. `backend/chroma_db/chroma.sqlite3` is tracked at ~93 MB but is 300+ MB on disk. GitHub rejects files >100 MB — never stage it. (Planned: untrack it entirely, since prod is Pinecone.)
4. Stage files **explicitly by path**, confirm with `git status` before every commit.

## Local development

```
# backend
cd backend && pip install -r requirements.txt && python app.py   # port 5000

# frontend
cd frontend && npm install && npm run dev
```

## Key documents

- `STATUS.md` — current state, open risks, next moves (start here)
- `TEST_FRAMEWORK.md` / `TEST_RESULTS.md` / `SCORECARD.md` — 19-scenario live test round (13 Jul 2026)
- `HILTON_1400_ATTRIBUTION.md` — Hilton multi-line report attribution findings
- `CLAUDE_CODE_BRIEF.md` / `BRIEF_RESULTS.md` — autonomous run brief + results (13 Jul 2026)
- `INGESTION_HANDOFF.md` — corpus ingestion notes
