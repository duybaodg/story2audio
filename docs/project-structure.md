# Ebook2Audio Technical and Operations Guide

This document describes the current Ebook2Audio v4 codebase, its supported
runtime topology, persistent data, configuration, deployment, and common
failure modes.

## Architecture

```text
Browser
  └─ HTTP/S → FastAPI app
                ├─ UI and document API
                ├─ Edge TTS and gTTS
                ├─ Redis rate limits
                └─ Redis VieNeu queue
                         ↓
                  VieNeu worker
                         └─ shared audio cache
```

The supported production topology has three services:

| Service | Responsibility |
| --- | --- |
| `app` | FastAPI, browser UI, uploads, extraction, Edge TTS, gTTS, and VieNeu job submission |
| `vieneu-worker` | One dedicated VieNeu model process consuming Redis jobs |
| `redis` | Request rate limits, VieNeu pending/processing queues, cancellation, and worker heartbeat |

Run one app replica and one VieNeu worker. Several locks and status maps are
process-local, while the VieNeu heartbeat and recovery keys assume one worker.

## Source Layout

```text
story2audio/
├── main.py                       # FastAPI application and TTS orchestration
├── document_api.py               # Document routes and ownership checks
├── file_processor.py             # Chunked uploads, persistence, and cleanup
├── text_extractor.py             # PDF/EPUB text and chapter extraction
├── job_queue.py                  # File-backed extraction jobs
├── rate_limiter.py               # Redis-backed request limits
├── tts_queue.py                  # Redis VieNeu queue primitives
├── tts_worker.py                 # Dedicated VieNeu worker
├── vieneu_model.py               # VieNeu model lifecycle and voice presets
├── vieneu_audio_quality.py       # Audio post-processing
├── vietnamese_text_processor.py  # Vietnamese normalization and chunking
├── models/
│   ├── document.py               # Document domain models
│   └── vieneu/assets/voices.json # VieNeu voice registry
├── templates/index.html          # Browser application shell
├── static/                       # JavaScript, CSS, and favicon
├── tests/                        # Pytest regression tests
├── Dockerfile                    # Multi-stage production image
├── docker-compose.yml            # Local/production service topology
├── pyproject.toml                # Direct dependencies
└── uv.lock                       # Locked dependency graph
```

Generated `audio_cache/`, `documents/`, and `jobs/` directories are runtime
data. They are excluded from Git and the Docker build context.

## Runtime Flows

### Text to speech

Edge TTS and gTTS execute inside the web process:

```text
POST /tts/start
  → validate and normalize request
  → reuse cache or schedule generation
  → write audio/metadata to /app/audio_cache
  → stream, poll, or download by cache_id
```

VieNeu executes in the separate worker:

```text
POST /tts/start
  → write queued metadata
  → enqueue a Redis job
  → worker reserves the job
  → VieNeu generates and encodes audio
  → worker writes shared cache files
  → worker acknowledges the job
```

On worker restart, jobs left in the processing list return to the pending
queue. Cancellation uses expiring Redis keys. A worker heartbeat backs
`/tts/health`.

### Document processing

```text
POST /document/upload/initiate
  → validate filename, extension, size, and MD5 format
  → create an owner-bound upload session

POST /document/upload/chunk
  → verify session ownership and exact chunk size
  → write the numbered chunk

POST /document/upload/complete
  → assemble chunks
  → verify checksum and PDF/EPUB signature
  → persist document metadata

POST /document/job/{document_id}/extract
  → verify ownership
  → run extraction in the local thread pool
  → persist chapters and final status
```

Documents belong to the anonymous HTTP-only browser session that uploaded
them. Other sessions receive `404`. Completed document metadata is recovered
after restart; incomplete upload sessions are not.

## Docker Image

The image uses a builder and a smaller runtime stage:

- Python 3.13 slim base
- locked production dependencies installed by pinned `uv`
- compilers present only in the builder
- FFmpeg present in the runtime image
- only Python source, `static/`, `templates/`, and `models/` copied at runtime
- unprivileged UID/GID `10001` used for both app and worker
- built-in `/health` check for the default web command

Compose builds the image once as `ebook2audio:${APP_VERSION}` and both app and
worker reuse it. The worker overrides the command and health check.

## Persistent Paths

| Container path | Compose volume | Contents |
| --- | --- | --- |
| `/app/audio_cache` | `ebook2audio_cache` | Generated audio, metadata, and subtitles |
| `/app/documents` | `ebook2audio_documents` | Upload chunks, assembled files, and document metadata |
| `/app/jobs` | `ebook2audio_jobs` | Extraction job JSON |
| `/home/app/.cache/huggingface` | `ebook2audio_models` | Replaceable VieNeu model downloads |
| `/data` in Redis | `ebook2audio_redis` | Queue, rate-limit, and heartbeat state |

All application paths are writable by the non-root `app` user. Existing
volumes created by an older root-running image may require a one-time ownership
migration before using the new image.

## Configuration

Copy `.env.example` to `.env`. Never commit `.env` or real tokens.

### Required production decisions

| Variable | Recommended value | Notes |
| --- | --- | --- |
| `VIENEU_INIT_IN_WEB` | `false` | Worker owns the heavy model |
| `VIENEU_MAX_WORKERS` | `1` | Current model pool is single-instance |
| `ENABLE_DEBUG_TTS` | `false` | Keeps debug endpoint unavailable |
| `ENABLE_GLOBAL_CACHE_CLEAR` | `false` | Keeps global destructive route unavailable |
| `SESSION_SECRET` | random 32+ character secret | Signs browser ownership sessions; keep it only in the server `.env` |
| `SESSION_COOKIE_SECURE` | `true` with HTTPS | Protects the anonymous ownership cookie |
| `TRUST_PROXY_HEADERS` | `true` only behind a trusted proxy | Proxy must overwrite forwarded headers and port 8000 must not be public |
| `HF_TOKEN` | optional secret | Use a deployment secret, not an image build argument |

### Capacity and retention

| Variable | Default/Compose value | Purpose |
| --- | --- | --- |
| `TTS_MAX_TEXT_LENGTH` | `100000` | Maximum normalized characters per request |
| `VIENEU_MAX_WORDS` | `5000` | VieNeu-specific word limit |
| `TTS_MAX_QUEUE_SIZE` | `20` | Maximum pending VieNeu jobs |
| `LOCAL_TTS_MAX_CONCURRENT` | `2` | Maximum concurrent Edge/gTTS jobs |
| `LOCAL_TTS_JOB_TIMEOUT_SECONDS` | `900` | Whole-job Edge/gTTS deadline |
| `UPLOAD_MAX_SIZE_MB` | `50` | Maximum document upload size |
| `EXTRACTED_TEXT_MAX_CHARS` | `2000000` | Maximum retained extracted text per document |
| `MAX_WORKERS` | Compose: `2` | Document extraction threads |
| `JOB_TIMEOUT_MINUTES` | `30` | Cooperative extraction timeout |
| `JOB_RETENTION_HOURS` | `24` | Extraction metadata retention |
| `UPLOAD_SESSION_EXPIRY_HOURS` | `12` | Upload/document expiry window |
| `AUDIO_CACHE_RETENTION_HOURS` | `12` | Audio cache retention |

## API Summary

Interactive OpenAPI documentation is available at `/docs`.

| Area | Important endpoints |
| --- | --- |
| General | `GET /`, `GET /health` |
| TTS | `POST /tts/start`, `GET /tts/status/{cache_id}`, `GET /tts/stream/{cache_id}`, `GET /tts/file/{cache_id}` |
| Subtitles | `GET /tts/subtitle/srt/{cache_id}`, `GET /tts/subtitle/vtt/{cache_id}`, `GET /tts/cues/stream/{cache_id}` |
| TTS lifecycle | `GET /tts/health`, `DELETE /tts/file/{cache_id}` |
| Upload | `POST /document/upload/initiate`, `/chunk`, `/complete` |
| Documents | `GET /document/queue`, `GET/DELETE /document/{document_id}`, `POST /document/{document_id}/content` |
| Extraction | `GET /document/{document_id}/extract/stream`, `POST /document/job/{document_id}/extract`, job status/result/retry/cancel routes |

`DELETE /tts/cache` and `/tts/debug/chunks` are disabled by default.

## Local Development

Python 3.13, Redis, and FFmpeg are required.

```bash
cp .env.example .env
uv sync --locked
docker compose up -d redis
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Run the worker separately when testing VieNeu:

```bash
REDIS_URL=redis://localhost:6379 uv run python tts_worker.py
```

## Deployment Runbook

1. Provision Docker Engine and Compose. VieNeu CPU deployments should start
   with at least 4 vCPU, 8 GB RAM, and 30–40 GB SSD.
2. Create `.env`, inject secrets, and set the HTTPS/proxy flags correctly.
3. Keep port 8000 private behind the TLS reverse proxy.
4. Build and start the three services:

   ```bash
   docker compose --profile vieneu up -d --build --remove-orphans --wait
   ```

5. Verify readiness and logs:

   ```bash
   docker compose --profile vieneu ps
   curl -fsS http://127.0.0.1:8000/health
   curl -fsS http://127.0.0.1:8000/tts/health
   docker compose --profile vieneu logs --tail=200
   ```

6. Run one short Edge request and one short VieNeu request.
7. Confirm backups for private document/audio data and Redis. The model cache
   is replaceable and normally does not need backup.

To run only Edge TTS and gTTS, omit `--profile vieneu`.

## Verification Before Release

```bash
uv lock --check
uv run pytest -q
python -m py_compile main.py document_api.py file_processor.py text_extractor.py \
  job_queue.py tts_queue.py tts_worker.py rate_limiter.py vieneu_model.py \
  vieneu_audio_quality.py vietnamese_text_processor.py
node --check static/app.js
docker compose --profile vieneu config --quiet
docker compose --profile vieneu build app
```

## Troubleshooting

### App restarts immediately

Inspect the first traceback rather than later restart messages:

```bash
docker compose logs app --tail=200
```

`ModuleNotFoundError` generally means a required source package was not copied
into the runtime image. Rebuild without stale containers after correcting the
Dockerfile.

### `/health` returns 503

Redis is unavailable. Check `REDIS_URL`, Redis health, the Compose network, and
Redis memory logs.

### `/tts/health` returns 503

The VieNeu heartbeat is absent. Initial model download and warm-up may take
several minutes. Inspect worker logs and available memory.

### Permission denied after upgrading the image

The current image runs as UID/GID `10001`. If named volumes already contain
root-owned files from an older image, migrate their ownership once or recreate
only replaceable volumes. Do not delete document/audio volumes without a
backup.

### VieNeu job remains queued

Check worker health, pending and processing counts, and the shared cache mount:

```bash
docker compose exec redis redis-cli LLEN ebook2audio:tts:vieneu:queue
docker compose exec redis redis-cli LLEN ebook2audio:tts:vieneu:processing
```

### Document returns 404

Ownership is bound to the `story2audio_session` browser cookie. Clearing that
cookie loses access to anonymous documents even if their files still exist.

## Known Constraints

- Anonymous sessions are not durable user accounts.
- Edge/gTTS work is not durable across a hard web-process crash.
- Parsing a valid 50 MB PDF/EPUB can still consume significant CPU and memory.
- Parser calls stop only at cooperative cancellation/timeout checkpoints.
- Audio/status URLs remain shareable by cache ID; destructive actions require ownership.
- Horizontal web or VieNeu scaling is not supported by the current coordination model.
