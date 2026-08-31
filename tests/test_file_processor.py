# tests/test_file_processor.py
import pytest
import hashlib
from datetime import datetime, timedelta
from datetime import UTC
from pathlib import Path
import sys
import os
import tempfile
import shutil

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set environment variable before importing module
os.environ['DOCUMENT_STORAGE_PATH'] = tempfile.mkdtemp()

from file_processor import (
    initiate_upload,
    receive_chunk,
    complete_upload,
    get_document,
    cleanup_expired_sessions,
    recover_documents,
    active_sessions,
    active_documents,
    document_queue,
)
from models import Document, FileType

@pytest.fixture(autouse=True)
def cleanup_storage():
    """Clean up in-memory storage and temp files between tests."""
    # Get the temp directory path
    temp_path = os.environ.get('DOCUMENT_STORAGE_PATH')

    yield

    # Clear in-memory storage
    active_sessions.clear()
    active_documents.clear()
    document_queue.clear()

    # Clean up temp files
    if temp_path and os.path.exists(temp_path):
        for item in os.listdir(temp_path):
            item_path = os.path.join(temp_path, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path, ignore_errors=True)

@pytest.mark.asyncio
async def test_initiate_upload():
    session = await initiate_upload(
        filename="test.pdf",
        file_size=10485760,  # 10MB
        checksum="0" * 32
    )
    assert session.upload_id
    assert session.filename == "test.pdf"
    assert session.total_size == 10485760
    assert session.chunk_size == 5242880  # 5MB default
    assert session.upload_id in active_sessions

@pytest.mark.asyncio
async def test_receive_chunk():
    session = await initiate_upload(
        filename="test.pdf",
        file_size=10485760,
        checksum="0" * 32
    )

    chunk_data = b"x" * 5242880  # 5MB chunk
    result = await receive_chunk(session.upload_id, 0, chunk_data)
    assert result is True
    assert 0 in session.received_chunks


@pytest.mark.asyncio
async def test_receive_chunk_rejects_invalid_chunk_numbers():
    session = await initiate_upload(
        filename="test.pdf",
        file_size=10485760,
        checksum="0" * 32
    )

    chunk_data = b"x" * 5242880

    with pytest.raises(ValueError, match="Invalid chunk number -1"):
        await receive_chunk(session.upload_id, -1, chunk_data)

    with pytest.raises(ValueError, match="Invalid chunk number 2"):
        await receive_chunk(session.upload_id, 2, chunk_data)

    assert session.received_chunks == set()


@pytest.mark.asyncio
async def test_complete_upload():
    # Create 2 chunks (5MB + 5MB = 10MB)
    chunk1 = b"%PDF-1.4\n" + (b"a" * (5242880 - 9))
    chunk2 = b"b" * 5242880

    checksum = hashlib.md5(chunk1 + chunk2).hexdigest()

    session = await initiate_upload(
        filename="complete.pdf",
        file_size=10485760,
        checksum=checksum
    )

    await receive_chunk(session.upload_id, 0, chunk1)
    await receive_chunk(session.upload_id, 1, chunk2)

    document = await complete_upload(session.upload_id)

    assert document.document_id == session.upload_id
    assert document.filename == "complete.pdf"
    assert document.file_size == 10485760
    assert document.status.value == "uploading"
    assert session.upload_id not in active_sessions
    assert document.document_id in active_documents
    assert Path(document.file_path).exists()
    assert (Path(document.file_path).parent / "document.json").exists()

    active_documents.clear()
    document_queue.clear()
    assert recover_documents() == 1
    assert (await get_document(document.document_id)).filename == "complete.pdf"

@pytest.mark.asyncio
async def test_upload_size_limit():
    with pytest.raises(ValueError, match="exceeds limit"):
        await initiate_upload(
            filename="huge.pdf",
            file_size=100 * 1024 * 1024,  # 100MB
            checksum="0" * 32
        )

@pytest.mark.asyncio
async def test_missing_chunks():
    session = await initiate_upload(
        filename="incomplete.pdf",
        file_size=10485760,
        checksum="0" * 32
    )

    # Only upload first chunk
    await receive_chunk(session.upload_id, 0, b"x" * 5242880)

    with pytest.raises(ValueError, match="Missing chunks"):
        await complete_upload(session.upload_id)

@pytest.mark.asyncio
async def test_cleanup_expired_documents():
    # Create expired document
    doc = Document(
        document_id="expired-doc",
        filename="expired.pdf",
        file_type=FileType.PDF,
        file_size=1000,
        file_path="/tmp/test_expired.pdf",
        expires_at=datetime.now(UTC) - timedelta(hours=1)
    )
    active_documents["expired-doc"] = doc

    # Run cleanup
    await cleanup_expired_sessions()

    # Verify expired doc removed
    assert "expired-doc" not in active_documents


@pytest.mark.asyncio
async def test_cleanup_removes_old_upload_files():
    base = os.environ['DOCUMENT_STORAGE_PATH']
    old_dir = Path(base) / "uploads" / "old-upload"
    old_dir.mkdir(parents=True, exist_ok=True)
    old_file = old_dir / "chunk_0"
    old_file.write_bytes(b"old")

    old_time = (datetime.now(UTC) - timedelta(hours=13)).timestamp()
    os.utime(old_file, (old_time, old_time))
    os.utime(old_dir, (old_time, old_time))

    await cleanup_expired_sessions()

    assert not old_dir.exists()


@pytest.mark.asyncio
async def test_initiate_upload_validates_metadata():
    with pytest.raises(ValueError, match="File size must be greater than zero"):
        await initiate_upload("bad.pdf", 0, "0" * 32)

    with pytest.raises(ValueError, match="Checksum must be"):
        await initiate_upload("bad.pdf", 1, "abc123")

    with pytest.raises(ValueError, match="Unsupported file type"):
        await initiate_upload("bad.exe", 1, "0" * 32)


@pytest.mark.asyncio
async def test_duplicate_upload_returns_active_session():
    checksum = "1" * 32
    session = await initiate_upload("duplicate.pdf", 100, checksum)
    duplicate = await initiate_upload("duplicate.pdf", 100, checksum)

    assert duplicate.upload_id == session.upload_id
    assert len(active_sessions) == 1


@pytest.mark.asyncio
async def test_duplicate_chunk_is_idempotent():
    session = await initiate_upload("duplicate-chunk.pdf", 10, "2" * 32)
    chunk_data = b"x" * 10

    assert await receive_chunk(session.upload_id, 0, chunk_data) is True
    assert await receive_chunk(session.upload_id, 0, chunk_data) is True
    assert session.received_chunks == {0}


@pytest.mark.asyncio
async def test_duplicate_chunk_rejects_different_content():
    session = await initiate_upload("duplicate-chunk.pdf", 10, "2" * 32)

    await receive_chunk(session.upload_id, 0, b"x" * 10)

    with pytest.raises(ValueError, match="different content"):
        await receive_chunk(session.upload_id, 0, b"y" * 10)


@pytest.mark.asyncio
async def test_complete_upload_rejects_content_type_mismatch():
    chunk_data = b"not a pdf"
    checksum = hashlib.md5(chunk_data).hexdigest()
    session = await initiate_upload("fake.pdf", len(chunk_data), checksum)

    await receive_chunk(session.upload_id, 0, chunk_data)

    with pytest.raises(ValueError, match="File content"):
        await complete_upload(session.upload_id)
