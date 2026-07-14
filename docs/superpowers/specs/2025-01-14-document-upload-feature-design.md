# Document Upload Feature Design Specification

**Project:** Ebook2Audio v3.0.0+
**Feature:** PDF/EPUB Upload with Text Extraction and OCR Support
**Date:** 2025-01-14
**Status:** Design Approved

---

## Executive Summary

Add PDF and EPUB file upload capabilities to Ebook2Audio, enabling users to convert ebooks and documents into audio content. The feature supports large files (200+ pages), intelligent text extraction with structure detection, OCR fallback for scanned documents, and user-controlled chapter selection before audio conversion.

**Key Capabilities:**
- Upload PDF/EPUB files up to 50MB with chunked upload (5MB chunks)
- Extract text with intelligent chapter/structure detection
- User-controlled chapter selection via split-view interface
- OCR enhancement for image-based or poor-quality content
- Streaming extraction with real-time progress updates
- Session-based storage with automatic 24-hour cleanup
- Memory-safe processing for large files (200+ pages)

---

## System Architecture

### Modular Hybrid Architecture

The feature integrates into the existing FastAPI application as a parallel module to the current TTS functionality.

```
                    ┌─────────────────────────────────────┐
                    │         Browser (Frontend)          │
                    │   - Upload UI (Split View)          │
                    │   - TTS UI (Existing)               │
                    └─────────┬───────────────────────────┘
                              │ HTTP/WebSocket
                              │
        ┌─────────────────────┴─────────────────────────┐
        │              FastAPI Application              │
        ├───────────────────────┬───────────────────────┤
        │   Existing TTS Module  │  Document Module      │
        │   ├── TTS Endpoints    │  ├── Upload API       │
        │   ├── Audio Streaming  │  ├── Extraction API   │
        │   └── Subtitle Gen     │  ├── OCR API          │
        │                        │  └── Chapter API      │
        └───────────┬───────────┴───────────┬───────────┘
                    │                       │
        ┌───────────┴───────────────────────┴───────────┐
        │           Shared Services & Storage           │
        │  ├── File Storage (/app/documents/)           │
        │  ├── Audio Cache (/app/audio_cache/)          │
        │  ├── Session Manager (in-memory)              │
        │  └── Background Tasks                         │
        └────────────────────────────────────────────────┘
```

### Component Structure

```
ebook2audio/
├── main.py                    # Existing TTS API (unchanged)
├── document_api.py            # New: Document upload/management API
├── file_processor.py          # New: File storage & session management
├── text_extractor.py          # New: PDF/EPUB extraction
├── ocr_service.py             # New: OCR fallback service
├── models/
│   ├── document.py            # New: Document data models
│   └── chapter.py             # New: Chapter data models
└── templates/
    ├── index.html             # Existing TTS UI
    └── document.html          # New: Split-view document UI
```

---

## Core Components

### 1. File Processor (`file_processor.py`)

**Responsibilities:**
- Chunked file upload (5MB chunks)
- File validation and security checks
- Session management and cleanup
- Storage management

**Key Functions:**
```python
async def initiate_upload(filename: str, file_size: int, checksum: str) -> UploadSession
async def receive_chunk(upload_id: str, chunk_number: int, chunk_data: bytes) -> bool
async def complete_upload(upload_id: str) -> Document
async def cleanup_expired_sessions()  # Runs every hour
async def validate_file_security(file_path: str) -> bool
```

**Storage Structure:**
```
/app/documents/
├── uploads/
│   ├── {upload_id}/
│   │   ├── chunk_0, chunk_1, chunk_2...
│   │   └── metadata.json
├── assembled/
│   └── {document_id}/
│       ├── document.pdf
│       ├── metadata.json
│       └── chapters/
└── sessions/
    └── {document_id}/lock
```

### 2. Text Extractor (`text_extractor.py`)

**Responsibilities:**
- PDF and EPUB text extraction
- Chapter/structure detection
- Quality assessment per chapter
- Streaming extraction progress

**Key Functions:**
```python
async def extract_pdf_structure(document_id: str) -> AsyncGenerator[Chapter, None]
async def extract_epub_structure(document_id: str) -> AsyncGenerator[Chapter, None]
async def detect_chapters(text: str, file_type: str) -> List[Chapter]
def assess_extraction_quality(chapter: Chapter) -> float  # Returns 0.0-1.0
async def extract_chapter_text(chapter: Chapter) -> str
```

**Extraction Strategy:**
- **PDF:** Process page-by-page, batch 20 pages at a time
- **EPUB:** Process chapter-by-chapter (natural boundaries)
- **Streaming:** Send results via Server-Sent Events (SSE)
- **Memory:** Only keep current batch in memory + metadata

### 3. OCR Service (`ocr_service.py`)

**Responsibilities:**
- Tesseract integration for image-based PDFs
- Page-by-page OCR processing
- OCR result caching
- Quality assessment

**Key Functions:**
```python
async def process_page_with_ocr(page_image: bytes, language: str) -> Tuple[str, float]
async def enhance_chapter_ocr(chapter_id: str, document_id: str) -> Chapter
def get_ocr_cached_result(page_key: str) -> Optional[str]
def save_ocr_result(page_key: str, text: str, confidence: float)
async def initialize_tesseract() -> bool
```

**OCR Strategy:**
- Process one page at a time
- Cache results per page (don't re-process)
- Parallel processing: Max 2 pages concurrently
- Timeout: 30 seconds per page
- Fallback: Keep original text if OCR fails

### 4. Document API (`document_api.py`)

**New API Endpoints:**

```python
# Upload Management
POST /document/upload/initiate
POST /document/upload/chunk
POST /document/upload/complete

# Document Operations
GET  /document/{document_id}
GET  /document/{document_id}/structure
GET  /document/{document_id}/extract/stream

# Chapter Operations
GET  /document/{document_id}/chapter/{chapter_id}
POST /document/{document_id}/enhance

# Cleanup
DELETE /document/{document_id}
GET  /document/health
```

---

## Data Models

### Document Metadata

```python
class Document:
    document_id: str              # UUID
    filename: str
    file_type: str                # "pdf" or "epub"
    file_size: int
    upload_date: datetime
    expires_at: datetime          # upload_date + 24h
    status: str                   # "uploading" | "extracting" | "ready" | "error"
    total_pages: int | None       # For PDF
    total_chapters: int | None
    extraction_progress: float    # 0.0 to 1.0
    file_path: str
    metadata: dict                # Author, title, language, etc.
```

### Chapter Structure

```python
class Chapter:
    chapter_id: str               # UUID
    document_id: str
    chapter_number: int
    title: str                    # Detected chapter heading
    start_page: int               # For PDF
    end_page: int                 # For PDF
    text_preview: str             # First 500 chars
    full_text: str | None
    word_count: int
    estimated_audio_duration: float
    quality_score: float          # 0.0 to 1.0
    needs_ocr: bool
    ocr_processed: bool
    extraction_method: str        # "basic" | "advanced" | "ocr"
    language: str
```

### Upload Session

```python
class UploadSession:
    upload_id: str
    document_id: str | None
    filename: str
    total_size: int
    chunk_size: int               # Default 5MB
    received_chunks: set[int]
    temp_dir: str
    checksum: str
    created_at: datetime
```

---

## User Interface Design

### Split View Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Ebook2Audio - Document Upload                              │
├──────────────────────┬──────────────────────────────────────┤
│   Chapter Tree       │   Chapter Preview                    │
│   (Left Panel)       │   (Right Panel)                      │
│                      │                                      │
│  □ Document Title    │  Chapter 1: Introduction             │
│    ├ □ Ch 1: Intro   │  ──────────────────────────────────  │
│    ├ □ Ch 2: Method  │  This is the first 500 characters   │
│    └ □ Ch 3: Results │  of the chapter text preview...     │
│                      │                                      │
│  □ Chapter 4         │  [Word Count: 1,234]                │
│    └ □ Subsection    │  [Est. Duration: 4:32]              │
│                      │  [Quality: ████░░ 80%]              │
│  [Select All]        │                                      │
│  [Enhance with OCR]  │  [Enhance with OCR]                 │
│  [Convert to Audio]  │  [Edit Chapter]                     │
└──────────────────────┴──────────────────────────────────────┘
```

### User Workflow

1. **Upload File**
   - Drag & drop or select PDF/EPUB
   - Real-time upload progress (chunked)
   - File validation feedback

2. **Preview Structure**
   - Chapter tree populates as extraction progresses
   - Quality indicators per chapter (green/yellow/red)
   - Expand/collapse chapter sections

3. **Select & Enhance**
   - Select/deselect chapters
   - Preview chapter text
   - Trigger OCR for poor-quality chapters

4. **Convert to Audio**
   - Click "Convert to Audio"
   - Redirect to existing TTS interface
   - Selected chapters concatenated and processed

---

## Chunking Strategy

### Upload Chunking

**Chunk Size:** 5MB per chunk (configurable via `UPLOAD_CHUNK_SIZE` env var)

**Upload Flow:**
1. Client: Calculate file size and number of chunks
2. Server: Initiate session, return `upload_id`
3. Client: Upload chunks sequentially or in parallel
4. Server: Validate each chunk (checksum, size)
5. Client: Signal upload complete
6. Server: Assemble chunks, validate file, return `document_id`

**Memory Efficiency:**
- Never load entire file into memory
- Maximum memory: 5MB (single chunk) + overhead
- Resume capability for failed uploads

### Extraction Chunking

**PDF Processing:**
- Batch size: 20 pages per batch (configurable)
- Stream chapters via SSE as extracted
- Progress: "Processed 45/200 pages (22%)"

**EPUB Processing:**
- Process chapter-by-chapter (natural boundaries)
- Stream immediately as each chapter completes
- Progress: "Extracted 12/18 chapters (66%)"

**OCR Processing:**
- Process one page at a time
- Max 2 pages concurrently
- Cache results to avoid re-processing

---

## Error Handling

### File Upload Errors

| Error Type | Handling |
|------------|----------|
| Network timeout during chunk upload | Retry mechanism (3 attempts) |
| Server restart during upload | Clean up orphaned chunks on startup |
| Checksum mismatch | Reject chunk, request re-upload |
| Out of disk space | Clear old sessions, reject new uploads |
| Missing chunks in complete request | Return missing chunk numbers |

### Extraction Errors

| Error Type | Handling |
|------------|----------|
| Password-protected PDF | Return error requesting password |
| Image-only PDF | Mark all chapters as `needs_ocr=true` |
| Corrupted EPUB structure | Attempt recovery, reject if fails |
| Missing fonts/glyphs | Extract with placeholders, flag quality issues |
| Partial extraction success | Return extracted chapters, flag problematic ones |

### OCR Errors

| Error Type | Handling |
|------------|----------|
| Tesseract not installed | Fail gracefully, require admin setup |
| Wrong language data | Download language pack or fail with message |
| Memory limit exceeded | Process fewer pages concurrently |
| Timeout on single page | Skip page, keep original text |
| Poor OCR confidence | Keep original text, mark as low quality |

### Large File Management

| Scenario | Strategy |
|----------|----------|
| Memory spike during extraction | Process in smaller batches (10 pages) |
| Too many concurrent sessions | Queue new uploads |
| Large single chapter | Split into sub-chunks |
| OCR memory overflow | Reduce parallel workers to 1 |

---

## Testing Strategy

### Unit Tests

**File Processor Tests:**
- Chunk upload (single and multi-chunk)
- Resume after failure
- Checksum validation
- File size limit enforcement
- Temp file cleanup
- Orphaned chunk cleanup
- Session expiry

**Text Extractor Tests:**
- PDF basic extraction
- PDF structure detection
- PDF password protection handling
- EPUB extraction and metadata
- Quality scoring
- Streaming extraction
- Large file memory limits
- Malformed PDF recovery
- Image-only PDF detection

**OCR Service Tests:**
- Tesseract availability check
- Single page OCR
- Multi-language support
- OCR confidence scoring
- OCR caching
- Timeout handling
- Memory limits
- Fallback on failure

### Integration Tests

**End-to-End Workflows:**
- Full PDF workflow (upload → extract → convert)
- Full EPUB workflow
- Chunked upload to extraction
- OCR enhancement workflow
- Concurrent document processing
- Session cleanup integration

**API Integration:**
- Upload initiate to complete sequence
- Streaming extraction API
- Chapter selection to TTS
- Error response formatting
- Rate limiting enforcement
- Document expiration

### Performance Tests

**Load Testing:**
- 10 simultaneous uploads
- 200-page PDF processing time
- Memory usage monitoring
- OCR performance per page

**Stress Testing:**
- 50MB file processing
- Corrupted large file handling
- OCR under memory pressure
- Disk full scenario

---

## Implementation Phases

### Phase 1: Foundation (MVP) - Week 1-2

**Deliverables:**
- Chunked file upload (5MB chunks)
- PDF/EPUB text extraction
- Basic chapter structure detection
- Streaming extraction progress
- Session-based cleanup (24h)
- Basic error handling

**Success Criteria:**
- Can upload and extract 20-page PDF in < 30 seconds
- Can handle 50MB file without memory issues
- Basic error handling works

### Phase 2: UI & Integration - Week 3

**Deliverables:**
- Split view UI (chapter tree + preview)
- Upload progress indicator
- Chapter selection interface
- Integration with existing TTS API

**Success Criteria:**
- Complete user workflow from upload to audio
- User can select specific chapters
- UI is responsive and intuitive

### Phase 3: OCR Enhancement - Week 4

**Deliverables:**
- Tesseract integration
- Page-by-page processing
- Quality scoring and caching
- "Enhance with OCR" workflow

**Success Criteria:**
- Can process image-based PDFs
- User can enhance poor-quality chapters
- OCR caching improves performance

### Phase 4: Production Readiness - Week 5

**Deliverables:**
- Performance optimization
- Security hardening
- Monitoring and logging
- Documentation completion

**Success Criteria:**
- Can handle 200+ page files reliably
- Memory usage stays under 500MB
- Production deployment stable

---

## Dependencies

### Python Packages

```python
dependencies = [
    # Existing dependencies...
    "PyPDF2>=3.0.1",           # PDF extraction
    "pdfplumber>=0.11.0",      # Advanced PDF parsing
    "ebooklib>=0.18",          # EPUB extraction
    "python-magic>=0.4.27",    # File type detection
    "pytesseract>=0.3.10",     # OCR wrapper
]
```

### System Requirements

```
# Required for Phase 3 (OCR)
- Tesseract OCR engine 4.0+
- Language data: Vietnamese, English, Japanese, Chinese, Korean, French, German
```

### Development Dependencies

```python
[dependency-groups]
dev = [
    "pytest>=9.0.3",
    "pytest-asyncio>=0.23.0",
    "pytest-mock>=3.14.0",
    "pytest-cov>=6.0.0",
    "httpx>=0.28.0",
    "faker>=30.0.0",
]
```

---

## Configuration

### Environment Variables

```env
# File Upload
UPLOAD_MAX_SIZE_MB=50
UPLOAD_CHUNK_SIZE=5242880    # 5MB chunks
UPLOAD_SESSION_EXPIRY_HOURS=24

# Extraction
EXTRACTION_PAGE_BATCH=20     # Process 20 pages per batch
EXTRACTION_TIMEOUT_SECONDS=300

# OCR
OCR_ENABLED=true
OCR_PARALLEL_WORKERS=2
OCR_TIMEOUT_SECONDS=30
OCR_CONFIDENCE_THRESHOLD=0.3

# Storage
DOCUMENT_STORAGE_PATH=/app/documents
OCR_CACHE_PATH=/app/documents/ocr_cache
CLEANUP_INTERVAL_HOURS=1
```

---

## Security Considerations

### File Upload Security

- File type validation (MIME type + extension)
- File size limits (50MB max)
- Checksum validation for chunks
- Path traversal prevention
- Malicious content scanning

### Processing Security

- Resource limits (memory, CPU, disk)
- Rate limiting (5 uploads per IP per minute)
- Session limits (3 concurrent extractions per user)
- Input sanitization
- Dependency security updates

### Privacy & Data

- No long-term file storage (24h auto-cleanup)
- OCR cache cleanup
- No user tracking beyond session
- Secure temporary file handling

---

## Monitoring & Metrics

### Key Metrics

- Active upload sessions
- Extraction success rate
- OCR enhancement usage
- Average file size
- Processing time per page
- Memory usage trends
- Disk space usage
- Error rates by type

### Logging

```python
# Structured logging
logger.info("document_upload_initiated",
            document_id=doc_id,
            filename=name,
            size_mb=file_size/1024/1024)

logger.error("extraction_failed",
             document_id=doc_id,
             error_type="pdf_malformed",
             partial_success=True,
             extracted_chapters=15,
             failed_chapters=3)
```

---

## Success Metrics

### Phase 1 Success
- ✅ Upload and extract 20-page PDF in < 30 seconds
- ✅ Handle 50MB file without memory issues
- ✅ Basic error handling operational

### Phase 2 Success
- ✅ Complete user workflow from upload to audio
- ✅ Chapter selection working
- ✅ Responsive UI

### Phase 3 Success
- ✅ Image-based PDF processing functional
- ✅ OCR quality assessment accurate
- ✅ Enhancement workflow smooth

### Phase 4 Success
- ✅ Handle 200+ page files reliably
- ✅ Memory usage under 500MB
- ✅ Production deployment stable

---

## Future Enhancements (Post-MVP)

- Multi-language OCR improvements
- Advanced chapter editing capabilities
- Batch document processing
- User library/favorites
- Document sharing capabilities
- Cloud storage integration (Google Drive, Dropbox)
- Advanced PDF layout analysis
- Handwriting recognition
- Table and image extraction
- Automatic language detection

---

## Appendix

### Sample API Requests/Responses

**Initiate Upload:**
```http
POST /document/upload/initiate
Content-Type: application/json

{
  "filename": "ebook.pdf",
  "file_size": 15728640,
  "total_chunks": 4,
  "checksum": "a1b2c3d4"
}

Response:
{
  "upload_id": "uuid-here",
  "chunk_size": 5242880
}
```

**Upload Chunk:**
```http
POST /document/upload/chunk
Content-Type: multipart/form-data

upload_id: uuid-here
chunk_number: 0
file: <binary>

Response:
{
  "received": true,
  "next_chunk": 1
}
```

**Get Document Structure:**
```http
GET /document/{document_id}/structure

Response:
{
  "document_id": "uuid",
  "status": "ready",
  "total_chapters": 18,
  "chapters": [
    {
      "chapter_id": "uuid",
      "chapter_number": 1,
      "title": "Introduction",
      "word_count": 1234,
      "quality_score": 0.85,
      "needs_ocr": false
    }
  ]
}
```

### OCR Quality Scoring

| Score Range | Quality Label | Action Required |
|-------------|---------------|-----------------|
| 0.8 - 1.0 | Excellent | None |
| 0.5 - 0.8 | Good | None |
| 0.3 - 0.5 | Fair | Offer OCR option |
| 0.0 - 0.3 | Poor | Recommend OCR |

---

**Document Status:** Approved for Implementation
**Next Phase:** Writing Implementation Plan
**Last Updated:** 2025-01-14
