# job_queue.py
import os
import uuid
import json
from datetime import datetime, timedelta, UTC
from typing import Optional, Dict, List, Callable
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, Future
import threading
import time

from pydantic import BaseModel, Field
from text_extractor import extract_pdf_text_blocking, extract_epub_text_blocking

# Configuration
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
JOB_TIMEOUT_MINUTES = int(os.getenv("JOB_TIMEOUT_MINUTES", "30"))
JOB_RETENTION_HOURS = int(os.getenv("JOB_RETENTION_HOURS", "24"))
JOBS_DIR = os.getenv("JOBS_DIR", "./jobs")


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


# In-memory job tracking
_active_jobs: Dict[str, Future] = {}
_cancelled_jobs: set[str] = set()
_jobs_lock = threading.Lock()
_job_files_lock = threading.Lock()


# Job storage paths
def _get_job_path(job_id: str) -> str:
    return os.path.join(JOBS_DIR, f"{job_id}.json")


def _ensure_jobs_dir():
    os.makedirs(JOBS_DIR, exist_ok=True)


def _save_job(job: ExtractionJob):
    _ensure_jobs_dir()
    path = _get_job_path(job.job_id)
    tmp_path = f"{path}.tmp.{threading.get_ident()}"
    with _job_files_lock:
        with open(tmp_path, 'w') as f:
            json.dump(job.model_dump(), f, default=str)
        os.replace(tmp_path, path)


def _load_job(job_id: str) -> Optional[ExtractionJob]:
    path = _get_job_path(job_id)
    with _job_files_lock:
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            data = json.load(f)
    return ExtractionJob(**data)


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

        deadline = time.monotonic() + JOB_TIMEOUT_MINUTES * 60

        # Both extractors report progress here, so cancellation and timeout live once.
        def callback(progress: float, message: str):
            with _jobs_lock:
                cancelled = job_id in _cancelled_jobs
            if cancelled:
                raise RuntimeError("Cancelled by user")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Extraction timed out after {JOB_TIMEOUT_MINUTES} minutes")
            _update_job_progress(job_id, progress, message)

        # Run extraction (blocking)
        callback(0.0, "Starting extraction...")
        chapters = _run_extraction_blocking(document_id, file_path, file_type, callback)
        callback(1.0, "Finishing extraction...")

        # Store result
        job.status = JobStatus.COMPLETED
        job.progress = 1.0
        job.message = "Extraction complete"
        job.completed_at = datetime.now(UTC)
        job.result = {"chapters": chapters}

        # Update document status to READY
        from file_processor import active_documents, save_document
        from models import DocumentStatus
        doc = active_documents.get(document_id)
        if doc:
            doc.status = DocumentStatus.READY
            doc.total_chapters = len(chapters)
            doc.extraction_progress = 1.0
            # Store chapters in metadata for content retrieval
            doc.metadata["chapters"] = chapters
            save_document(doc)

    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        job.message = f"Failed: {str(e)}"
        job.completed_at = datetime.now(UTC)

        # Update document status to ERROR
        from file_processor import active_documents, save_document
        from models import DocumentStatus
        doc = active_documents.get(document_id)
        if doc:
            doc.status = DocumentStatus.ERROR
            save_document(doc)

    finally:
        _save_job(job)
        # Remove from active jobs
        with _jobs_lock:
            _active_jobs.pop(job_id, None)
            _cancelled_jobs.discard(job_id)


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


def get_job_status(job_id: str) -> Optional[ExtractionJob]:
    """
    Get the current status of a job.

    Args:
        job_id: Job ID to query

    Returns:
        ExtractionJob or None if not found
    """
    return _load_job(job_id)


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
            _cancelled_jobs.add(job_id)
            if future.cancel():
                _active_jobs.pop(job_id, None)
                _cancelled_jobs.discard(job_id)

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
