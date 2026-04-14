# tests/test_models.py
import pytest
from datetime import datetime, timedelta, UTC
from models import UploadSession, Document, Chapter, DocumentStatus, FileType

def test_upload_session_creation():
    session = UploadSession(
        filename="test.pdf",
        total_size=10485760,
        temp_dir="/tmp/uploads",
        checksum="abc123"
    )
    assert session.upload_id
    assert session.filename == "test.pdf"
    assert session.total_size == 10485760
    assert session.chunk_size == 5242880
    assert len(session.received_chunks) == 0
    assert session.created_at

def test_document_creation_defaults():
    doc = Document(
        filename="ebook.pdf",
        file_type=FileType.PDF,
        file_size=15728640,
        file_path="/app/documents/assembled/doc.pdf"
    )
    assert doc.document_id
    assert doc.status == DocumentStatus.UPLOADING
    assert doc.expires_at > datetime.now(UTC)
    assert doc.expires_at < datetime.now(UTC) + timedelta(hours=25)
    assert doc.extraction_progress == 0.0

def test_chapter_creation():
    chapter = Chapter(
        document_id="doc-123",
        chapter_number=1,
        title="Introduction",
        text_preview="This is the first chapter..."
    )
    assert chapter.chapter_id
    assert chapter.quality_score == 1.0
    assert chapter.needs_ocr == False
    assert chapter.word_count == 0
