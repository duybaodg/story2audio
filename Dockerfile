FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    cmake \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.12.5

COPY pyproject.toml README.md ./
COPY uv.lock ./
RUN uv sync --locked --no-dev


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

COPY --from=builder /app/.venv /app/.venv
COPY --chown=app:app *.py ./
COPY --chown=app:app static ./static
COPY --chown=app:app templates ./templates
COPY --chown=app:app models ./models
RUN mkdir -p /app/audio_cache /app/documents/uploads /app/documents/assembled /app/jobs /home/app/.cache/huggingface \
    && chown -R app:app /app/audio_cache /app/documents /app/jobs /home/app/.cache

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]

CMD ["/app/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
