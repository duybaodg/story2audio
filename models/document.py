# models/document.py
from datetime import datetime, timedelta, UTC
from typing import Optional, List, Dict, Set, Any
from pydantic import BaseModel, Field
from enum import Enum
import uuid

class DocumentStatus(str, Enum):
    UPLOADING = "uploading"
    EXTRACTING = "extracting"
    READY = "ready"
    ERROR = "error"

class FileType(str, Enum):
    PDF = "pdf"
    EPUB = "epub"

class ExtractionMethod(str, Enum):
    BASIC = "basic"
    ADVANCED = "advanced"
    OCR = "ocr"

class UploadSession(BaseModel):
    upload_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    document_id: Optional[str] = None
    filename: str
    total_size: int
    chunk_size: int = 5242880  # 5MB default
    received_chunks: Set[int] = Field(default_factory=set)
    temp_dir: str
    checksum: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

class Document(BaseModel):
    document_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    file_type: FileType
    file_size: int
    upload_date: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(hours=24))
    status: DocumentStatus = DocumentStatus.UPLOADING
    total_pages: Optional[int] = None
    total_chapters: Optional[int] = None
    extraction_progress: float = 0.0
    file_path: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class Chapter(BaseModel):
    chapter_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str
    chapter_number: int
    title: str
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    text_preview: str
    full_text: Optional[str] = None
    word_count: int = 0
    estimated_audio_duration: float = 0.0
    quality_score: float = 1.0
    needs_ocr: bool = False
    ocr_processed: bool = False
    extraction_method: ExtractionMethod = ExtractionMethod.BASIC
    language: str = "en"
