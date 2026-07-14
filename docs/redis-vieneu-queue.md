# Redis and VieNeu Queue

This document explains how Redis works with VieNeu-TTS in Ebook2Audio.

## Why Redis Is Used

VieNeu-TTS is the heaviest engine in this project. It loads a local model, uses significant CPU/RAM, and should not run inside the public FastAPI process on small servers.

Redis is used to separate the web app from the heavy TTS worker:

```text
FastAPI app
  -> accepts user request
  -> validates text/voice
  -> writes queued metadata to /app/audio_cache
  -> pushes a VieNeu job to Redis

VieNeu worker
  -> waits for Redis jobs
  -> reserves one job
  -> runs VieNeu-TTS
  -> writes audio and status metadata
```

This keeps the web app responsive even when VieNeu generation is slow.

## Runtime Components

### `app`

Defined in `docker-compose.yml`.

Responsibilities:

- serves the UI
- handles `/tts/start`
- handles `/tts/status/{cache_id}`
- handles `/tts/stream/{cache_id}`
- handles `/tts/file/{cache_id}`
- handles upload APIs
- rate-limits upload initiation through Redis
- enqueues VieNeu jobs into Redis

Important setting:

```env
VIENEU_INIT_IN_WEB=false
```

The web process should not load the VieNeu model in production.

### `vieneu-worker`

Defined in `docker-compose.yml`.

Responsibilities:

- runs `python tts_worker.py`
- initializes the VieNeu model once
- pulls one Redis job at a time
- calls the existing TTS generation code
- writes generated audio to `/app/audio_cache`
- writes status metadata to `/app/audio_cache/{cache_id}.json`

Important settings:

```env
VIENEU_MAX_WORKERS=1
MAX_WORKERS=1
```

Keep one worker at first. Each worker can load its own model instance.

### `redis`

Responsibilities:

- upload rate limiting
- VieNeu TTS queue
- VieNeu in-flight job recovery
- cancellation flags

## Redis Keys

Defined in `tts_queue.py`.

| Key | Default | Purpose |
| --- | --- | --- |
| Queue list | `ebook2audio:tts:vieneu:queue` | Pending VieNeu jobs |
| Processing list | `ebook2audio:tts:vieneu:processing` | Job currently reserved by a worker |
| Cancel prefix | `ebook2audio:tts:cancel:` | Cancellation flags by `cache_id` |

Override keys with:

```env
TTS_QUEUE_KEY=ebook2audio:tts:vieneu:queue
TTS_PROCESSING_KEY=ebook2audio:tts:vieneu:processing
TTS_CANCEL_PREFIX=ebook2audio:tts:cancel:
TTS_CANCEL_TTL_SECONDS=86400
```

## Job Payload

FastAPI enqueues a JSON object like this:

```json
{
  "text": "Xin chao",
  "voice": "vieneu:default",
  "engine": "vieneu",
  "cache_id": "f505c750500460bcab34a908b74671a1",
  "language": "vi",
  "chunks": ["Xin chao"],
  "audio_quality": "standard"
}
```

The `cache_id` is the shared identifier used by:

- Redis job payload
- audio file path
- metadata file path
- stream endpoint
- status endpoint

## Request Flow

1. User selects `VieNeu-TTS` and clicks convert.
2. Browser calls:

```text
POST /tts/start
```

3. FastAPI validates:

```text
text
voice
engine
language
audio_quality
```

4. FastAPI computes `cache_id`.
5. FastAPI writes queued metadata:

```text
/app/audio_cache/{cache_id}.json
```

6. FastAPI pushes the serialized job to Redis:

```text
LPUSH ebook2audio:tts:vieneu:queue <job-json>
```

7. Worker reserves the job atomically:

```text
BRPOPLPUSH ebook2audio:tts:vieneu:queue ebook2audio:tts:vieneu:processing
```

8. Worker runs VieNeu.
9. Worker appends/writes audio:

```text
/app/audio_cache/{cache_id}.mp3
```

10. Worker updates metadata:

```text
queued -> processing -> generating -> completed
```

11. Worker acknowledges the job:

```text
LREM ebook2audio:tts:vieneu:processing 1 <job-json>
```

12. Browser streams or downloads the final audio.

## Status Flow

The frontend does not read Redis directly.

It calls:

```text
GET /tts/status/{cache_id}
GET /tts/stream/{cache_id}
GET /tts/file/{cache_id}
```

FastAPI reads:

```text
/app/audio_cache/{cache_id}.json
/app/audio_cache/{cache_id}.mp3
```

This is why `app` and `vieneu-worker` must share the same audio cache volume.

## Cancellation Flow

When the user deletes/cancels an active VieNeu job:

```text
DELETE /tts/file/{cache_id}
```

FastAPI writes a Redis cancellation flag:

```text
SETEX ebook2audio:tts:cancel:{cache_id} 86400 1
```

The worker checks this flag before processing the job. The shared generation code also checks cancellation between chunks.

Important limitation:

- cancellation may not interrupt the exact model inference call already running
- cancellation is reliable at job boundaries and chunk boundaries

## Recovery Flow

If the worker crashes after reserving a job, the job may remain in:

```text
ebook2audio:tts:vieneu:processing
```

On worker startup, `recover_processing_jobs()` moves in-flight jobs back to:

```text
ebook2audio:tts:vieneu:queue
```

This prevents jobs from being permanently stuck after a worker restart.

## Failure Flow

If VieNeu generation fails:

1. Worker catches the exception.
2. Worker writes failed metadata:

```json
{
  "status": "failed",
  "error": "RuntimeError: ..."
}
```

3. Worker acknowledges the Redis job so it is not retried forever.
4. Frontend sees failure through `/tts/status/{cache_id}`.

## Shared Storage Requirement

Redis stores only queue state. It does not store generated audio.

These paths must be shared between `app` and `vieneu-worker`:

```text
/app/audio_cache
/app/documents
/app/jobs
/root/.cache/huggingface
```

In Docker Compose these are named volumes.

In Azure, mount Azure Files to the same paths.

## Operational Commands

View Redis queue length in Docker:

```bash
docker compose exec redis redis-cli LLEN ebook2audio:tts:vieneu:queue
docker compose exec redis redis-cli LLEN ebook2audio:tts:vieneu:processing
```

Inspect pending jobs:

```bash
docker compose exec redis redis-cli LRANGE ebook2audio:tts:vieneu:queue 0 5
```

Inspect processing jobs:

```bash
docker compose exec redis redis-cli LRANGE ebook2audio:tts:vieneu:processing 0 5
```

Watch worker logs:

```bash
docker compose logs -f vieneu-worker
```

Watch web logs:

```bash
docker compose logs -f app
```

## Production Guidance

Start with:

```text
app replicas: 1-2
vieneu-worker replicas: 1
VIENEU_MAX_WORKERS=1
```

Increase worker replicas only after validating memory usage. More replicas mean more model instances and more RAM pressure.

For Azure deployment details, see:

- [`azure-deployment.md`](azure-deployment.md)
- [`azure-deploy-plan.md`](azure-deploy-plan.md)
