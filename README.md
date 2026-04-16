# Story2Audio 🎧

**Miễn phí · Không giới hạn · Không cần đăng ký**

Story2Audio chuyển đổi văn bản, truyện, bài báo... thành âm thanh tự nhiên với **phụ đề trực tiếp**. Bạn có thể dán bất kỳ nội dung nào — từ một câu ngắn đến cả cuốn tiểu thuyết — và bắt đầu nghe ngay lập tức.

> 🌐 **Demo trực tiếp:** [story2audio.hoctuthien.com](https://story2audio.hoctuthien.com)

## ✨ Tại sao nên dùng Story2Audio?

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
Truy cập [story2audio.hoctuthien.com](https://story2audio.hoctuthien.com), dán văn bản, chọn ngôn ngữ và giọng đọc, rồi bấm **Chuyển thành audio**.

### Tự host (Self-host)

**Docker Compose (Khuyến nghị):**
```bash
git clone https://github.com/dvchd/story2audio.git
cd story2audio
docker compose up -d --build
```
Truy cập `http://localhost:8000` để sử dụng.

**Cài đặt thủ công:**
```bash
# Cài đặt dependency
pip install fastapi edge-tts gtts python-dotenv "uvicorn[standard]"

# Chạy server
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Tùy chỉnh

Tạo file `.env` tại thư mục gốc (xem `.env.example`) để cấu hình:

```env
PROXY=http://user:password@proxy-host:8080   # Proxy nếu cần
HOST=0.0.0.0
PORT=8000
ENABLE_DEBUG_TTS=false                          # Bật debug trên production
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

---

## 📄 Giấy phép

MIT — Sử dụng tự do cho mục đích cá nhân và thương mại.
