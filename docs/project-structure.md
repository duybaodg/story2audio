# Project Structure

This document explains the main files and directories in Ebook2Audio.

## Runtime Overview

Ebook2Audio is a FastAPI web app with a separate VieNeu worker:

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

## Directory Tree

```text
story2audio/
├── .github/workflows/
│   └── ci-cd.yml                 # Test, build, and VPS deployment workflow
├── docs/                         # Architecture, deployment, and historical plans
├── models/
│   ├── document.py               # Upload/document domain models
│   └── vieneu/assets/voices.json # VieNeu voice registry shown in the UI
├── static/
│   ├── app.js                    # Browser behavior and API calls
│   ├── style.css                 # UI styling
│   └── favicon.svg
├── templates/
│   └── index.html                # Main application page
├── tests/                        # Pytest regression and integration tests
├── main.py                       # FastAPI application and TTS orchestration
├── document_api.py               # Document HTTP endpoints and ownership checks
├── file_processor.py             # Chunked upload storage and assembly
├── text_extractor.py             # PDF, EPUB, chapter, and OCR extraction
├── job_queue.py                  # File-backed document extraction jobs
├── rate_limiter.py               # Redis upload/TTS rate limits
├── tts_queue.py                  # Redis VieNeu queue and worker readiness
├── tts_worker.py                 # Dedicated VieNeu inference process
├── vieneu_model.py               # VieNeu SDK/model lifecycle
├── vieneu_audio_quality.py       # Audio normalization and encoding
├── vietnamese_text_processor.py  # Vietnamese text preparation
├── Dockerfile                    # Reproducible application image
├── docker-compose.yml            # App, worker, Redis, and persistent volumes
├── pyproject.toml                # Project metadata and direct dependencies
└── uv.lock                       # Exact resolved dependency versions
```

Ignored runtime directories such as `audio_cache/`, `documents/`, `jobs/`, and
`obs/` may exist locally but are deliberately omitted from the source tree.

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
| `.github/workflows/ci-cd.yml` | GitHub Actions CI and VPS deployment after successful `main` checks |
| `.env.example` | Safe example production/local configuration without credentials |
| `.gitignore` / `.dockerignore` | Prevent runtime data, credentials, caches, and local tooling files from entering Git or Docker images |

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
ebook2audio_models
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

These directories are ignored by Git and Docker. Named volumes preserve them in
Compose without putting generated or private data in the repository or image.

## Test Files

| Path | Purpose |
| --- | --- |
| `tests/test_document_api.py` | Document API tests and TTS route behavior tests |
| `tests/test_file_processor.py` | Upload/file processor tests |
| `tests/test_job_queue.py` | Document extraction job queue tests |
| `tests/test_models.py` | Model/domain tests |
| `tests/test_text_extractor.py` | Text extraction tests |
| `tests/test_tts_queue.py` | VieNeu queue-cap and worker-heartbeat tests |
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
  -> main.py assigns an HTTP-only browser session cookie
  -> file_processor.py creates an owner-bound upload session

POST /document/upload/chunk
  -> file_processor.py stores chunks

POST /document/upload/complete
  -> file_processor.py assembles document
  -> job_queue.py can run extraction
  -> text_extractor.py extracts PDF/EPUB/OCR text

GET /document/{id}/extract/stream
  -> document_api.py verifies browser-session ownership
  -> document_api.py streams extraction progress
```

Document IDs are not global authorization. Every queue, upload, document,
content, extraction-job, and deletion route checks the HTTP-only browser session
that created the upload. A different browser session receives `404` instead of
learning whether another user's document exists.

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

After model initialization, the worker writes a readiness marker and refreshes
a Redis heartbeat. Docker health checks wait for the marker; `/tts/health`
checks the heartbeat so Redis availability alone cannot make a dead worker look
ready.

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
ebook2audio_cache
ebook2audio_documents
ebook2audio_jobs
ebook2audio_models
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

## Security and Abuse Controls

The public application uses lightweight anonymous-session isolation rather than
user accounts:

- an HTTP-only, SameSite browser cookie owns uploaded documents and extraction jobs
- production HTTPS deployments should set `SESSION_COOKIE_SECURE=true`
- `X-Forwarded-For` is ignored unless `TRUST_PROXY_HEADERS=true`
- forwarded headers should only be enabled when port `8000` is firewalled behind a trusted reverse proxy
- upload initiation and TTS requests are rate-limited in Redis
- `TTS_MAX_TEXT_LENGTH` bounds each synthesis request
- `TTS_MAX_QUEUE_SIZE` prevents an unbounded VieNeu backlog
- `/health` fails when Redis is unavailable
- `/tts/health` fails when the VieNeu worker heartbeat expires

The audio cache remains shared and content-addressed. Add authenticated accounts
or API keys if the service needs durable identities, billing, quotas, or stronger
protection against distributed abuse.

## CI/CD Flow

The workflow in `.github/workflows/ci-cd.yml` runs for pull requests and pushes
to `main`:

```text
checkout
  -> install locked dependencies
  -> run pytest
  -> run Python and JavaScript syntax checks
  -> validate Docker Compose
  -> build production images
```

After those checks pass on `main`, the `deploy` job connects to the VPS over SSH,
fast-forwards the existing checkout, and runs:

```bash
docker compose up -d --build --remove-orphans --wait
```

The GitHub `production` environment supplies VPS secrets and can require manual
approval. See `.github/workflows/ci-cd.yml` for the required secrets and variables.

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
uv run pytest
```

Syntax check key Python files:

```bash
uv run python -m py_compile main.py tts_queue.py tts_worker.py
```

Syntax check frontend:

```bash
node --check static/app.js
```
