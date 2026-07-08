# Story2Audio - Project Context

Vietnamese text-to-speech application with live streaming, document upload, and multi-engine TTS support.

## Quick Start

```bash
# Install dependencies (requires uv)
uv sync

# Run development server
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Run tests
uv run pytest

# Docker deployment
docker compose up -d --build
```

## Tech Stack

**Backend**: FastAPI, Python 3.13, Redis (rate limiting)
**TTS Engines**: Edge TTS (Microsoft), gTTS, VieNeu (Vietnamese)
**Audio**: pydub for MP3/WAV processing, ffmpeg
**Storage**: Local file cache (audio_cache/, documents/)
**Deployment**: Docker, Docker Compose

## Architecture

```
main.py                    # FastAPI app entry point
├── document_api.py        # Document upload/extraction (PDF/EPUB)
├── file_processor.py      # Chunked upload, session management
├── job_queue.py           # Background job queue
├── text_extractor.py      # PDF/EPUB text extraction
├── vieneu_model.py        # VieNeu TTS model pool manager
├── vieneu_audio_quality.py # Audio post-processing
└── rate_limiter.py        # Redis-based rate limiting
```

## Key Modules

- **vieneu_model.py**: Thread-safe model pool with per-model locking. llama.cpp isn't thread-safe, so pool manages sequential processing. Already supports remote mode via `VIENEU_MODE=remote`.
- **vieneu_audio_quality.py**: Audio post-processing—crossfade, normalization, MP3 encoding. Uses 24kHz mono format specific to VieNeu.
- **document_api.py**: Chunked upload (5MB chunks), OCR fallback, chapter detection.
- **job_queue.py**: In-memory job queue for async document processing.

## Environment Variables

```bash
# VieNeu Configuration
VIENEU_MODE=standard|remote           # default: standard
VIENEU_REMOTE_API_BASE=http://localhost:23333/v1  # for remote mode
HF_TOKEN                              # Hugging Face token for model downloads

# App Configuration
HOST=0.0.0.0
PORT=8000
REDIS_URL=redis://localhost:6379      # Required for rate limiting
PROXY=http://...                      # Optional proxy
```

## Code Style

- Async/await for I/O operations
- Type hints with `from typing import ...`
- Logging via `logging.getLogger("story2audio")`
- Context managers for resource management

## Gotchas

1. **llama.cpp thread safety**: VieNeu uses llama.cpp which isn't thread-safe. Model pool uses locks per instance. Always use `with get_vieneu_model() as model:` context manager.
2. **MP3 concatenation**: VBR encoding causes playback issues. Uses CBR encoding in `encode_mp3_cbr()`.
3. **Session persistence**: `window.currentCacheId` vs lexical variable mismatch—session restoration has bugs.
4. **Test collection**: `tests/test_job_queue.py` has syntax error—`await` in non-async function.
5. **Cleanup scheduler**: Document cleanup registered but never actually scheduled (uses local `BackgroundTasks`).

## VieNeu Architecture Decision

**Current**: Model embedded in FastAPI app (appropriate for current scale)

**When to separate as API service**:
- Pool size increases to 4+ workers (currently 1)
- Need GPU acceleration (local hardware insufficient)
- Multiple main app instances needed
- Multiple applications need to share TTS service

**Existing infrastructure**: `VIENEU_MODE=remote` config exists but HTTP client not implemented. Migration would be ~2-3 days including:
- Extracting model pool to separate service
- Implementing HTTP client wrapper
- Adding circuit breaker for fault tolerance
- Health checks and graceful degradation

## Known Issues (from PROJECT_REVIEW_FINDINGS.md)

**Critical**:
- `test_cancel_job()` uses `await` in non-async function—blocks test collection
- `/tts/session/{cache_id}` raises TypeError for valid cache IDs
- Edge subtitle cues are duplicated
- Repeat `/tts/start` calls can corrupt active generation

**High**:
- Document cleanup scheduler doesn't run
- VieNeu audio quality selector has no backend effect
- Frontend session persistence mostly non-functional

## Testing

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_vieneu_reliability.py

# Run with coverage
uv run pytest --cov=. --cov-report=html
```

## Deployment

**Docker Compose** (recommended):
- Services: app + redis
- Volumes: audio_cache, documents, models (HuggingFace cache)
- Health checks on both services
- Resource limits available (uncomment for production)

**Manual**:
- Requires ffmpeg, tesseract (OCR), poppler (PDF)
- Python 3.13+
- Redis for rate limiting
