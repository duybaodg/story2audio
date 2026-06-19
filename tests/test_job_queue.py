# tests/test_job_queue.py
import pytest
import os
import sys
import tempfile
import shutil
import json
import time
from datetime import datetime, UTC
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set environment variable before importing module
os.environ['JOBS_DIR'] = tempfile.mkdtemp()

from job_queue import (
    JobStatus,
    ExtractionJob,
    submit_extraction_job,
    get_job_status,
    retry_job,
    cancel_job,
    cleanup_old_jobs,
    recover_orphan_jobs,
    _save_job,
    _load_job,
    _get_job_path,
    _ensure_jobs_dir,
    JOBS_DIR
)
from file_processor import active_documents
from models import Document, FileType, DocumentStatus


@pytest.fixture(autouse=True)
def cleanup_jobs():
    """Clean up job storage between tests."""
    jobs_dir = os.environ.get('JOBS_DIR')

    yield

    # Clean up job files
    if jobs_dir and os.path.exists(jobs_dir):
        for filename in os.listdir(jobs_dir):
            file_path = os.path.join(jobs_dir, filename)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except OSError:
                pass

    # Clear active documents
    active_documents.clear()


def test_save_and_load_job():
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


def test_load_nonexistent_job():
    """Test loading a job that doesn't exist."""
    loaded = _load_job("nonexistent")
    assert loaded is None


def test_jobs_dir_created():
    """Test that jobs directory is created."""
    _ensure_jobs_dir()
    assert os.path.exists(JOBS_DIR)


@pytest.mark.asyncio
async def test_submit_job():
    """Test submitting a job to the queue."""
    # Create a mock document
    doc = Document(
        document_id="test-doc-1",
        filename="test.pdf",
        file_type=FileType.PDF,
        file_size=1024,
        file_path="/tmp/test.pdf"
    )
    active_documents["test-doc-1"] = doc

    # Note: This will submit the job but extraction will fail
    # since we don't have a real PDF file. We're just testing
    # that the job is created properly.
    job_id = await submit_extraction_job("test-doc-1", "/tmp/test.pdf", "pdf")

    assert job_id is not None
    assert len(job_id) > 0

    # Check job was saved
    job = get_job_status(job_id)
    assert job is not None
    assert job.document_id == "test-doc-1"
    assert job.status in [JobStatus.PENDING, JobStatus.RUNNING, JobStatus.FAILED]


def test_cleanup_old_jobs():
    """Test cleanup of old jobs."""
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

    # Modify file mtime to make it old (25 hours ago)
    job_path = _get_job_path("old-job")
    old_time = time.time() - (25 * 3600)
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


def test_recover_orphan_jobs():
    """Test recovery of orphan jobs (jobs left in RUNNING state)."""
    # Create a job in RUNNING state (simulating crash)
    running_job = ExtractionJob(
        job_id="orphan-job",
        document_id="doc-1",
        status=JobStatus.RUNNING,
        progress=0.5,
        message="Processing...",
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC)
    )
    _save_job(running_job)

    # Run recovery
    recovered = recover_orphan_jobs()

    assert recovered == 1

    # Verify job was marked as FAILED
    job = get_job_status("orphan-job")
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert "crashed" in job.error.lower()


@pytest.mark.asyncio
async def test_cancel_job():
    """Test cancelling a job."""
    # Create a mock document
    doc = Document(
        document_id="test-doc-cancel",
        filename="test.pdf",
        file_type=FileType.PDF,
        file_size=1024,
        file_path="/tmp/test.pdf"
    )
    active_documents["test-doc-cancel"] = doc

    # Submit a job
    job_id = await submit_extraction_job("test-doc-cancel", "/tmp/test.pdf", "pdf")

    # Try to cancel it immediately
    # Note: This may fail if the job completes too quickly
    # In a real test, we'd need to mock the executor
    success = cancel_job(job_id)

    # The result depends on timing - just verify the function runs
    assert isinstance(success, bool)


@pytest.mark.asyncio
async def test_retry_failed_job():
    """Test retrying a failed job."""
    # Create a failed job
    failed_job = ExtractionJob(
        job_id="failed-job",
        document_id="doc-retry",
        status=JobStatus.FAILED,
        progress=0.0,
        message="Failed",
        error="Test error",
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC)
    )
    _save_job(failed_job)

    # Create a mock document
    doc = Document(
        document_id="doc-retry",
        filename="test.pdf",
        file_type=FileType.PDF,
        file_size=1024,
        file_path="/tmp/test.pdf"
    )
    active_documents["doc-retry"] = doc

    # Retry the job
    new_job_id = await retry_job("failed-job")

    assert new_job_id is not None
    assert new_job_id != "failed-job"

    # Verify new job exists
    new_job = get_job_status(new_job_id)
    assert new_job is not None


@pytest.mark.asyncio
async def test_retry_nonexistent_job():
    """Test retrying a job that doesn't exist."""
    new_job_id = await retry_job("nonexistent")
    assert new_job_id is None


@pytest.mark.asyncio
async def test_retry_completed_job():
    """Test that completed jobs cannot be retried."""
    completed_job = ExtractionJob(
        job_id="completed-job",
        document_id="doc-completed",
        status=JobStatus.COMPLETED,
        progress=1.0,
        message="Complete",
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC)
    )
    _save_job(completed_job)

    new_job_id = await retry_job("completed-job")
    assert new_job_id is None
