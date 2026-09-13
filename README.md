# Ebook2Audio 🎧

Ebook2Audio is a FastAPI application that converts text, PDF, and EPUB content into audio. It supports Edge TTS, gTTS, and a dedicated VieNeu worker for Vietnamese speech generation.

## Features

- Edge TTS and gTTS for Vietnamese, English, Japanese, Chinese, Korean, French, and German.
- VieNeu-TTS v3 Turbo INT8 for Vietnamese voices.
- Live audio generation with progress tracking and cancellation.
- SRT and WebVTT subtitles for Edge TTS.
- Chunked PDF and EPUB uploads with chapter selection.
- Persistent audio cache, document storage, jobs, model cache, and Redis data.
- Session-scoped access to uploaded documents and generated audio.

## Run with Docker Compose

Requirements: Docker Engine with Docker Compose and enough memory to load the VieNeu model when that profile is enabled.

```bash
git clone https://github.com/duybaodg/story2audio.git
cd story2audio
cp .env.example .env
docker compose --profile vieneu up -d --build --wait
```

Open `http://SERVER_IP:8000`. Change `APP_PORT` in `.env` if port `8000` is unavailable.

The stack contains:

- `app`: FastAPI UI and API.
- `redis`: rate limiting and the VieNeu job queue.
- `vieneu-worker`: one sequential VieNeu model worker, enabled by the `vieneu` profile.

For Edge TTS and gTTS without the VieNeu worker:

```bash
docker compose up -d --build --wait
```

Useful commands:

```bash
docker compose --profile vieneu ps
docker compose --profile vieneu logs -f
docker compose --profile vieneu down
```

The included deployment binds the application to localhost through `APP_PORT` and does not configure HTTPS. If the service is exposed publicly, configure Nginx and TLS separately and set `SESSION_COOKIE_SECURE=true`.

## Configuration

Copy `.env.example` to `.env` and change only the values needed for the server.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_PORT` | `127.0.0.1:8000` | Loopback host address and port mapped to the application. |
| `APP_VERSION` | `v4.0.0` | Local Docker image tag and reported application version. |
| `SESSION_SECRET` | required with HTTPS | Server-only secret used to sign browser sessions. |
| `TTS_MAX_TEXT_LENGTH` | `100000` | Maximum characters per TTS request. |
| `VIENEU_MAX_WORDS` | `5000` | Maximum words per VieNeu request. |
| `TTS_MAX_QUEUE_SIZE` | `20` | Maximum queued VieNeu jobs. |
| `LOCAL_TTS_MAX_CONCURRENT` | `2` | Maximum simultaneous Edge/gTTS jobs. |
| `LOCAL_TTS_JOB_TIMEOUT_SECONDS` | `900` | Whole-job Edge/gTTS deadline. |
| `UPLOAD_MAX_SIZE_MB` | `50` | Maximum PDF or EPUB upload size. |
| `EXTRACTED_TEXT_MAX_CHARS` | `2000000` | Maximum extracted text retained per document. |
| `VIENEU_CHUNK_SIZE` | `500` | Target VieNeu text chunk size. |
| `VIENEU_WARMUP_ITERATIONS` | `1` | Model warmup iterations. |
| `VIENEU_SAMPLE_RATE` | `48000` | VieNeu output sample rate. |
| `HF_TOKEN` | empty | Optional Hugging Face access token. |
| `PROXY` | empty | Optional outbound HTTP proxy. |
| `TRUST_PROXY_HEADERS` | `false` | Trust forwarded client IP headers only when supplied by trusted infrastructure. |
| `SESSION_COOKIE_SECURE` | `false` | Send session cookies only over HTTPS. |
| `ENABLE_GLOBAL_CACHE_CLEAR` | `false` | Enable the global cache-clear endpoint. Keep disabled normally. |

## GitHub Actions deployment

[`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml) runs tests and builds the Docker image for pull requests. A successful push to `main` deploys the exact tested commit to the Ubuntu server with Docker Compose.

See [`UBUNTU_DEPLOYMENT.md`](UBUNTU_DEPLOYMENT.md) for the complete server setup, SSH key, GitHub environment, verification, troubleshooting, and rollback steps.

Prepare the Ubuntu server once:

```bash
git clone https://github.com/duybaodg/story2audio.git /opt/ebook2audio
cd /opt/ebook2audio
cp .env.example .env
docker compose --profile vieneu config --quiet
```

The SSH user must have access to the repository and permission to run Docker. Configure these values in the GitHub `production` environment:

| Type | Name | Value |
| --- | --- | --- |
| Secret | `VPS_HOST` | Ubuntu server hostname or IP address. |
| Secret | `VPS_USER` | SSH deployment user. |
| Secret | `VPS_SSH_KEY` | Private key whose public key is authorized on the server. |
| Secret | `VPS_KNOWN_HOSTS` | Verified SSH host-key entry for the server. |
| Variable | `VPS_PATH` | Repository path, for example `/opt/ebook2audio`. |
| Variable | `VPS_PORT` | SSH port; defaults to `22`. |

After the values are configured, merge or push to `main`. The workflow checks out the tested commit on the server and runs:

```bash
docker compose --profile vieneu up -d --build --remove-orphans --wait
```

## Local development

Requirements: Python 3.13, `uv`, Redis, and Node.js for the JavaScript syntax check.

```bash
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Run the VieNeu worker separately when needed:

```bash
uv run python tts_worker.py
```

API documentation is available at `/docs`. Health endpoints are `/health` for the web/Redis stack and `/tts/health` for the VieNeu worker.

## Checks

```bash
uv run pytest
uv run python -m py_compile main.py tts_queue.py tts_worker.py
node --check static/app.js
docker compose --profile vieneu config --quiet
```

## License and attribution

The project is distributed under the MIT License. See [`LICENSE`](LICENSE).

This repository is based on [`dvchd/story2audio`](https://github.com/dvchd/story2audio). Vietnamese speech generation uses [`pnnbao97/VieNeu-TTS`](https://github.com/pnnbao97/VieNeu-TTS) and [`pnnbao-ump/VieNeu-TTS-v3-Turbo`](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo).
