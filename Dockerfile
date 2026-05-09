FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies, ffmpeg for audio conversion, tesseract for OCR, and poppler for PDF->image
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    cmake \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-vie \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Cài uv để quản lý dependency từ pyproject.toml
RUN pip install --no-cache-dir --upgrade pip uv

# Copy file dependency trước để tối ưu layer cache
COPY pyproject.toml README.md ./
COPY uv.lock ./

# Đồng bộ dependency (không yêu cầu frozen để tránh fail khi lock chưa đồng bộ 100%)
RUN uv sync --no-dev

# Copy source code
COPY . .

# Đảm bảo thư mục cache và documents tồn tại
RUN mkdir -p /app/audio_cache /app/models/vieneu/assets /app/documents/uploads /app/documents/assembled /app/documents/sessions

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
