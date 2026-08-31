# Ebook2Audio Technical Guide

This is the authoritative technical and operational guide for Ebook2Audio v4.
It describes the current code and supported deployment topology.

## Quick Start

### Docker Compose

```bash
cp .env.example .env
docker compose --profile vieneu up -d --build --wait
docker compose ps
```

Open `http://localhost:8000`. The `.env` file is required by Compose even when
all values use their defaults.

### Local Development

Python 3.13, Redis, and FFmpeg are required.

```bash
cp .env.example .env
uv sync --locked
docker compose up -d redis
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Run the VieNeu worker in another terminal only when VieNeu is needed:

```bash
REDIS_URL=redis://localhost:6379 uv run python tts_worker.py
```

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

There are deliberately only three deployable services. Edge TTS and gTTS run
as FastAPI background tasks; VieNeu runs out of process because its local model
is substantially heavier.

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
| `file_processor.py` | Upload session handling, file assembly, persistent document metadata, restart recovery, cleanup |
| `text_extractor.py` | PDF/EPUB extraction, chapter detection, and text-quality scoring |
| `job_queue.py` | Local file-backed extraction jobs, worker threads, cooperative cancellation and timeout |
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
| `documents/` | Upload sessions, assembled documents, and persistent document metadata |
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
| `CLAUDE.md` | Local assistant/project notes |

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
  -> validates MD5 and PDF/EPUB content signature
  -> persists document.json beside the uploaded file

GET /document/{id}/extract/stream
  -> document_api.py verifies browser-session ownership
  -> text_extractor.py extracts PDF/EPUB text
  -> document_api.py streams progress and chapters with SSE

POST /document/job/{id}/extract
  -> submits the same document to the file-backed worker queue
  -> job_queue.py applies cancellation and JOB_TIMEOUT_MINUTES
  -> completed chapter data is persisted with the document
```

The synchronous SSE flow is used by the current browser UI. The job endpoints
provide polling, retry, and cancellation for clients that prefer asynchronous
processing.

### Document Persistence and Restart Recovery

An assembled document has this layout:

```text
/app/documents/
├── uploads/{upload_id}/chunk_N
└── assembled/{document_id}/
    ├── document.json
    └── original-filename.pdf
```

Upload sessions live in the single web process while their chunks are stored on
disk. Completed documents write `document.json` atomically whenever status or extracted chapters change.
At web startup, `recover_documents()` loads valid metadata whose source file
still exists. Running extraction jobs left by a crash are marked failed and can
be retried.

Cancellation and timeout are cooperative: both are checked before extraction,
after extraction, and whenever the PDF/EPUB extractor reports progress. A
single third-party parser call already in progress cannot be force-killed; it
will stop at the next checkpoint.

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
VIENEU_CHUNK_SIZE=500
```

The worker loads only VieNeu v3 Turbo INT8 and is enabled through the Compose
`vieneu` profile. Without that profile, the app runs only Edge TTS and gTTS.

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

Audio cache is server-side and shared by all users. The `cache_id` is generated
from the normalized text, voice, engine, language, and quality variant. If two
users submit the same request, they can reuse the same generated audio.

Cache metadata records every anonymous browser session that submitted the same
request. Streaming and downloads remain shareable by cache ID, while destructive
actions require the caller to be one of those owners. This preserves cache reuse
without allowing one browser to cancel or delete another browser's work.

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
- TTS cancellation and deletion require an owner recorded in cache metadata
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
docker compose --profile vieneu up -d --build --remove-orphans --wait
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
docker compose --profile vieneu up -d --build
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

## HTTP API Reference

FastAPI also exposes interactive OpenAPI documentation at `/docs`.

### General and Health

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Main browser application |
| `GET` | `/health` | Web readiness; returns `503` when Redis is unavailable |
| `GET` | `/document/health` | Document subsystem counts; diagnostic only |
| `GET` | `/tts/health` | VieNeu readiness based on the Redis worker heartbeat |

### TTS

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/tts/voices` | Supported language and voice registry |
| `POST` | `/tts/start` | Validate and start/reuse a synthesis request |
| `GET` | `/tts/status/{cache_id}` | Current queue/generation status |
| `GET` | `/tts/stream/{cache_id}` | Stream MP3 bytes while the file grows |
| `GET` | `/tts/file/{cache_id}` | Download completed audio |
| `GET` | `/tts/subtitle/srt/{cache_id}` | Download Edge TTS subtitles as SRT |
| `GET` | `/tts/subtitle/vtt/{cache_id}` | Download Edge TTS subtitles as WebVTT |
| `GET` | `/tts/cues/{cache_id}` | Read completed subtitle cues |
| `GET` | `/tts/cues/stream/{cache_id}` | Stream subtitle cues with SSE |
| `GET` | `/tts/session/{cache_id}` | Check whether a cached result still exists |
| `DELETE` | `/tts/file/{cache_id}` | Owner-only cancellation or deletion |
| `DELETE` | `/tts/cache` | Delete the whole cache; disabled by default |
| `POST` | `/tts/debug/chunks` | Debug chunk inspection; disabled by default |

`POST /tts/start` accepts:

```json
{
  "text": "Xin chào",
  "voice": "vi-VN-HoaiMyNeural",
  "engine": "edge",
  "language": "vi",
  "audio_quality": "standard",
  "model": null
}
```

`source_chapters` may replace `text`. Supported engines are `edge`, `gtts`, and
`vieneu`. VieNeu voice IDs start with `vieneu:`.

### Documents

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/document/upload/initiate` | Validate upload metadata and create a session |
| `POST` | `/document/upload/chunk` | Store one exact-sized multipart chunk |
| `POST` | `/document/upload/complete` | Assemble and validate the uploaded document |
| `GET` | `/document/queue` | List documents owned by this browser session |
| `GET` | `/document/{document_id}` | Read owned document metadata |
| `GET` | `/document/{document_id}/extract/stream` | Extract and stream chapter progress with SSE |
| `GET` | `/document/{document_id}/structure` | Read stored chapter structure |
| `POST` | `/document/{document_id}/content` | Read selected chapter text |
| `DELETE` | `/document/{document_id}` | Delete an owned document and its files |
| `POST` | `/document/job/{document_id}/extract` | Submit background extraction |
| `GET` | `/document/job/{job_id}/status` | Poll an owned extraction job |
| `GET` | `/document/job/{job_id}/result` | Read a completed extraction result |
| `POST` | `/document/job/{job_id}/retry` | Retry a failed extraction |
| `DELETE` | `/document/job/{job_id}` | Cooperatively cancel extraction |

Upload endpoints use `multipart/form-data`, not JSON. `initiate` requires
`filename`, `file_size`, and a 32-character lowercase/uppercase MD5 checksum.
Chunk numbering starts at zero.

## Configuration Reference

Values below are application defaults. Compose overrides paths and several
worker settings for containers.

### Web, Security, and Storage

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_VERSION` | `v4.0.0` | Version returned by `/health` |
| `APP_PORT` | `8000` | Host port published by Compose |
| `HOST` | `0.0.0.0` | Host used when executing `python main.py` |
| `PORT` | `8000` | Port used when executing `python main.py` |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection for limits and VieNeu queue |
| `PROXY` | unset | Optional outbound HTTP/HTTPS proxy |
| `TRUST_PROXY_HEADERS` | `false` | Trust the first `X-Forwarded-For` address |
| `SESSION_COOKIE_SECURE` | `false` | Send anonymous owner cookie over HTTPS only |
| `ENABLE_DEBUG_TTS` | `false` | Enable `/tts/debug/chunks` |
| `ENABLE_GLOBAL_CACHE_CLEAR` | `false` | Enable destructive `DELETE /tts/cache` |
| `DOCUMENT_STORAGE_PATH` | `/app/documents` | Upload and document root |
| `JOBS_DIR` | `./jobs` | Extraction job JSON directory |
| `AUDIO_CACHE_RETENTION_HOURS` | `12` | Completed/failed audio retention |

For public HTTPS deployment, set `SESSION_COOKIE_SECURE=true`. Enable
`TRUST_PROXY_HEADERS` only behind a proxy that overwrites, rather than appends
untrusted, forwarding headers.

### Request and Extraction Limits

| Variable | Default | Meaning |
| --- | --- | --- |
| `TTS_MAX_TEXT_LENGTH` | `100000` | Maximum normalized characters per TTS request |
| `VIENEU_MAX_WORDS` | `5000` | Additional VieNeu word limit |
| `TTS_STREAM_FIRST_BYTE_TIMEOUT_SECONDS` | `300` | Stop an empty live stream after this wait |
| `UPLOAD_MAX_SIZE_MB` | `50` | Maximum announced document size |
| `UPLOAD_CHUNK_SIZE` | `5242880` | Exact upload chunk size in bytes |
| `UPLOAD_SESSION_EXPIRY_HOURS` | `12` | Upload/document expiry window |
| `EXTRACTION_PAGE_BATCH` | `20` | Async PDF progress interval |
| `MAX_WORKERS` | `4` | Document extraction thread count; Compose uses 2 |
| `JOB_TIMEOUT_MINUTES` | `30` | Cooperative extraction timeout |
| `JOB_RETENTION_HOURS` | `24` | Extraction job metadata retention |

### VieNeu and Redis Queue

| Variable | Default | Meaning |
| --- | --- | --- |
| `VIENEU_INIT_IN_WEB` | `false` | Load VieNeu in FastAPI; keep false with worker service |
| `VIENEU_SAMPLE_RATE` | `48000` | Output sample rate |
| `VIENEU_CHUNK_SIZE` | `500` | Maximum VieNeu input characters per inference |
| `VIENEU_MAX_WORKERS` | `1` | Compatibility setting; current model pool is deliberately fixed at 1 |
| `VIENEU_WARMUP_ITERATIONS` | `1` | Model warm-up calls |
| `VIENEU_WARMUP_TEXT` | `Xin chào` | Warm-up input |
| `HF_TOKEN` | unset | Optional Hugging Face token |
| `TTS_MAX_QUEUE_SIZE` | `20` | Pending VieNeu queue cap |
| `TTS_QUEUE_KEY` | `ebook2audio:tts:vieneu:queue` | Redis pending list |
| `TTS_PROCESSING_KEY` | `ebook2audio:tts:vieneu:processing` | Redis reserved list |
| `TTS_CANCEL_PREFIX` | `ebook2audio:tts:cancel:` | Cancellation key prefix |
| `TTS_CANCEL_TTL_SECONDS` | `86400` | Cancellation marker lifetime |
| `TTS_WORKER_HEARTBEAT_KEY` | `ebook2audio:tts:vieneu:worker` | Readiness heartbeat key |
| `TTS_WORKER_HEARTBEAT_TTL_SECONDS` | `30` | Heartbeat expiry |
| `LOG_LEVEL` | `INFO` | VieNeu worker logging level |

VieNeu v3 Turbo INT8 is fixed in `vieneu_model.py`. This avoids downloading or
keeping multiple model variants in memory and prevents request-time reloads.

### VieNeu performance tuning

Start with `VIENEU_CHUNK_SIZE=500`. To tune for a specific CPU, convert the same
representative text three times with 350, 500, and 700, restarting the worker
after each `.env` change. Compare `estimated_seconds`, `started_at`, and
wall-clock completion time; keep the smallest chunk size that improves progress
latency without increasing total runtime.
Values below 300 usually spend too much time encoding many MP3 fragments, while
very large values delay progress and cancellation. Keep one worker and one
warm-up iteration unless measurements on a higher-memory host justify more.

## Deployment Runbook

1. Provision a Linux host with Docker Engine, Compose, sufficient disk, and at
   least 8 GB RAM when running VieNeu on CPU.
2. Copy `.env.example` to `.env`; set `SESSION_COOKIE_SECURE=true` for HTTPS.
3. Keep port 8000 behind a TLS reverse proxy. If enabling forwarded headers,
   ensure direct access to port 8000 is firewalled.
4. Start exactly one `app` replica and one `vieneu-worker` replica.
5. Deploy and wait for health checks:

   ```bash
   docker compose --profile vieneu up -d --build --remove-orphans --wait
   docker compose ps
   curl -fsS http://127.0.0.1:8000/health
   curl -fsS http://127.0.0.1:8000/tts/health
   ```

6. Verify logs and run one short Edge request plus one short VieNeu request.
7. Confirm named volumes exist:

   ```bash
   docker volume ls | grep ebook2audio
   ```

8. Back up `ebook2audio_cache`, `ebook2audio_documents`,
   `ebook2audio_jobs`, and Redis data according to the service's privacy and
   recovery requirements. The Hugging Face model volume is replaceable.

Do not scale the web or VieNeu service horizontally in this version. Web
generation locks and status maps are process-local, document extraction uses a
local thread pool, and the VieNeu recovery/heartbeat keys assume one worker.

## Operations and Troubleshooting

### Useful Commands

```bash
docker compose ps
docker compose logs -f app
docker compose logs -f vieneu-worker
docker compose logs -f redis
docker compose exec redis redis-cli LLEN ebook2audio:tts:vieneu:queue
docker compose exec redis redis-cli LLEN ebook2audio:tts:vieneu:processing
docker compose exec redis redis-cli INFO memory
```

### `/health` Returns 503

Redis is unavailable. Check the Redis container, `REDIS_URL`, network, and
memory/eviction logs. The web container is intentionally considered unready
without Redis because both rate limiting and VieNeu dispatch depend on it.

### `/tts/health` Returns 503

The worker heartbeat is absent. Initial model download and warm-up can take up
to the Compose health check's ten-minute start period. Inspect worker logs and
available RAM before restarting repeatedly.

### VieNeu Job Stays Queued

Confirm the worker is healthy, compare pending and processing list lengths, and
verify both app and worker mount the same `ebook2audio_cache` volume. On worker
restart, reserved jobs are returned to the pending queue.

### Documents Disappear or Return 404

Ownership is bound to the `story2audio_session` browser cookie. Clearing browser
cookies intentionally loses access to anonymous documents. After an app restart,
verify `documents/assembled/{id}/document.json` and the original uploaded file
both exist in the persistent documents volume.

### Upload Fails at Completion

The server checks exact chunk sizes, the full-file MD5, extension, and content
signature. Recompute the checksum over the original file and retry from
`/document/upload/initiate`; do not hash individual chunks.

## Known Constraints

- Anonymous cookie ownership is not an account system; access cannot be restored
  after cookie loss.
- In-progress uploads do not resume across a web-process restart; completed
  documents do.
- Audio URLs and non-destructive status endpoints are shareable by cache ID.
- Edge/gTTS work runs in the web process and is not durable across a hard crash.
- Document cancellation and timeout cannot interrupt a parser call until it
  reaches the next progress checkpoint.
- Redis queue capacity enforcement assumes one web process.
- The current service topology supports one app and one VieNeu worker replica.
- PDF/EPUB parsing is CPU/memory intensive; the 50 MB upload cap is not
  a guarantee of cheap processing.
