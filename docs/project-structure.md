# Project Structure

This document explains the main files and directories in Story2Audio.

## Runtime Overview

Story2Audio is a FastAPI web app with a separate VieNeu worker:

```text
Browser
  -> FastAPI app
      UI
      TTS API
      document upload API
      status/audio streaming

FastAPI app
  -> Redis
      upload rate limiting
      VieNeu job queue

VieNeu worker
  -> Redis
      reserves VieNeu jobs
  -> shared cache
      writes generated audio and metadata
```

## Top-Level Files

| Path | Purpose |
| --- | --- |
| `main.py` | FastAPI app entrypoint, TTS routes, streaming, cache metadata, Edge/gTTS/VieNeu generation orchestration |
| `tts_queue.py` | Redis queue helpers for VieNeu jobs, cancellation, recovery, and acknowledgements |
| `tts_worker.py` | Dedicated VieNeu worker process that pulls Redis jobs and runs TTS generation |
| `vieneu_model.py` | VieNeu model configuration, model pool initialization, preset voices |
| `vieneu_audio_quality.py` | VieNeu audio post-processing and quality settings |
| `vietnamese_text_processor.py` | Vietnamese-aware text chunking/normalization helpers |
| `document_api.py` | Document upload, chunk upload, extraction endpoints, document status routes |
| `file_processor.py` | Upload session handling, file assembly, document storage cleanup |
| `text_extractor.py` | PDF/EPUB extraction, OCR fallback, chapter extraction helpers |
| `job_queue.py` | Local file-backed job queue for document extraction workers |
| `rate_limiter.py` | Redis-backed upload rate limiting |
| `docker-compose.yml` | Local/production Compose services: `redis`, `app`, `vieneu-worker` |
| `Dockerfile` | Python image build, system packages, dependency install, app startup command |
| `pyproject.toml` | Python project metadata and dependencies |
| `uv.lock` | Locked dependency graph for `uv` |

## Frontend Files

| Path | Purpose |
| --- | --- |
| `templates/index.html` | Main HTML shell rendered by FastAPI |
| `static/app.js` | Browser app logic: upload UI, language/voice engine controls, TTS calls, streaming playback |
| `static/style.css` | UI styling |
| `static/favicon.svg` | App favicon |

Important frontend behavior:

- selecting `VieNeu-TTS` forces Vietnamese and hides other language choices
- Edge/gTTS/VieNeu requests are sent to `/tts/start`
- live audio uses `/tts/stream/{cache_id}`
- status polling uses `/tts/status/{cache_id}`
- completed audio downloads from `/tts/file/{cache_id}`
- the visible delete button deletes only the current `cache_id`, not the whole shared cache

## Model and Voice Assets

| Path | Purpose |
| --- | --- |
| `models/__init__.py` | Model package marker |
| `models/document.py` | Document-related Pydantic/domain models |
| `models/vieneu/assets/voices.json` | Local VieNeu preset voice metadata |

VieNeu model weights are not stored in the repository. They are downloaded/cached under:

```text
/root/.cache/huggingface
```

In Docker Compose this path is backed by:

```text
story2audio_models
```

In Azure this should be backed by Azure Files.

## Generated Runtime Data

These paths are runtime data, not source code:

| Path | Purpose |
| --- | --- |
| `audio_cache/` | Generated audio, metadata, subtitles, cue files |
| `documents/` | Uploaded/assembled documents and sessions |
| `jobs/` | Document extraction job files |
| `.coverage` | Test coverage artifact |

The repository may contain local generated files while developing. Do not treat them as source unless intentionally committed.

## Test Files

| Path | Purpose |
| --- | --- |
| `tests/test_document_api.py` | Document API tests and TTS route behavior tests |
| `tests/test_file_processor.py` | Upload/file processor tests |
| `tests/test_job_queue.py` | Document extraction job queue tests |
| `tests/test_models.py` | Model/domain tests |
| `tests/test_text_extractor.py` | Text extraction tests |
| `tests/test_vieneu_reliability.py` | VieNeu configuration and reliability tests |
| `tests/fixtures/documents/` | Test fixture documentation/data |

## Documentation Files

| Path | Purpose |
| --- | --- |
| `README.md` | Main project overview and quick start |
| `docs/azure-deployment.md` | Azure architecture, versioning, sizing, and operational considerations |
| `docs/azure-deploy-plan.md` | Step-by-step Azure deployment runbook |
| `docs/redis-vieneu-queue.md` | Redis and VieNeu queue explanation |
| `docs/project-structure.md` | This project structure guide |
| `RELEASE_NOTES.md` | Release history |
| `CONTRIBUTING.md` | Contribution guidance |
| `IMPLEMENTATION_SUMMARY.md` | Historical implementation notes |
| `PROJECT_REVIEW_FINDINGS.md` | Review notes and known findings |
| `CLAUDE.md` | Local assistant/project notes |
| `docs/superpowers/` | Historical feature specs and plans |

## TTS Flow

### Edge/gTTS

```text
POST /tts/start
  -> FastAPI validates request
  -> FastAPI schedules generate_chunks_sync with BackgroundTasks
  -> generation writes /app/audio_cache/{cache_id}.mp3
  -> frontend streams or downloads audio
```

### VieNeu

```text
POST /tts/start
  -> FastAPI validates request
  -> FastAPI writes queued metadata
  -> FastAPI enqueues Redis job via tts_queue.py
  -> tts_worker.py reserves job
  -> worker runs generate_chunks_sync
  -> worker writes audio and metadata to shared cache
  -> frontend streams or downloads audio
```

For more detail, see [`redis-vieneu-queue.md`](redis-vieneu-queue.md).

## Document Upload Flow

```text
POST /document/upload/initiate
  -> rate_limiter.py checks Redis limits
  -> file_processor.py creates upload session

POST /document/upload/chunk
  -> file_processor.py stores chunks

POST /document/upload/complete
  -> file_processor.py assembles document
  -> job_queue.py can run extraction
  -> text_extractor.py extracts PDF/EPUB/OCR text

GET /document/{id}/extract/stream
  -> document_api.py streams extraction progress
```

## Docker Compose Services

### `redis`

Runs Redis for upload rate limits and VieNeu queue state.

### `app`

Runs:

```text
uvicorn main:app --host 0.0.0.0 --port 8000
```

Key production setting:

```env
VIENEU_INIT_IN_WEB=false
```

### `vieneu-worker`

Runs:

```text
python tts_worker.py
```

Key production setting:

```env
VIENEU_MAX_WORKERS=1
```

## Shared Volume Requirements

The app and worker must share these paths:

```text
/app/audio_cache
/app/documents
/app/jobs
/root/.cache/huggingface
```

In Compose:

```text
story2audio_cache
story2audio_documents
story2audio_jobs
story2audio_models
```

In Azure:

```text
Azure Files mounts
```

## Cache Scope

Audio cache is server-side and shared by all users. The `cache_id` is generated from the normalized text, voice, engine, language, and quality variant. If two users submit the same request, they can reuse the same generated audio.

Normal UI actions must only delete the current audio:

```text
DELETE /tts/file/{cache_id}
```

The global cache route deletes all audio cache files:

```text
DELETE /tts/cache
```

That route is disabled by default and should only be enabled for admin/internal maintenance:

```env
ENABLE_GLOBAL_CACHE_CLEAR=true
```

## Common Development Commands

Install dependencies:

```bash
uv sync
```

Run app locally:

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Run worker locally:

```bash
uv run python tts_worker.py
```

Run with Docker Compose:

```bash
docker compose up -d --build
```

Run tests:

```bash
pytest
```

Syntax check key Python files:

```bash
python -m py_compile main.py tts_queue.py tts_worker.py
```

Syntax check frontend:

```bash
node --check static/app.js
```
