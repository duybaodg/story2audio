# file_processor.py
import os
import shutil
import hashlib
import uuid
import asyncio
import json
import re
import zipfile
from datetime import datetime, timedelta, UTC
from typing import Dict, Optional, List
from pathlib import Path
from models import UploadSession, Document, DocumentStatus, FileType
from fastapi import BackgroundTasks
import logging

logger = logging.getLogger(__name__)

_CHECKSUM_RE = re.compile(r"^[a-f0-9]{32}$")

def _get_config():
    """Get configuration from environment variables."""
    return {
        'UPLOAD_MAX_SIZE_MB': int(os.getenv("UPLOAD_MAX_SIZE_MB", "50")),
        'UPLOAD_CHUNK_SIZE': int(os.getenv("UPLOAD_CHUNK_SIZE", "5242880")),  # 5MB
        'UPLOAD_SESSION_EXPIRY_HOURS': int(os.getenv("UPLOAD_SESSION_EXPIRY_HOURS", "12")),
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


def _get_session_path(upload_id: str) -> str:
    """Get path to session file."""
    paths = _get_storage_paths()
    return os.path.join(paths['SESSIONS_DIR'], f"{upload_id}.json")


async def save_session(session: UploadSession) -> None:
    """
    Persist session to disk for recovery.

    Args:
        session: UploadSession to persist
    """
    _ensure_directories()
    path = _get_session_path(session.upload_id)

    # Convert to dict, handling non-serializable types
    session_data = session.model_dump()
    # Convert set to list for JSON serialization
    session_data['received_chunks'] = list(session_data.get('received_chunks', []))

    with open(path, 'w') as f:
        json.dump(session_data, f, default=str)

    # Also keep in memory
    active_sessions[session.upload_id] = session


async def load_session(upload_id: str) -> Optional[UploadSession]:
    """
    Load session from disk.

    Args:
        upload_id: Session ID to load

    Returns:
        UploadSession or None if not found
    """
    path = _get_session_path(upload_id)
    if not os.path.exists(path):
        return None

    with open(path, 'r') as f:
        data = json.load(f)

    # Convert received_chunks from list back to set
    if 'received_chunks' in data and isinstance(data['received_chunks'], list):
        data['received_chunks'] = set(data['received_chunks'])

    # Reconstruct UploadSession with proper types
    session = UploadSession(**data)

    # Also restore to memory
    active_sessions[upload_id] = session
    return session


async def get_pending_sessions() -> List[UploadSession]:
    """
    Return all non-expired pending sessions.

    Returns:
        List of UploadSession that are less than 24 hours old
    """
    paths = _get_storage_paths()
    sessions_dir = paths['SESSIONS_DIR']

    if not os.path.exists(sessions_dir):
        return []

    config = _get_config()
    now = datetime.now(UTC)
    expiry = timedelta(hours=config['UPLOAD_SESSION_EXPIRY_HOURS'])

    pending = []
    for filename in os.listdir(sessions_dir):
        if not filename.endswith('.json'):
            continue

        upload_id = filename[:-5]  # Remove .json
        session = await load_session(upload_id)
        if session and (now - session.created_at) < expiry:
            pending.append(session)

    return pending


# In-memory storage
active_sessions: Dict[str, UploadSession] = {}
active_documents: Dict[str, Document] = {}
document_queue: List[str] = []  # Ordered list of document_ids in queue

def _ensure_directories():
    """Create required directories if they don't exist."""
    paths = _get_storage_paths()
    for directory in paths.values():
        os.makedirs(directory, exist_ok=True)


def _normalize_filename(filename: str) -> str:
    """Return a safe basename with a supported extension."""
    safe_name = os.path.basename((filename or "").replace("\\", "/")).strip()
    if not safe_name:
        raise ValueError("Filename is required")
    if len(safe_name) > 255:
        raise ValueError("Filename is too long")
    if any(ord(char) < 32 for char in safe_name):
        raise ValueError("Filename contains invalid characters")

    ext = Path(safe_name).suffix.lower().lstrip(".")
    try:
        FileType(ext)
    except ValueError:
        raise ValueError(f"Unsupported file type: {ext}")
    return safe_name


def _normalize_checksum(checksum: str) -> str:
    """Validate the expected MD5 checksum format."""
    normalized = (checksum or "").strip().lower()
    if not _CHECKSUM_RE.fullmatch(normalized):
        raise ValueError("Checksum must be a 32-character hexadecimal MD5 digest")
    return normalized


def _validate_file_size(file_size: int, max_size: int, max_size_mb: int) -> None:
    if file_size <= 0:
        raise ValueError("File size must be greater than zero")
    if file_size > max_size:
        raise ValueError(
            f"File size {file_size} exceeds limit {max_size} bytes "
            f"({max_size_mb}MB)"
        )


def _detect_file_type(path: str) -> FileType:
    """Detect supported document types from file content, not just extension."""
    with open(path, "rb") as f:
        header = f.read(8)

    if header.startswith(b"%PDF-"):
        return FileType.PDF

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            try:
                mimetype = zf.read("mimetype").strip()
            except KeyError:
                mimetype = b""
            if mimetype == b"application/epub+zip" or "META-INF/container.xml" in names:
                return FileType.EPUB

    raise ValueError("File content does not match supported PDF/EPUB types")


def _remove_file(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

async def initiate_upload(
    filename: str,
    file_size: int,
    checksum: str,
    owner_session: Optional[str] = None,
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
    safe_filename = _normalize_filename(filename)
    expected_checksum = _normalize_checksum(checksum)
    _validate_file_size(file_size, max_size, config['UPLOAD_MAX_SIZE_MB'])

    for session in active_sessions.values():
        if (
            session.filename == safe_filename
            and session.total_size == file_size
            and session.checksum == expected_checksum
            and session.owner_session == owner_session
        ):
            return session

    upload_id = str(uuid.uuid4())
    temp_dir = os.path.join(paths['UPLOADS_DIR'], upload_id)
    os.makedirs(temp_dir, exist_ok=True)

    session = UploadSession(
        upload_id=upload_id,
        filename=safe_filename,
        total_size=file_size,
        chunk_size=config['UPLOAD_CHUNK_SIZE'],
        temp_dir=temp_dir,
        checksum=expected_checksum,
        owner_session=owner_session,
    )

    # Persist session to disk
    await save_session(session)

    return session


def get_expected_chunk_size(upload_id: str, chunk_number: int) -> int:
    """Return expected byte size for a chunk in an active upload."""
    if upload_id not in active_sessions:
        raise ValueError(f"Upload session {upload_id} not found")

    session = active_sessions[upload_id]
    expected_chunks = (session.total_size + session.chunk_size - 1) // session.chunk_size
    if chunk_number < 0 or chunk_number >= expected_chunks:
        raise ValueError(
            f"Invalid chunk number {chunk_number}; expected 0 to {expected_chunks - 1}"
        )

    if chunk_number == expected_chunks - 1:
        return session.total_size - (chunk_number * session.chunk_size)
    return session.chunk_size

async def receive_chunk(
    upload_id: str,
    chunk_number: int,
    chunk_data: bytes
) -> bool:
    """
    Receive and store a single chunk.

    Raises:
        ValueError: If upload_id not found or chunk invalid
        OSError: If file system operations fail
    """
    expected_size = get_expected_chunk_size(upload_id, chunk_number)
    session = active_sessions[upload_id]

    if len(chunk_data) != expected_size:
        raise ValueError(
            f"Chunk {chunk_number} size {len(chunk_data)} "
            f"doesn't match expected {expected_size} "
            f"(file: {session.total_size} bytes, chunk_size: {session.chunk_size})"
        )

    # Store chunk
    chunk_path = os.path.join(session.temp_dir, f"chunk_{chunk_number}")
    if chunk_number in session.received_chunks:
        try:
            if os.path.getsize(chunk_path) == expected_size:
                with open(chunk_path, "rb") as existing:
                    if existing.read() != chunk_data:
                        raise ValueError(
                            f"Chunk {chunk_number} was already uploaded with different content"
                        )
                return True
        except OSError:
            pass

    # Ensure temp directory exists
    os.makedirs(session.temp_dir, exist_ok=True)
    try:
        with open(chunk_path, "wb") as f:
            f.write(chunk_data)
    except OSError as e:
        raise ValueError(f"Failed to write chunk {chunk_number}: {e}")

    session.received_chunks.add(chunk_number)

    # Persist updated session
    await save_session(session)

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

    try:
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
            raise ValueError(
                f"Checksum mismatch: expected {session.checksum}, "
                f"got {calculated_checksum}"
            )

        # Determine file type from extension and verify content matches.
        ext = Path(session.filename).suffix.lower().lstrip(".")
        file_type = FileType(ext)
        detected_type = _detect_file_type(assembled_path)
        if detected_type != file_type:
            raise ValueError("File content does not match file extension")

        # Create document
        final_path = os.path.join(paths['ASSEMBLED_DIR'], upload_id, session.filename)
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        shutil.move(assembled_path, final_path)
    except Exception:
        _remove_file(assembled_path)
        raise

    document = Document(
        document_id=upload_id,
        filename=session.filename,
        file_type=file_type,
        file_size=session.total_size,
        file_path=final_path,
        owner_session=session.owner_session,
        metadata={"checksum": session.checksum}
    )

    active_documents[upload_id] = document
    document_queue.append(upload_id)  # Add to queue
    del active_sessions[upload_id]

    # Delete session file
    session_path = _get_session_path(upload_id)
    if os.path.exists(session_path):
        os.remove(session_path)

    # Clean up temp directory
    shutil.rmtree(session.temp_dir, ignore_errors=True)

    return document

async def get_document(document_id: str, owner_session: Optional[str] = None) -> Optional[Document]:
    """Get document by ID."""
    document = active_documents.get(document_id)
    if document and owner_session is not None and document.owner_session != owner_session:
        return None
    return document

async def get_queue(owner_session: Optional[str] = None) -> List[Dict]:
    """Get all documents in queue with their info."""
    queue_items = []
    for doc_id in document_queue:
        doc = active_documents.get(doc_id)
        if doc and (owner_session is None or doc.owner_session == owner_session):
            queue_items.append({
                "document_id": doc_id,
                "filename": doc.filename,
                "file_type": doc.file_type.value,
                "file_size": doc.file_size,
                "status": doc.status.value,
                "upload_date": doc.upload_date.isoformat(),
                "total_chapters": doc.total_chapters
            })
    return queue_items

async def delete_document(document_id: str, owner_session: Optional[str] = None) -> bool:
    """Delete a document and its files."""
    doc = active_documents.get(document_id)
    if not doc or (owner_session is not None and doc.owner_session != owner_session):
        return False

    # Remove from queue
    if document_id in document_queue:
        document_queue.remove(document_id)

    # Delete files
    if os.path.exists(doc.file_path):
        try:
            os.remove(doc.file_path)
        except OSError:
            pass

    doc_dir = os.path.dirname(doc.file_path)
    if os.path.exists(doc_dir):
        try:
            shutil.rmtree(doc_dir, ignore_errors=True)
        except OSError:
            pass

    del active_documents[document_id]
    return True

async def cleanup_expired_sessions():
    """
    Background task to clean up expired documents and orphaned sessions.
    Run this periodically (e.g., every hour).
    """
    config = _get_config()
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=config['UPLOAD_SESSION_EXPIRY_HOURS'])

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

    # Clean up orphaned upload sessions older than the configured retention.
    orphan_sessions = [
        session_id for session_id, session in active_sessions.items()
        if session.created_at < cutoff
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

    paths = _get_storage_paths()
    for root in (paths['UPLOADS_DIR'], paths['ASSEMBLED_DIR'], paths['SESSIONS_DIR']):
        if not os.path.exists(root):
            continue
        for name in os.listdir(root):
            path = os.path.join(root, name)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=UTC)
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except OSError:
                pass


async def start_cleanup_scheduler():
    """
    Start background task to clean up expired sessions.
    Should be called during application startup.
    """
    while True:
        try:
            await cleanup_expired_sessions()
            logger.info("Completed cleanup of expired sessions")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        await asyncio.sleep(3600)  # Run every hour


def register_cleanup_task(background_tasks: BackgroundTasks):
    """
    Register cleanup scheduler task to run in background continuously.
    Call this during FastAPI startup event.
    """
    background_tasks.add_task(start_cleanup_scheduler)
