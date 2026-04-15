# file_processor.py
import os
import shutil
import hashlib
import uuid
import asyncio
from datetime import datetime, timedelta, UTC
from typing import Dict, Optional
from pathlib import Path
from models import UploadSession, Document, DocumentStatus, FileType

def _get_config():
    """Get configuration from environment variables."""
    return {
        'UPLOAD_MAX_SIZE_MB': int(os.getenv("UPLOAD_MAX_SIZE_MB", "50")),
        'UPLOAD_CHUNK_SIZE': int(os.getenv("UPLOAD_CHUNK_SIZE", "5242880")),  # 5MB
        'UPLOAD_SESSION_EXPIRY_HOURS': int(os.getenv("UPLOAD_SESSION_EXPIRY_HOURS", "24")),
        'DOCUMENT_STORAGE_PATH': os.getenv("DOCUMENT_STORAGE_PATH", "/app/documents")
    }

def _get_storage_paths():
    """Get storage paths from configuration."""
    config = _get_config()
    base_path = config['DOCUMENT_STORAGE_PATH']
    return {
        'UPLOADS_DIR': os.path.join(base_path, "uploads"),
        'ASSEMBLED_DIR': os.path.join(base_path, "assembled"),
        'SESSIONS_DIR': os.path.join(base_path, "sessions")
    }

# In-memory storage
active_sessions: Dict[str, UploadSession] = {}
active_documents: Dict[str, Document] = {}

def _ensure_directories():
    """Create required directories if they don't exist."""
    paths = _get_storage_paths()
    for directory in paths.values():
        os.makedirs(directory, exist_ok=True)

async def initiate_upload(
    filename: str,
    file_size: int,
    checksum: str
) -> UploadSession:
    """
    Initiate a chunked upload session.

    Raises:
        ValueError: If file size exceeds limit
    """
    _ensure_directories()

    config = _get_config()
    paths = _get_storage_paths()

    max_size = config['UPLOAD_MAX_SIZE_MB'] * 1024 * 1024
    if file_size > max_size:
        raise ValueError(
            f"File size {file_size} exceeds limit {max_size} bytes "
            f"({config['UPLOAD_MAX_SIZE_MB']}MB)"
        )

    upload_id = str(uuid.uuid4())
    temp_dir = os.path.join(paths['UPLOADS_DIR'], upload_id)
    os.makedirs(temp_dir, exist_ok=True)

    session = UploadSession(
        upload_id=upload_id,
        filename=filename,
        total_size=file_size,
        chunk_size=config['UPLOAD_CHUNK_SIZE'],
        temp_dir=temp_dir,
        checksum=checksum
    )

    active_sessions[upload_id] = session
    return session

async def receive_chunk(
    upload_id: str,
    chunk_number: int,
    chunk_data: bytes
) -> bool:
    """
    Receive and store a single chunk.

    Raises:
        ValueError: If upload_id not found or chunk invalid
    """
    if upload_id not in active_sessions:
        raise ValueError(f"Upload session {upload_id} not found")

    session = active_sessions[upload_id]

    # Validate chunk size (last chunk may be smaller)
    expected_size = session.chunk_size
    is_last_chunk = (chunk_number * session.chunk_size + len(chunk_data)) >= session.total_size

    if not is_last_chunk and len(chunk_data) != session.chunk_size:
        raise ValueError(
            f"Chunk {chunk_number} size {len(chunk_data)} "
            f"doesn't match expected {expected_size}"
        )

    # Store chunk
    chunk_path = os.path.join(session.temp_dir, f"chunk_{chunk_number}")
    with open(chunk_path, "wb") as f:
        f.write(chunk_data)

    session.received_chunks.add(chunk_number)
    return True

async def complete_upload(upload_id: str) -> Document:
    """
    Complete upload by assembling chunks and creating document.

    Raises:
        ValueError: If chunks missing or checksum mismatch
    """
    if upload_id not in active_sessions:
        raise ValueError(f"Upload session {upload_id} not found")

    session = active_sessions[upload_id]
    paths = _get_storage_paths()

    # Check all chunks received
    expected_chunks = (session.total_size + session.chunk_size - 1) // session.chunk_size
    missing_chunks = set(range(expected_chunks)) - session.received_chunks

    if missing_chunks:
        raise ValueError(f"Missing chunks: {sorted(missing_chunks)}")

    # Assemble chunks
    assembled_path = os.path.join(paths['ASSEMBLED_DIR'], f"{upload_id}.temp")
    file_checksum = hashlib.md5()

    with open(assembled_path, "wb") as outfile:
        for i in range(expected_chunks):
            chunk_path = os.path.join(session.temp_dir, f"chunk_{i}")
            with open(chunk_path, "rb") as infile:
                chunk_data = infile.read()
                outfile.write(chunk_data)
                file_checksum.update(chunk_data)

    # Verify checksum
    calculated_checksum = file_checksum.hexdigest()
    if calculated_checksum != session.checksum:
        os.remove(assembled_path)
        raise ValueError(
            f"Checksum mismatch: expected {session.checksum}, "
            f"got {calculated_checksum}"
        )

    # Determine file type from extension
    ext = Path(session.filename).suffix.lower().lstrip(".")
    try:
        file_type = FileType(ext)
    except ValueError:
        os.remove(assembled_path)
        raise ValueError(f"Unsupported file type: {ext}")

    # Create document
    final_path = os.path.join(paths['ASSEMBLED_DIR'], upload_id, os.path.basename(session.filename))
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    shutil.move(assembled_path, final_path)

    document = Document(
        document_id=upload_id,
        filename=session.filename,
        file_type=file_type,
        file_size=session.total_size,
        file_path=final_path
    )

    active_documents[upload_id] = document
    del active_sessions[upload_id]

    # Clean up temp directory
    shutil.rmtree(session.temp_dir, ignore_errors=True)

    return document

async def get_document(document_id: str) -> Optional[Document]:
    """Get document by ID."""
    return active_documents.get(document_id)

async def cleanup_expired_sessions():
    """
    Background task to clean up expired documents and orphaned sessions.
    Run this periodically (e.g., every hour).
    """
    config = _get_config()
    now = datetime.now(UTC)

    # Clean up expired documents
    expired_docs = [
        doc_id for doc_id, doc in active_documents.items()
        if doc.expires_at <= now
    ]

    for doc_id in expired_docs:
        doc = active_documents[doc_id]
        # Delete files
        if os.path.exists(doc.file_path):
            try:
                os.remove(doc.file_path)
            except OSError:
                pass
        # Delete chapter directory
        doc_dir = os.path.dirname(doc.file_path)
        if os.path.exists(doc_dir):
            try:
                shutil.rmtree(doc_dir)
            except OSError:
                pass
        del active_documents[doc_id]

    # Clean up orphaned upload sessions (>24h old)
    orphan_sessions = [
        session_id for session_id, session in active_sessions.items()
        if (now - session.created_at) > timedelta(hours=config['UPLOAD_SESSION_EXPIRY_HOURS'])
    ]

    for session_id in orphan_sessions:
        session = active_sessions[session_id]
        # Delete temp directory
        if os.path.exists(session.temp_dir):
            try:
                shutil.rmtree(session.temp_dir)
            except OSError:
                pass
        del active_sessions[session_id]
