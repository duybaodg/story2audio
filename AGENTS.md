# Repository Guidelines

## Project Structure & Module Organization

Story2Audio is a FastAPI app with a separate VieNeu worker. Core backend code lives at the repository root:

- `main.py` — FastAPI app, TTS routes, streaming, cache metadata.
- `tts_queue.py` / `tts_worker.py` — Redis-backed VieNeu queue and worker.
- `document_api.py`, `file_processor.py`, `text_extractor.py`, `job_queue.py` — document upload and extraction flow.
- `vieneu_model.py`, `vieneu_audio_quality.py`, `vietnamese_text_processor.py` — VieNeu and text processing helpers.
- `templates/` and `static/` — browser UI.
- `models/` — domain models and VieNeu voice assets.
- `tests/` — pytest suite.
- `docs/` — deployment, Redis queue, and project structure guides.

Runtime/generated data such as `audio_cache/`, `documents/`, `jobs/`, and `.coverage` should not be treated as source.

## Build, Test, and Development Commands

```bash
uv sync
```
Install Python dependencies from `pyproject.toml` and `uv.lock`.

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Run the FastAPI app locally.

```bash
uv run python tts_worker.py
```
Run the VieNeu Redis worker locally.

```bash
docker compose up -d --build
```
Run Redis, the web app, and `vieneu-worker`.

```bash
pytest
python -m py_compile main.py tts_queue.py tts_worker.py
node --check static/app.js
```
Run tests and syntax checks.

## Coding Style & Naming Conventions

Use Python 3.13, 4-space indentation, type hints where practical, and clear snake_case names. Keep FastAPI routes thin and place shared behavior in helpers/modules. JavaScript in `static/app.js` uses plain browser APIs; prefer small functions and existing UI patterns.

## Testing Guidelines

Tests use `pytest`. Name files `tests/test_*.py` and test functions `test_*`. Add focused regression tests for route behavior, Redis queue behavior, cache semantics, and document processing. Avoid tests that require real model downloads unless explicitly isolated or mocked.

## Commit & Pull Request Guidelines

Recent history uses conventional prefixes such as `feat:` and `fix:`. Keep commits scoped, for example `fix: prevent global cache clearing from UI`. PRs should include a short summary, test results, affected endpoints/UI areas, and screenshots for visible frontend changes.

## Security & Configuration Tips

Do not commit real tokens or Redis URLs. Use `.env`, Docker secrets, Azure Container Apps secrets, or Key Vault. In production keep `VIENEU_INIT_IN_WEB=false`, `VIENEU_MAX_WORKERS=1`, and leave `ENABLE_GLOBAL_CACHE_CLEAR` disabled unless performing admin maintenance.
