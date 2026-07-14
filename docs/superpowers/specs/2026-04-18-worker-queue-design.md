# Worker Queue Design for Background PDF Extraction

**Date:** 2026-04-18
**Branch:** `feature/worker-queue`
**Status:** Draft

---

## Context

The Story to Audio app currently processes PDF/EPUB extraction synchronously. With **5-50 concurrent users** and **large documents (200+ pages)**, this blocks the event loop and degrades performance for all users.

**Problem:** CPU-intensive text extraction blocks the FastAPI event loop, making the server unresponsive during processing.

**Solution:** Background worker queue using ThreadPoolExecutor for non-blocking extraction.

---

## Architecture Overview

```
┌─────────┐      ┌──────────┐      ┌─────────────┐      ┌──────────┐
│  Front  │ ───> │ FastAPI  │ ───> │ Job Queue   │ ───> │ Workers  │
│  End    │      │  Routes  │      │ (ThreadPool)│      │ (4x)     │
└─────────┘      └──────────┘      └─────────────┘      └──────────┘
                      │                     │
                      v                     v
                 ┌─────────┐         ┌─────────────┐
                 │ Poll    │<────────│ Job Status  │
                 │ Status  │         │ (JSON files)│
                 └─────────┘         └─────────────┘
```

**Components:**
1. **ThreadPoolExecutor** - 4 worker threads for CPU-bound extraction
2. **Job Queue** - manages pending/running/failed jobs
3. **Job Storage** - persistent JSON files for job state
4. **Status API** - pollable endpoint for job progress

---

## Components & Interfaces

### New File: `job_queue.py`

```python
class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class ExtractionJob(BaseModel):
    job_id: str
    document_id: str
    status: JobStatus
    progress: float           # 0.0 to 1.0
    message: str
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error: Optional[str]
    result: Optional[Dict]     # chapters data
```

**Core Functions:**
- `submit_extraction_job(document_id: str) -> str` - Create job, return job_id
- `get_job_status(job_id: str) -> Optional[ExtractionJob]` - Get current status
- `retry_job(job_id: str) -> bool` - Retry failed job
- `cancel_job(job_id: str) -> bool` - Cancel pending/running job

### Worker Pool

```python
# In job_queue.py
from concurrent.futures import ThreadPoolExecutor

pdf_executor = ThreadPoolExecutor(
    max_workers=4,  # Configurable via env var
    thread_name_prefix="pdf_worker"
)
```

Each worker runs blocking extraction functions:
- `extract_pdf_text_blocking(document_id, file_path)`
- `extract_epub_text_blocking(document_id, file_path)`

### New API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/job/{document_id}/extract` | Submit extraction job |
| GET | `/job/{job_id}/status` | Get job status + progress |
| POST | `/job/{job_id}/retry` | Retry failed job |
| DELETE | `/job/{job_id}` | Cancel/delete job |
| GET | `/job/{job_id}/result` | Get extraction result |

---

## Data Flow

```
1. Upload → complete_upload() → status=UPLOADING
2. User clicks Extract → POST /job/{doc_id}/extract
3. Create job (status=PENDING) → submit to thread pool
4. Worker picks up → status=RUNNING → update progress
5. Extraction done → status=COMPLETED → store chapters in result
6. Frontend polls → detects COMPLETED → fetches result
7. Chapters displayed → user can select/convert
```

**Progress Updates:**
- Workers update job progress during extraction
- Progress includes: current_page, total_pages, current_chapter
- Frontend polls every 2 seconds

---

## Error Handling

| Scenario | Handling |
|----------|----------|
| **Timeout** | Jobs > 30min auto-fail with timeout error |
| **Worker Crash** | Exception caught → status=FAILED with error message |
| **Retry** | Failed jobs can be retried (new job_id, same document) |
| **Orphan Recovery** | On startup, RUNNING jobs marked as FAILED |
| **Invalid Document** | Immediate fail with descriptive error |

---

## Storage

**Job Storage:**
- Location: `./jobs/{job_id}.json`
- Format: JSON with all job fields
- Survives server restarts

**Cleanup:**
- Auto-delete jobs older than 24 hours
- Runs via background task every hour

**Configuration:**
```python
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
JOB_TIMEOUT_MINUTES = int(os.getenv("JOB_TIMEOUT_MINUTES", "30"))
JOB_RETENTION_HOURS = int(os.getenv("JOB_RETENTION_HOURS", "24"))
```

---

## Frontend Integration

**New JavaScript Functions:**

```javascript
async function startExtractionWithJob(docId, filename) {
    const response = await fetch(`/job/${docId}/extract`, {
        method: 'POST'
    });
    const {job_id} = await response.json();
    pollJobStatus(job_id, docId, filename);
}

function pollJobStatus(jobId, docId, filename) {
    const interval = setInterval(async () => {
        const status = await fetch(`/job/${jobId}/status`)
            .then(r => r.json());

        updateProgressUI(status);

        if (status.status === 'completed') {
            clearInterval(interval);
            loadJobResult(jobId, docId, filename);
        } else if (status.status === 'failed') {
            clearInterval(interval);
            showError(status.error);
        }
    }, 2000);
}
```

**UI Changes:**
- Reuse existing extraction progress UI
- Add "Retry" button for failed jobs in queue
- No changes to queue tab structure

---

## Implementation Phases

1. **Phase 1:** Create `job_queue.py` with worker pool and job management
2. **Phase 2:** Add job API endpoints to `document_api.py`
3. **Phase 3:** Update frontend `index.html` with polling logic
4. **Phase 4:** Test with large PDFs (200+ pages)
5. **Phase 5:** Add retry button and error handling improvements

---

## Success Criteria

- [ ] Uploads return immediately (< 1 second)
- [ ] Multiple PDFs can extract concurrently
- [ ] Server remains responsive during extraction
- [ ] Failed jobs can be retried
- [ ] Job status persists across server restarts
- [ ] Progress updates every ~2 seconds
- [ ] Cleanup removes old jobs automatically
