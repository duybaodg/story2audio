# Versioning and Azure Deployment Guide

This document describes how to version Story2Audio releases and what to consider before deploying the FastAPI, Redis, and VieNeu worker architecture to Azure.

For an executable step-by-step runbook, see [`azure-deploy-plan.md`](azure-deploy-plan.md).

## Version Management

Use three linked versions for every deployable release:

1. App version in `pyproject.toml`
2. Git release tag
3. Docker image tag

Example release:

```toml
[project]
version = "4.0.0"
```

```bash
git tag v4.0.0
git push origin v4.0.0
```

```bash
docker build -t story2audio:v4.0.0 .
docker tag story2audio:v4.0.0 story2audio:latest
```

Use semantic versioning:

| Version type | Example | Use when |
| --- | --- | --- |
| Major | `4.0.0` | Breaking API, cache, storage, or deployment change |
| Minor | `4.1.0` | New compatible feature, no breaking deployment or API change |
| Patch | `4.0.1` | Bug fix, small config/doc update, no behavior break |

For deployment, also set:

```env
APP_VERSION=v4.0.0
```

The app reads `APP_VERSION`, and `/health` returns the active deployed version.

## Recommended Azure Architecture

Recommended first production shape:

```text
Azure Container Registry
  -> story2audio:v4.0.0

Azure Container Apps
  -> story2audio-app
  -> story2audio-vieneu-worker

Azure Managed Redis or Azure Cache for Redis
  -> upload rate limiting
  -> VieNeu TTS queue
  -> cancellation flags

Azure Files
  -> /app/audio_cache
  -> /app/documents
  -> /app/jobs
  -> /root/.cache/huggingface

Log Analytics
  -> app logs
  -> worker logs

Container App secrets or Key Vault
  -> REDIS_URL
  -> HF_TOKEN
  -> PROXY
```

Azure Container Apps is a good fit because this project has one HTTP app and one background worker. It supports HTTP APIs, background processing, scale rules, revisions, HTTPS ingress, registry images, secrets, and Log Analytics.

Reference:
- Azure Container Apps overview: <https://learn.microsoft.com/en-us/azure/container-apps/overview>
- Azure Container Registry overview: <https://learn.microsoft.com/en-us/azure/container-registry/container-registry-intro>

## Service Responsibilities

### `story2audio-app`

Runs FastAPI, static UI, document upload APIs, status APIs, and cached audio downloads.

Recommended settings:

```env
VIENEU_INIT_IN_WEB=false
REDIS_URL=<managed-redis-url>
TTS_STREAM_FIRST_BYTE_TIMEOUT_SECONDS=300
MAX_WORKERS=1
```

The web app should not load the VieNeu model. That keeps the public API responsive and reduces memory pressure.

### `story2audio-vieneu-worker`

Runs `python tts_worker.py`, warms the VieNeu model, pulls jobs from Redis, writes audio and metadata to shared storage, and processes one VieNeu job at a time.

Recommended settings:

```env
VIENEU_MAX_WORKERS=1
VIENEU_MODE=v3_turbo
VIENEU_SAMPLE_RATE=48000
REDIS_URL=<managed-redis-url>
HF_TOKEN=<optional-huggingface-token>
```

Keep one worker replica at first. Add more worker replicas only if each replica has enough CPU/RAM for its own model instance.

### Redis

Redis is now used for two things:

```text
upload rate limiting
VieNeu TTS job queue
```

VieNeu request flow:

```text
POST /tts/start
-> FastAPI writes queued metadata
-> FastAPI pushes VieNeu job to Redis
-> worker reserves one job
-> worker runs VieNeu
-> worker writes audio to /app/audio_cache
-> frontend polls /tts/status/{cache_id}
-> frontend streams/downloads completed audio
```

Prefer Azure Managed Redis if it is available in your target region. If not, use Azure Cache for Redis and plan migration if needed.

Reference:
- Azure Cache for Redis overview: <https://learn.microsoft.com/en-us/azure/azure-cache-for-redis/cache-overview>

## Persistent Storage

Do not rely on container-local storage for this app. Generated audio, uploaded documents, job metadata, and model cache need persistence.

Mount Azure Files to:

```text
/app/audio_cache
/app/documents
/app/jobs
/root/.cache/huggingface
```

Why this matters:

- `app` and `vieneu-worker` both need to see generated audio and metadata.
- Restarting the worker should not redownload VieNeu models every time.
- Restarting the app should not delete completed audio.

Azure Container Apps supports Azure Files mounts for persistent data and cross-container access.

Reference:
- Azure Container Apps storage mounts: <https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts>

## Sizing

Minimum practical Azure sizing:

| Component | CPU | Memory | Notes |
| --- | ---: | ---: | --- |
| `story2audio-app` | 1 vCPU | 2 GiB | HTTP API, UI, status, cached downloads |
| `story2audio-vieneu-worker` | 2-4 vCPU | 6-8 GiB | CPU VieNeu, one job at a time |
| Redis | Small managed tier | Provider managed | Queue and rate limits |
| Azure Files | 50 GiB+ | N/A | Audio, docs, jobs, model cache |

Cheap deployment profile:

```env
MAX_WORKERS=1
VIENEU_INIT_IN_WEB=false
VIENEU_MAX_WORKERS=1
VIENEU_MODE=v3_turbo
VIENEU_SAMPLE_RATE=48000
TTS_STREAM_FIRST_BYTE_TIMEOUT_SECONDS=300
```

Expected concurrency on this profile:

```text
1 active VieNeu generation at a time
multiple users browsing UI/status/cached downloads
some Edge/gTTS requests depending on CPU/network
```

## Scaling Guidance

Scale the web app separately from the VieNeu worker.

Recommended starting point:

```text
app min replicas: 1
app max replicas: 2-3
worker min replicas: 1
worker max replicas: 1
```

Only increase worker replicas after validating memory usage. Each worker can load its own model instance, so two workers can roughly double memory pressure.

For larger deployments:

```text
app max replicas: scale by HTTP traffic
worker max replicas: scale by Redis queue length
Redis: managed tier with enough memory/connections
storage: monitor IOPS and latency
```

## Secrets

Store these in Container Apps secrets or Key Vault:

```env
REDIS_URL
HF_TOKEN
PROXY
```

Do not put secrets in:

```text
Dockerfile
docker-compose.yml committed values
GitHub repository
image tags
README examples with real tokens
```

## Deployment Flow

Suggested release flow:

```bash
# 1. Update app version
# pyproject.toml -> version = "4.0.0"

# 2. Run tests
python -m py_compile main.py tts_queue.py tts_worker.py
pytest

# 3. Tag release
git tag v4.0.0
git push origin v4.0.0

# 4. Build and push image to Azure Container Registry
docker build -t <acr-name>.azurecr.io/story2audio:v4.0.0 .
docker push <acr-name>.azurecr.io/story2audio:v4.0.0

# 5. Deploy or update Container Apps
# - story2audio-app uses uvicorn command from Dockerfile
# - story2audio-vieneu-worker uses: python tts_worker.py
```

## Runtime Checks

After deployment, verify:

```bash
curl https://<app-url>/health
curl https://<app-url>/tts/health
```

Check app logs for:

```text
VieNeu model initialization skipped in web process
Rate limiter initialized
```

Check worker logs for:

```text
Initializing VieNeu model in worker process
VieNeu worker ready
Starting VieNeu job ...
Finished VieNeu job ...
```

## Production Checklist

- [ ] `APP_VERSION` set to the release tag.
- [ ] Docker image pushed with immutable tag, for example `v4.0.0`.
- [ ] Web app has `VIENEU_INIT_IN_WEB=false`.
- [ ] Worker runs `python tts_worker.py`.
- [ ] Worker replica count starts at `1`.
- [ ] Redis URL configured as a secret.
- [ ] Hugging Face token configured as a secret if used.
- [ ] Azure Files mounted for audio, documents, jobs, and model cache.
- [ ] `/health` returns the expected version.
- [ ] Worker logs show `VieNeu worker ready`.
- [ ] A small VieNeu request completes end to end.
