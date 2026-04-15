# tests/test_file_processor.py
import pytest
import asyncio
import hashlib
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
    active_sessions,
    active_documents
)

@pytest.fixture(autouse=True)
def cleanup_storage():
    """Clean up in-memory storage and temp files between tests."""
    # Get the temp directory path
    temp_path = os.environ.get('DOCUMENT_STORAGE_PATH')

    yield

    # Clear in-memory storage
    active_sessions.clear()
    active_documents.clear()

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
        checksum="abc123"
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
        checksum="abc123"
    )

    chunk_data = b"x" * 5242880  # 5MB chunk
    result = await receive_chunk(session.upload_id, 0, chunk_data)
    assert result is True
    assert 0 in session.received_chunks

@pytest.mark.asyncio
async def test_complete_upload():
    # Create 2 chunks (5MB + 5MB = 10MB)
    chunk1 = b"a" * 5242880
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

@pytest.mark.asyncio
async def test_upload_size_limit():
    with pytest.raises(ValueError, match="exceeds limit"):
        await initiate_upload(
            filename="huge.pdf",
            file_size=100 * 1024 * 1024,  # 100MB
            checksum="abc123"
        )

@pytest.mark.asyncio
async def test_missing_chunks():
    session = await initiate_upload(
        filename="incomplete.pdf",
        file_size=10485760,
        checksum="abc123"
    )

    # Only upload first chunk
    await receive_chunk(session.upload_id, 0, b"x" * 5242880)

    with pytest.raises(ValueError, match="Missing chunks"):
        await complete_upload(session.upload_id)
