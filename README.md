# Ebook2Audio 🎧

**Miễn phí · Không giới hạn · Không cần đăng ký**

Ebook2Audio chuyển đổi văn bản, truyện, bài báo... thành âm thanh tự nhiên với **phụ đề trực tiếp**. Bạn có thể dán bất kỳ nội dung nào — từ một câu ngắn đến cả cuốn tiểu thuyết — và bắt đầu nghe ngay lập tức.

> 🌐 **Demo trực tiếp:** [ebook2audio.hoctuthien.com](https://ebook2audio.hoctuthien.com)

## ✨ Tại sao nên dùng Ebook2Audio?

- 🆓 **Hoàn toàn miễn phí** — Sử dụng công nghệ Edge TTS của Microsoft, không tốn phí, không cần API key.
- 📝 **Không giới hạn độ dài văn bản** — Dán một câu hay cả cuốn tiểu thuyết đều được. Văn bản dài sẽ được chia nhỏ tự động.
- 🎧 **Nghe ngay lập tức** — Âm thanh được phát theo thời gian thực (live streaming) ngay khi đang tạo, không cần chờ hoàn tất.
- 📜 **Phụ đề trực tiếp (Live Subtitles)** — Phụ đề hiện song song với audio, cập nhật từng câu theo thời gian thực. Hỗ trợ tải về định dạng SRT và WebVTT.
- 🌍 **Đa ngôn ngữ** — Hỗ trợ 7 ngôn ngữ với giọng đọc bản địa chất lượng cao: Tiếng Việt, Anh, Nhật, Trung, Hàn, Pháp, Đức.
- 🎙️ **Nhiều giọng đọc** — Hàng chục giọng đọc Neural tự nhiên cho mỗi ngôn ngữ (nam, nữ, trẻ em...).
- 💾 **Tải về dễ dàng** — Tải file MP3, file phụ đề SRT và WebVTT chỉ bằng một cú click.
- ⚡ **Lưu cache thông minh** — Văn bản đã chuyển đổi sẽ được lưu lại, lần sau mở lại là phát ngay không cần tạo lại.

## 📄 Document Upload

Upload PDF and EPUB files to convert ebooks and documents into audio:

- **Chunked Upload:** Supports files up to 50MB with 5MB chunked transfer
- **Smart Extraction:** Automatic chapter detection and structure analysis
- **Quality Assessment:** Text quality scoring with OCR recommendations
- **Session Storage:** Auto-cleanup after 24 hours
- **Large File Support:** Optimized for 200+ page documents

### Upload Workflow

1. Upload PDF/EPUB file (chunked transfer)
2. Preview chapter structure in real-time
3. Select specific chapters or entire document
4. Convert selected content to audio
5. Download audio with synchronized subtitles

### API Endpoints

```bash
# Initiate upload
POST /document/upload/initiate

# Upload chunks
POST /document/upload/chunk

# Complete upload
POST /document/upload/complete

# Stream extraction progress
GET /document/{id}/extract/stream

# Get document structure
GET /document/{id}/structure
```

### Usage Example

```python
import requests

# Initiate upload
response = requests.post("http://localhost:8000/document/upload/initiate", json={
    "filename": "ebook.pdf",
    "file_size": 15728640,
    "checksum": "abc123..."
})
upload_id = response.json()["upload_id"]

# Upload chunks (5MB each)
with open("ebook.pdf", "rb") as f:
    chunk_number = 0
    while True:
        chunk = f.read(5242880)  # 5MB
        if not chunk:
            break
        requests.post("http://localhost:8000/document/upload/chunk", 
            data={"upload_id": upload_id, "chunk_number": chunk_number},
            files={"chunk": chunk})
        chunk_number += 1

# Complete upload
response = requests.post("http://localhost:8000/document/upload/complete",
    data={"upload_id": upload_id})
document_id = response.json()["document_id"]

# Stream extraction progress
response = requests.get(f"http://localhost:8000/document/{document_id}/extract/stream", stream=True)
for line in response.iter_lines():
    if line:
        print(line.decode())
```
- 🐳 **Dễ dàng tự host** — Hỗ trợ Docker, Docker Compose, triển khai trên Coolify, Railway, VPS...
- 📄 **Upload tài liệu** — Tải lên PDF và EPUB để chuyển đổi sách và tài liệu thành audio có phụ đề.

## 🚀 Sử dụng

### Trực tuyến
Truy cập [ebook2audio.hoctuthien.com](https://ebook2audio.hoctuthien.com), dán văn bản, chọn ngôn ngữ và giọng đọc, rồi bấm **Chuyển thành audio**.

### Tự host (Self-host)

**Docker Compose (Khuyến nghị):**
```bash
git clone https://github.com/dvchd/ebook2audio.git
cd ebook2audio
docker compose up -d --build
```
Truy cập `http://localhost:8000` để sử dụng.

Compose chạy 3 service:
- `app`: FastAPI/UI/API, không tải model VieNeu nặng.
- `vieneu-worker`: xử lý VieNeu-TTS tuần tự từ Redis queue.
- `redis`: rate limit upload và queue cho VieNeu jobs.

Thiết lập tối thiểu khuyến nghị cho VPS nhỏ:

```env
MAX_WORKERS=1
VIENEU_INIT_IN_WEB=false
VIENEU_MAX_WORKERS=1
VIENEU_MODE=v3_turbo
VIENEU_SAMPLE_RATE=48000
TTS_STREAM_FIRST_BYTE_TIMEOUT_SECONDS=300
```

Với cấu hình này, chỉ nên chạy **1 VieNeu job tại một thời điểm**. Người dùng khác vẫn có thể mở web, xem trạng thái, tải audio cache, hoặc dùng Edge/gTTS nhẹ hơn.

Xem thêm:
- [`docs/redis-vieneu-queue.md`](docs/redis-vieneu-queue.md) — Redis hoạt động với VieNeu như thế nào.
- [`docs/project-structure.md`](docs/project-structure.md) — cấu trúc project và vai trò từng file chính.
- [`docs/azure-deployment.md`](docs/azure-deployment.md) — quản lý version và kiến trúc Azure.
- [`docs/azure-deploy-plan.md`](docs/azure-deploy-plan.md) — checklist triển khai Azure từng bước.

**Cài đặt thủ công:**
```bash
# Cài đặt dependency
pip install fastapi edge-tts gtts python-dotenv redis vieneu "uvicorn[standard]"

# Chạy server
uvicorn main:app --host 0.0.0.0 --port 8000

# Terminal khác: chạy worker VieNeu nếu dùng engine VieNeu
python tts_worker.py
```

### Tùy chỉnh

Tạo file `.env` tại thư mục gốc (xem `.env.example`) để cấu hình:

```env
PROXY=http://user:password@proxy-host:8080   # Proxy nếu cần
HOST=0.0.0.0
PORT=8000
ENABLE_DEBUG_TTS=false                          # Bật debug trên production
```

VieNeu mặc định chạy bằng **VieNeu-TTS v3 Turbo** với audio 48 kHz, voice built-in và tự chọn CPU ONNX hoặc GPU PyTorch theo môi trường:

```env
VIENEU_MODE=v3_turbo
VIENEU_SAMPLE_RATE=48000
```

Nếu cần quay về model v2 cũ, có thể dùng:

```env
VIENEU_MODE=v2_standard
```

Hoặc v2 Turbo CPU:

```env
VIENEU_MODE=v2_turbo
VIENEU_DEVICE=cpu
```

Hoặc v2 Turbo GPU nếu chạy trên NVIDIA GPU và đã có CUDA/LMDeploy phù hợp:

```env
VIENEU_MODE=v2_turbo_gpu
VIENEU_DEVICE=cuda
VIENEU_TURBO_BACKEND=lmdeploy
```

---

## 🛠 Cho nhà phát triển

### Cài đặt với `uv` (Khuyến nghị)
```bash
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### API
Ứng dụng cung cấp REST API đầy đủ. Xem tài liệu API tự động tại `/docs` (Swagger UI) sau khi chạy server.

### Triển khai trên Coolify
1. Thêm dự án từ GitHub vào Coolify.
2. Chọn loại **Docker Compose**.
3. Cấu hình biến môi trường (nếu cần).
4. Nhấn **Deploy**.

### Quản lý version và Azure
Khuyến nghị dùng cùng một version cho `pyproject.toml`, Git tag và Docker image tag, ví dụ `v4.0.0`. Nếu triển khai lên Azure, dùng kiến trúc `app` + `vieneu-worker` + Redis + Azure Files như mô tả trong [`docs/azure-deployment.md`](docs/azure-deployment.md), rồi làm theo runbook [`docs/azure-deploy-plan.md`](docs/azure-deploy-plan.md).

### Tài liệu kỹ thuật
- [`docs/project-structure.md`](docs/project-structure.md) — bản đồ cấu trúc source code, runtime data, test và service.
- [`docs/redis-vieneu-queue.md`](docs/redis-vieneu-queue.md) — luồng Redis queue, worker, cancellation và recovery cho VieNeu.

---

## 📄 Giấy phép

MIT — Sử dụng tự do cho mục đích cá nhân và thương mại.
