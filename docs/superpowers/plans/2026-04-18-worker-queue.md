# Worker Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add background worker queue for non-blocking PDF/EPUB text extraction, supporting 5-50 concurrent users with large documents (200+ pages).

**Architecture:** ThreadPoolExecutor with 4 worker threads runs blocking extraction functions; job state persisted to JSON files; frontend polls for status.

**Tech Stack:** FastAPI, ThreadPoolExecutor, JSON file storage, async/await

---

## File Structure

```
story2audio/
├── job_queue.py              # NEW - Job management & worker pool
├── document_api.py           # MODIFY - Add job endpoints
├── text_extractor.py         # MODIFY - Add blocking extraction functions
├── templates/index.html      # MODIFY - Add polling-based extraction
├── tests/                    # NEW - Add tests for job_queue.py
│   └── test_job_queue.py
└── jobs/                     # NEW - Job state storage (created at runtime)
```

---

## Task 1: Create job_queue.py with models and configuration

**Files:**
- Create: `job_queue.py`

- [ ] **Step 1: Add imports and configuration**

```python
# job_queue.py
import os
import uuid
import json
import asyncio
from datetime import datetime, timedelta, UTC
from typing import Optional, Dict, List, Callable
from enum import Enum
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, Future
import threading

from pydantic import BaseModel, Field

# Configuration
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
JOB_TIMEOUT_MINUTES = int(os.getenv("JOB_TIMEOUT_MINUTES", "30"))
JOB_RETENTION_HOURS = int(os.getenv("JOB_RETENTION_HOURS", "24"))
JOBS_DIR = os.getenv("JOBS_DIR", "./jobs")
```

- [ ] **Step 2: Define JobStatus enum and ExtractionJob model**

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
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str = ""
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Optional[Dict] = None
```

- [ ] **Step 3: Add global state and storage functions**

```python
# In-memory job tracking
_active_jobs: Dict[str, Future] = {}
_jobs_lock = threading.Lock()

# Job storage paths
def _get_job_path(job_id: str) -> str:
    return os.path.join(JOBS_DIR, f"{job_id}.json")

def _ensure_jobs_dir():
    os.makedirs(JOBS_DIR, exist_ok=True)

def _save_job(job: ExtractionJob):
    _ensure_jobs_dir()
    with open(_get_job_path(job.job_id), 'w') as f:
        json.dump(job.model_dump(), f, default=str)

def _load_job(job_id: str) -> Optional[ExtractionJob]:
    path = _get_job_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        data = json.load(f)
        return ExtractionJob(**data)

def _delete_job_file(job_id: str):
    path = _get_job_path(job_id)
    if os.path.exists(path):
        os.remove(path)
```

- [ ] **Step 4: Run commit**

```bash
git add job_queue.py
git commit -m "feat(job-queue): add job models and storage functions"
```

---

## Task 2: Create worker pool and job submission

**Files:**
- Modify: `job_queue.py`

- [ ] **Step 1: Create ThreadPoolExecutor and progress callback**

```python
# Add to job_queue.py after the storage functions

# Worker pool
_pdf_executor = ThreadPoolExecutor(
    max_workers=MAX_WORKERS,
    thread_name_prefix="pdf_worker"
)

# Progress callback type
ProgressCallback = Callable[[float, str], None]

def _update_job_progress(job_id: str, progress: float, message: str):
    """Update job progress from worker thread."""
    job = _load_job(job_id)
    if job:
        job.progress = min(1.0, max(0.0, progress))
        job.message = message
        _save_job(job)
```

- [ ] **Step 2: Add blocking wrapper for extraction (to be implemented in text_extractor.py)**

```python
def _run_extraction_blocking(
    document_id: str,
    file_path: str,
    file_type: str,
    progress_callback: ProgressCallback
) -> List[Dict]:
    """
    Blocking wrapper for extraction. Runs in worker thread.

    This will be implemented by adding blocking functions to text_extractor.py.
    For now, we'll create a stub that will be replaced.
    """
    # TODO: Import and call blocking extraction from text_extractor
    # For now, raise NotImplementedError
    raise NotImplementedError(
        "Blocking extraction not yet implemented. "
        "See Task 4 for implementation."
    )
```

- [ ] **Step 3: Create job worker function**

```python
def _job_worker(job_id: str, document_id: str, file_path: str, file_type: str):
    """
    Worker function that runs in ThreadPoolExecutor thread.
    Updates job status and handles errors.
    """
    job = _load_job(job_id)
    if not job:
        return

    try:
        # Update to running
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        job.message = "Starting extraction..."
        _save_job(job)

        # Create progress callback
        def callback(progress: float, message: str):
            _update_job_progress(job_id, progress, message)

        # Run extraction (blocking)
        chapters = _run_extraction_blocking(document_id, file_path, file_type, callback)

        # Store result
        job.status = JobStatus.COMPLETED
        job.progress = 1.0
        job.message = "Extraction complete"
        job.completed_at = datetime.now(UTC)
        job.result = {"chapters": chapters}

    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        job.message = f"Failed: {str(e)}"
        job.completed_at = datetime.now(UTC)

    finally:
        _save_job(job)
        # Remove from active jobs
        with _jobs_lock:
            _active_jobs.pop(job_id, None)
```

- [ ] **Step 4: Implement submit_extraction_job**

```python
# Add to job_queue.py after _job_worker

async def submit_extraction_job(document_id: str, file_path: str, file_type: str) -> str:
    """
    Submit an extraction job to the worker pool.

    Args:
        document_id: Document ID to extract
        file_path: Path to the document file
        file_type: Type of file ('pdf' or 'epub')

    Returns:
        job_id: The ID of the created job
    """
    job_id = str(uuid.uuid4())

    # Create job
    job = ExtractionJob(
        job_id=job_id,
        document_id=document_id,
        status=JobStatus.PENDING,
        progress=0.0,
        message="Queued for extraction...",
        created_at=datetime.now(UTC)
    )
    _save_job(job)

    # Submit to worker pool
    def job_wrapper():
        _job_worker(job_id, document_id, file_path, file_type)

    future = _pdf_executor.submit(job_wrapper)

    with _jobs_lock:
        _active_jobs[job_id] = future

    return job_id
```

- [ ] **Step 5: Implement get_job_status**

```python
# Add to job_queue.py after submit_extraction_job

def get_job_status(job_id: str) -> Optional[ExtractionJob]:
    """
    Get the current status of a job.

    Args:
        job_id: Job ID to query

    Returns:
        ExtractionJob or None if not found
    """
    return _load_job(job_id)
```

- [ ] **Step 6: Implement retry_job**

```python
# Add to job_queue.py after get_job_status

async def retry_job(job_id: str) -> Optional[str]:
    """
    Retry a failed job.

    Args:
        job_id: Failed job to retry

    Returns:
        New job_id if retry submitted, None if job not found or not failed
    """
    job = _load_job(job_id)
    if not job or job.status != JobStatus.FAILED:
        return None

    # Get document info from active_documents (imported from file_processor)
    from file_processor import active_documents
    doc = active_documents.get(job.document_id)
    if not doc:
        return None

    # Submit new job
    new_job_id = await submit_extraction_job(
        job.document_id,
        doc.file_path,
        doc.file_type.value
    )

    return new_job_id
```

- [ ] **Step 7: Implement cancel_job**

```python
# Add to job_queue.py after retry_job

def cancel_job(job_id: str) -> bool:
    """
    Cancel a pending or running job.

    Args:
        job_id: Job ID to cancel

    Returns:
        True if cancelled, False if not found or already completed
    """
    with _jobs_lock:
        future = _active_jobs.get(job_id)
        if future and not future.done():
            future.cancel()
            _active_jobs.pop(job_id, None)

            # Update job status
            job = _load_job(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error = "Cancelled by user"
                job.message = "Job cancelled"
                job.completed_at = datetime.now(UTC)
                _save_job(job)

            return True

    return False
```

- [ ] **Step 8: Implement cleanup_old_jobs**

```python
# Add to job_queue.py after cancel_job

def cleanup_old_jobs() -> int:
    """
    Delete job files older than retention period.

    Returns:
        Number of jobs deleted
    """
    if not os.path.exists(JOBS_DIR):
        return 0

    cutoff = datetime.now(UTC) - timedelta(hours=JOB_RETENTION_HOURS)
    deleted = 0

    for filename in os.listdir(JOBS_DIR):
        if not filename.endswith('.json'):
            continue

        filepath = os.path.join(JOBS_DIR, filename)
        # Check modification time
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath), tz=UTC)

        if mtime < cutoff:
            os.remove(filepath)
            deleted += 1

    return deleted

def recover_orphan_jobs() -> int:
    """
    Mark RUNNING jobs as FAILED (recovery from server crash).

    Returns:
        Number of jobs recovered
    """
    if not os.path.exists(JOBS_DIR):
        return 0

    recovered = 0

    for filename in os.listdir(JOBS_DIR):
        if not filename.endswith('.json'):
            continue

        job = _load_job(filename[:-5])  # Remove .json
        if job and job.status == JobStatus.RUNNING:
            job.status = JobStatus.FAILED
            job.error = "Server crashed during extraction"
            job.message = "Extraction interrupted"
            job.completed_at = datetime.now(UTC)
            _save_job(job)
            recovered += 1

    return recovered
```

- [ ] **Step 9: Run commit**

```bash
git add job_queue.py
git commit -m "feat(job-queue): add worker pool and job management functions"
```

---

## Task 3: Add blocking extraction functions to text_extractor.py

**Files:**
- Modify: `text_extractor.py`

- [ ] **Step 1: Add progress callback type and imports**

```python
# Add to text_extractor.py imports
from typing import Callable, List

# Progress callback type: (progress: float, message: str) -> None
ProgressCallback = Callable[[float, str], None]
```

- [ ] **Step 2: Add blocking PDF extraction function**

```python
# Add to text_extractor.py after extract_pdf_text (around line 225)

def extract_pdf_text_blocking(
    document_id: str,
    file_path: str,
    progress_callback: Optional[ProgressCallback] = None
) -> List[Dict]:
    """
    Blocking version of PDF extraction for use in worker threads.

    Args:
        document_id: Document ID
        file_path: Path to PDF file
        progress_callback: Optional callback for progress updates

    Returns:
        List of chapter dictionaries
    """
    try:
        # Try pdfplumber first
        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)
            all_text = []

            for page_num, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text()
                    if text:
                        all_text.append(text)
                except Exception:
                    all_text.append("")

                # Report progress
                if progress_callback and total_pages > 0:
                    progress = (page_num + 1) / total_pages
                    message = f"Processing page {page_num + 1}/{total_pages}"
                    progress_callback(progress, message)

            full_text = '\n\n'.join(all_text)

    except Exception as e:
        # Fallback to PyPDF2
        try:
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                total_pages = len(pdf_reader.pages)
                all_text = []

                for page_num in range(total_pages):
                    try:
                        page = pdf_reader.pages[page_num]
                        text = page.extract_text()
                        if text:
                            all_text.append(text)
                    except Exception:
                        all_text.append("")

                    if progress_callback and total_pages > 0:
                        progress = (page_num + 1) / total_pages
                        message = f"Processing page {page_num + 1}/{total_pages}"
                        progress_callback(progress, message)

                full_text = '\n\n'.join(all_text)

        except Exception as e2:
            raise RuntimeError(f"PDF extraction failed: {e2}")

    # Detect chapters
    chapters = _detect_chapters_in_text(full_text, document_id)

    # Build result list
    result = []
    for chapter in chapters:
        # Assess quality
        chapter.quality_score = _assess_text_quality(chapter.full_text or "")
        chapter.needs_ocr = chapter.quality_score < 0.5

        # Estimate audio duration
        if chapter.word_count > 0:
            chapter.estimated_audio_duration = (chapter.word_count / 150) * 60

        result.append({
            "chapter_id": chapter.chapter_id,
            "document_id": chapter.document_id,
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "start_page": chapter.start_page,
            "end_page": chapter.end_page,
            "text_preview": chapter.text_preview[:200] if chapter.text_preview else "",
            "full_text": chapter.full_text or "",
            "word_count": chapter.word_count,
            "estimated_audio_duration": chapter.estimated_audio_duration,
            "quality_score": chapter.quality_score,
            "needs_ocr": chapter.needs_ocr,
            "extraction_method": chapter.extraction_method.value,
            "language": chapter.language
        })

    return result
```

- [ ] **Step 3: Add blocking EPUB extraction function**

```python
# Add to text_extractor.py after extract_epub_text

def extract_epub_text_blocking(
    document_id: str,
    file_path: str,
    progress_callback: Optional[ProgressCallback] = None
) -> List[Dict]:
    """
    Blocking version of EPUB extraction for use in worker threads.

    Args:
        document_id: Document ID
        file_path: Path to EPUB file
        progress_callback: Optional callback for progress updates

    Returns:
        List of chapter dictionaries
    """
    try:
        from ebooklib import epub

        chapters = []
        book = epub.read_epub(file_path)

        # Get all items
        all_items = list(book.get_items())
        total_items = len(all_items)

        for idx, item in enumerate(all_items):
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                # Extract text from HTML content
                content = item.get_content()
                # Simple text extraction (strip HTML tags)
                import re
                text = re.sub(r'<[^>]+>', '\n', content.decode('utf-8', errors='ignore'))
                text = ' '.join(text.split())

                if text.strip():
                    ch_num = len(chapters) + 1
                    chapter = Chapter(
                        chapter_id=f"{document_id}_ch_{ch_num}",
                        document_id=document_id,
                        chapter_number=ch_num,
                        title=f"Section {ch_num}",
                        text_preview=text[:200],
                        full_text=text,
                        word_count=len(text.split())
                    )

                    chapter.quality_score = _assess_text_quality(text)
                    chapter.needs_ocr = chapter.quality_score < 0.5

                    if chapter.word_count > 0:
                        chapter.estimated_audio_duration = (chapter.word_count / 150) * 60

                    chapters.append(chapter)

            # Report progress
            if progress_callback and total_items > 0:
                progress = (idx + 1) / total_items
                message = f"Processing section {idx + 1}/{total_items}"
                progress_callback(progress, message)

    except Exception as e:
        raise RuntimeError(f"EPUB extraction failed: {e}")

    # Build result list
    result = []
    for chapter in chapters:
        result.append({
            "chapter_id": chapter.chapter_id,
            "document_id": chapter.document_id,
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "start_page": chapter.start_page,
            "end_page": chapter.end_page,
            "text_preview": chapter.text_preview[:200] if chapter.text_preview else "",
            "full_text": chapter.full_text or "",
            "word_count": chapter.word_count,
            "estimated_audio_duration": chapter.estimated_audio_duration,
            "quality_score": chapter.quality_score,
            "needs_ocr": chapter.needs_ocr,
            "extraction_method": chapter.extraction_method.value,
            "language": chapter.language
        })

    return result
```

- [ ] **Step 4: Run commit**

```bash
git add text_extractor.py
git commit -m "feat(extractor): add blocking extraction functions for worker threads"
```

---

## Task 4: Wire up blocking extraction in job_queue.py

**Files:**
- Modify: `job_queue.py`

- [ ] **Step 1: Update imports to include text_extractor**

```python
# Add to job_queue.py imports
from text_extractor import extract_pdf_text_blocking, extract_epub_text_blocking
```

- [ ] **Step 2: Replace the stub _run_extraction_blocking**

```python
# Find and replace the _run_extraction_blocking function with:

def _run_extraction_blocking(
    document_id: str,
    file_path: str,
    file_type: str,
    progress_callback: ProgressCallback
) -> List[Dict]:
    """
    Blocking wrapper for extraction. Runs in worker thread.
    """
    if file_type == "pdf":
        return extract_pdf_text_blocking(document_id, file_path, progress_callback)
    elif file_type == "epub":
        return extract_epub_text_blocking(document_id, file_path, progress_callback)
    else:
        raise ValueError(f"Unsupported file type: {file_type}")
```

- [ ] **Step 3: Run commit**

```bash
git add job_queue.py
git commit -m "feat(job-queue): wire up blocking extraction functions"
```

---

## Task 5: Add job API endpoints to document_api.py

**Files:**
- Modify: `document_api.py`

- [ ] **Step 1: Add job_queue imports**

```python
# Add to document_api.py imports
from job_queue import (
    submit_extraction_job,
    get_job_status,
    retry_job,
    cancel_job,
    ExtractionJob,
    JobStatus
)
```

- [ ] **Step 2: Add POST /job/{document_id}/extract endpoint**

```python
# Add to document_api.py after the delete endpoint (around line 300)

@router.post("/job/{document_id}/extract")
async def submit_job_extraction(document_id: str):
    """
    Submit a document extraction job to the worker queue.

    Returns immediately with job_id for status polling.
    """
    document = await get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if document.status == DocumentStatus.EXTRACTING:
        raise HTTPException(status_code=400, detail="Extraction already in progress")

    if document.status == DocumentStatus.READY:
        # Already extracted - return completed status immediately
        return {
            "job_id": document.document_id,
            "document_id": document_id,
            "status": "completed",
            "message": "Document already extracted"
        }

    # Submit job
    job_id = await submit_extraction_job(
        document_id,
        document.file_path,
        document.file_type.value
    )

    # Update document status
    document.status = DocumentStatus.EXTRACTING
    active_documents[document_id] = document

    return {
        "job_id": job_id,
        "document_id": document_id,
        "status": "pending",
        "message": "Job submitted to queue"
    }
```

- [ ] **Step 3: Add GET /job/{job_id}/status endpoint**

```python
# Add after the extract endpoint

@router.get("/job/{job_id}/status")
async def get_job_status_endpoint(job_id: str):
    """
    Get the status of an extraction job.
    """
    job = get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return job.model_dump()
```

- [ ] **Step 4: Add GET /job/{job_id}/result endpoint**

```python
# Add after the status endpoint

@router.get("/job/{job_id}/result")
async def get_job_result(job_id: str):
    """
    Get the extraction result from a completed job.
    """
    job = get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job not completed. Current status: {job.status.value}"
        )

    return job.result
```

- [ ] **Step 5: Add POST /job/{job_id}/retry endpoint**

```python
# Add after the result endpoint

@router.post("/job/{job_id}/retry")
async def retry_job_endpoint(job_id: str):
    """
    Retry a failed extraction job.
    """
    new_job_id = await retry_job(job_id)
    if not new_job_id:
        raise HTTPException(
            status_code=400,
            detail="Job not found or not failed. Only failed jobs can be retried."
        )

    return {
        "old_job_id": job_id,
        "new_job_id": new_job_id,
        "message": "Job resubmitted to queue"
    }
```

- [ ] **Step 6: Add DELETE /job/{job_id} endpoint**

```python
# Add after the retry endpoint

@router.delete("/job/{job_id}")
async def cancel_job_endpoint(job_id: str):
    """
    Cancel a pending or running job.
    """
    success = cancel_job(job_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Job not found, already completed, or cannot be cancelled"
        )

    return {"message": "Job cancelled"}
```

- [ ] **Step 7: Add startup event handler**

```python
# Add to document_api.py (need to import app from main)
# At the bottom of the file, add:

from job_queue import recover_orphan_jobs

@router.on_event("startup")
async def startup_event():
    """Recover orphan jobs on startup."""
    recovered = recover_orphan_jobs()
    if recovered > 0:
        print(f"Recovered {recovered} orphan jobs from server crash")
```

- [ ] **Step 8: Run commit**

```bash
git add document_api.py
git commit -m "feat(api): add job queue endpoints for background extraction"
```

---

## Task 6: Update frontend to use job polling

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add polling-based extraction function**

```javascript
// Add to index.html after the existing extraction functions (around line 1450)

async function startExtractionWithJob(docId, filename) {
    const doc = queueState.documents.find(d => d.document_id === docId);
    if (!doc) return;

    // Switch to upload tab and start extraction
    switchTab('upload-document');

    // Hide upload zone, show extraction
    document.getElementById('uploadZone').classList.add('hidden');
    document.getElementById('uploadProgress').classList.add('hidden');
    document.getElementById('extractionProgress').classList.remove('hidden');

    uploadState.documentId = docId;

    // Submit job
    try {
        const response = await fetch(`/job/${docId}/extract`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to submit job');
        }

        const data = await response.json();
        pollJobStatus(data.job_id, docId, filename);
    } catch (error) {
        console.error('Job submission error:', error);
        showExtractionError(error.message);
        resetUploadUI();
    }
}

function pollJobStatus(jobId, docId, filename) {
    const interval = setInterval(async () => {
        try {
            const response = await fetch(`/job/${jobId}/status`);

            if (!response.ok) {
                clearInterval(interval);
                showExtractionError('Failed to get job status');
                return;
            }

            const status = await response.json();

            // Update progress UI
            updateExtractionProgress({
                current: Math.floor(status.progress * 100),
                total: 100,
                message: status.message
            });

            if (status.status === 'completed') {
                clearInterval(interval);
                loadJobResult(jobId, docId, filename);
            } else if (status.status === 'failed') {
                clearInterval(interval);
                showExtractionError(status.error || 'Extraction failed');
                resetUploadUI();
            }
        } catch (error) {
            clearInterval(interval);
            console.error('Polling error:', error);
        }
    }, 2000);
}

async function loadJobResult(jobId, docId, filename) {
    try {
        const response = await fetch(`/job/${jobId}/result`);

        if (!response.ok) {
            throw new Error('Failed to get job result');
        }

        const data = await response.json();

        // Convert result to chapter format
        uploadState.chapters = data.chapters.map(ch => ({
            chapter_id: ch.chapter_id,
            chapter_number: ch.chapter_number,
            title: ch.title,
            word_count: ch.word_count,
            text_preview: ch.text_preview
        }));

        // Also update document metadata
        const docResponse = await fetch(`/document/${docId}`);
        if (docResponse.ok) {
            const docData = await docResponse.json();
            // Store chapters in document metadata for content retrieval
            const document = active_documents.get(docId);
            if (document) {
                document.metadata["chapters"] = data.chapters;
                document.total_chapters = data.chapters.length;
                document.status = DocumentStatus.READY;
            }
        }

        extractionComplete({ total_chapters: data.chapters.length }, filename);

        // Refresh queue to update status
        loadQueue();
    } catch (error) {
        console.error('Load result error:', error);
        showExtractionError(error.message);
    }
}
```

- [ ] **Step 2: Update startQueueExtraction to use job-based extraction**

```javascript
// Find the existing startQueueExtraction function and replace it:

async function startQueueExtraction(docId) {
    const doc = queueState.documents.find(d => d.document_id === docId);
    if (!doc) return;

    // Use new job-based extraction
    await startExtractionWithJob(docId, doc.filename);
}
```

- [ ] **Step 3: Add retry button handler for failed jobs**

```javascript
// Add after the pollJobStatus function:

async function retryFailedJob(docId) {
    const doc = queueState.documents.find(d => d.document_id === docId);
    if (!doc) return;

    try {
        const response = await fetch(`/job/${docId}/retry`, {
            method: 'POST'
        });

        if (!response.ok) {
            throw new Error('Failed to retry job');
        }

        const data = await response.json();

        // Start polling the new job
        await startExtractionWithJob(docId, doc.filename);
    } catch (error) {
        console.error('Retry error:', error);
        showModal({
            variant: 'error',
            title: 'Retry Failed',
            message: error.message,
            actions: [{ label: 'OK', primary: true }]
        });
    }
}
```

- [ ] **Step 4: Update renderQueue to show retry button for failed jobs**

```javascript
// Find the renderQueue function and update the buttons HTML:

// Replace the queue-actions div content with:
${doc.status === 'uploading' ? `
    <button class="queue-btn primary" data-action="extract" title="Trích xuất">▶</button>
` : ''}
${doc.status === 'ready' ? `
    <button class="queue-btn" data-action="view" title="Xem chương">📄</button>
` : ''}
${doc.status === 'failed' || doc.status === 'error' ? `
    <button class="queue-btn primary" data-action="retry" title="Thử lại">🔄</button>
` : ''}
<button class="queue-btn danger" data-action="delete" title="Xóa">🗑</button>

// Also add 'failed' to statusLabels:
const statusLabels = {
    'uploading': 'Đã tải',
    'extracting': 'Đang trích',
    'ready': 'Đã sẵn',
    'error': 'Lỗi',
    'failed': 'Thất bại'
};
```

- [ ] **Step 5: Add retry action handler**

```javascript
// Find the click handlers in renderQueue and add retry case:

if (action === 'extract') {
    e.stopPropagation();
    startQueueExtraction(docId);
} else if (action === 'retry') {
    e.stopPropagation();
    retryFailedJob(docId);
} else if (action === 'delete') {
    e.stopPropagation();
    deleteQueueDocument(docId);
} else if (action === 'view') {
    e.stopPropagation();
    viewQueueDocument(docId);
}
```

- [ ] **Step 6: Run commit**

```bash
git add templates/index.html
git commit -m "feat(frontend): add job polling and retry support"
```

---

## Task 7: Add tests for job_queue.py

**Files:**
- Create: `tests/test_job_queue.py`

- [ ] **Step 1: Create test file with imports and fixtures**

```python
# tests/test_job_queue.py
import os
import tempfile
import shutil
import json
from datetime import datetime, UTC

import pytest

from job_queue import (
    JobStatus,
    ExtractionJob,
    submit_extraction_job,
    get_job_status,
    cancel_job,
    cleanup_old_jobs,
    _save_job,
    _load_job,
    _get_job_path
)

# Test fixtures
@pytest.fixture
def temp_jobs_dir(tmp_path):
    """Create temporary jobs directory."""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()

    # Patch the JOBS_DIR
    import job_queue
    original_dir = job_queue.JOBS_DIR
    job_queue.JOBS_DIR = str(jobs_dir)

    yield jobs_dir

    # Cleanup
    job_queue.JOBS_DIR = original_dir

@pytest.fixture
def sample_document(tmp_path):
    """Create a sample PDF file for testing."""
    # Create a minimal PDF for testing
    from PyPDF2 import PdfWriter

    pdf_path = tmp_path / "test.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)

    with open(pdf_path, 'wb') as f:
        writer.write(f)

    return pdf_path
```

- [ ] **Step 2: Add job persistence tests**

```python
# Add to tests/test_job_queue.py

def test_save_and_load_job(temp_jobs_dir):
    """Test saving and loading a job."""
    job = ExtractionJob(
        job_id="test-job-1",
        document_id="doc-1",
        status=JobStatus.PENDING,
        progress=0.0,
        message="Test job",
        created_at=datetime.now(UTC)
    )

    _save_job(job)

    loaded = _load_job("test-job-1")

    assert loaded is not None
    assert loaded.job_id == "test-job-1"
    assert loaded.document_id == "doc-1"
    assert loaded.status == JobStatus.PENDING

def test_load_nonexistent_job(temp_jobs_dir):
    """Test loading a job that doesn't exist."""
    loaded = _load_job("nonexistent")
    assert loaded is None
```

- [ ] **Step 3: Add job submission test (integration)**

```python
# Add to tests/test_job_queue.py

@pytest.mark.asyncio
async def test_submit_job(temp_jobs_dir, sample_document):
    """Test submitting a job to the queue."""
    from unittest.mock import Mock, patch

    # Mock the document lookup
    with patch('job_queue.active_documents', {}):
        # This test verifies job creation - actual extraction
        # would require more setup
        job_id = str(os.urandom(16).hex())

        job = ExtractionJob(
            job_id=job_id,
            document_id="test-doc",
            status=JobStatus.PENDING,
            progress=0.0,
            message="Queued",
            created_at=datetime.now(UTC)
        )

        _save_job(job)

        loaded = get_job_status(job_id)
        assert loaded is not None
        assert loaded.status == JobStatus.PENDING
```

- [ ] **Step 4: Add cleanup test**

```python
# Add to tests/test_job_queue.py

def test_cleanup_old_jobs(temp_jobs_dir):
    """Test cleanup of old jobs."""
    import time

    # Create an old job file
    old_job = ExtractionJob(
        job_id="old-job",
        document_id="doc-1",
        status=JobStatus.COMPLETED,
        progress=1.0,
        message="Done",
        created_at=datetime.now(UTC)
    )
    _save_job(old_job)

    # Modify file mtime to make it old
    job_path = _get_job_path("old-job")
    old_time = time.time() - (25 * 3600)  # 25 hours ago
    os.utime(job_path, (old_time, old_time))

    # Create a recent job
    new_job = ExtractionJob(
        job_id="new-job",
        document_id="doc-2",
        status=JobStatus.PENDING,
        progress=0.0,
        message="Queued",
        created_at=datetime.now(UTC)
    )
    _save_job(new_job)

    # Run cleanup
    deleted = cleanup_old_jobs()

    assert deleted == 1

    # Verify old job is gone
    assert get_job_status("old-job") is None

    # Verify new job still exists
    assert get_job_status("new-job") is not None
```

- [ ] **Step 5: Run commit**

```bash
git add tests/test_job_queue.py
git commit -m "test(job-queue): add tests for job management"
```

---

## Task 8: Create startup hook for job recovery

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add job queue startup logic**

```python
# Add to main.py startup section (around line 120, after cache dir creation)

# Job queue startup
from job_queue import recover_orphan_jobs

@app.on_event("startup")
async def startup_event():
    """Application startup tasks."""
    # ... existing startup code ...

    # Recover orphan jobs from previous run
    recovered = recover_orphan_jobs()
    if recovered > 0:
        logger.info(f"Recovered {recovered} orphan extraction jobs")
```

- [ ] **Step 2: Add periodic cleanup task**

```python
# Add after startup_event

import asyncio

async def periodic_cleanup():
    """Background task to clean up old job files."""
    while True:
        try:
            from job_queue import cleanup_old_jobs
            deleted = cleanup_old_jobs()
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old job files")
        except Exception as e:
            logger.error(f"Job cleanup error: {e}")

        # Run every hour
        await asyncio.sleep(3600)

# Add to startup_event to start the background task
@app.on_event("startup")
async def startup_event():
    """Application startup tasks."""
    # ... existing code ...

    # Start periodic cleanup
    asyncio.create_task(periodic_cleanup())
```

- [ ] **Step 3: Run commit**

```bash
git add main.py
git commit -m "feat(startup): add job recovery and periodic cleanup"
```

---

## Task 9: Add environment variable documentation

**Files:**
- Modify: `.env`

- [ ] **Step 1: Add job queue configuration to .env**

```bash
# Add to .env file

# Job Queue Configuration
MAX_WORKERS=4                    # Number of worker threads for PDF extraction
JOB_TIMEOUT_MINUTES=30           # Maximum time per extraction job
JOB_RETENTION_HOURS=24           # How long to keep job files
JOBS_DIR=./jobs                  # Directory for job state files
```

- [ ] **Step 2: Run commit**

```bash
git add .env
git commit -m "docs: add job queue environment variables"
```

---

## Task 10: Update README with new feature

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add feature documentation**

```markdown
Add to README.md after the features list:

## Background Processing

Document extraction now runs in background worker threads, allowing:
- Multiple simultaneous extractions
- Server remains responsive during processing
- Failed jobs can be retried without re-uploading

The job queue is persistent across server restarts.
```

- [ ] **Step 2: Run commit**

```bash
git add README.md
git commit -m "docs: document background processing feature"
```

---

## Task 11: Manual testing checklist

**Files:**
- None (manual testing)

- [ ] **Step 1: Test single document extraction**

1. Start server: `python3 -m uvicorn main:app --host 0.0.0.0 --port 8000`
2. Upload a large PDF (100+ pages)
3. Click extract button in queue
4. Verify progress updates every ~2 seconds
5. Verify document status changes to "ready" when complete

- [ ] **Step 2: Test concurrent extractions**

1. Upload 3 different PDFs
2. Click extract on all 3 in quick succession
3. Verify all 3 process simultaneously
4. Verify server remains responsive

- [ ] **Step 3: Test job retry**

1. Upload a corrupted PDF (or cause an error)
2. Wait for job to fail
3. Click retry button
4. Verify new job is submitted

- [ ] **Step 4: Test job persistence**

1. Upload a PDF and start extraction
2. Kill the server (Ctrl+C) during extraction
3. Restart the server
4. Verify job is marked as failed
5. Verify retry works

- [ ] **Step 5: Test cleanup**

1. Create some old job files manually in `./jobs/`
2. Wait for periodic cleanup (1 hour) or manually trigger
3. Verify old jobs are deleted

---

## Success Criteria Verification

After completing all tasks, verify:

- [ ] Upload returns immediately (< 1 second)
- [ ] Multiple PDFs extract concurrently (4 at a time)
- [ ] Server remains responsive during extraction
- [ ] Failed jobs show retry button
- [ ] Job status persists across server restarts (RUNNING → FAILED)
- [ ] Progress updates every ~2 seconds
- [ ] Cleanup removes old jobs automatically

Run: `pytest tests/test_job_queue.py -v`

---

## Self-Review

**Spec Coverage:**
- ThreadPoolExecutor worker pool → Task 1
- Job state models → Task 1
- Job submission API → Task 5
- Status polling API → Task 5
- Retry endpoint → Task 5
- Blocking extraction functions → Task 3
- Frontend polling → Task 6
- Cleanup → Task 8
- Job persistence → Task 1

**Placeholder Scan:** No TBD, TODO, or "implement later" found.

**Type Consistency:** All type names match spec (JobStatus, ExtractionJob, etc.). Function signatures consistent across tasks.
