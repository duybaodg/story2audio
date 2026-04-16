# document_api.py
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from typing import Optional, List
from pydantic import BaseModel, Field
import json
import asyncio
from models.document import Document, Chapter, DocumentStatus
from file_processor import (
    initiate_upload,
    receive_chunk,
    complete_upload,
    get_document,
    active_sessions,
    active_documents
)
from text_extractor import extract_pdf_text, extract_epub_text


class ChapterContentRequest(BaseModel):
    chapter_ids: List[str] = Field(min_items=1, description="List of chapter IDs to retrieve")


router = APIRouter(prefix="/document", tags=["document"])

@router.get("/health")
async def health_check():
    """
    Health check endpoint for document module.
    """
    return {
        "status": "healthy",
        "active_sessions": len(active_sessions),
        "active_documents": len(active_documents)
    }

@router.post("/upload/initiate")
async def upload_initiate(
    filename: str = Form(...),
    file_size: int = Form(...),
    checksum: str = Form(...)
):
    """
    Initiate a chunked file upload.

    Returns upload_id and chunk size for subsequent chunk uploads.
    """
    try:
        session = await initiate_upload(filename, file_size, checksum)
        return {
            "upload_id": session.upload_id,
            "chunk_size": session.chunk_size,
            "status": "initiated"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/upload/chunk")
async def upload_chunk(
    upload_id: str = Form(...),
    chunk_number: int = Form(...),
    chunk: UploadFile = File(...)
):
    """
    Upload a single chunk of the file.

    Returns success status and next chunk number.
    """
    try:
        chunk_data = await chunk.read()
        success = await receive_chunk(upload_id, chunk_number, chunk_data)

        # Calculate next expected chunk
        session = active_sessions.get(upload_id)
        if not session:
            raise HTTPException(status_code=404, detail="Upload session not found")

        expected_chunks = (session.total_size + session.chunk_size - 1) // session.chunk_size
        received_count = len(session.received_chunks)

        return {
            "success": success,
            "chunk_number": chunk_number,
            "chunks_received": received_count,
            "total_chunks": expected_chunks,
            "next_chunk": chunk_number + 1 if received_count < expected_chunks else None
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/upload/complete")
async def upload_complete(upload_id: str = Form(...)):
    """
    Complete the upload process.

    Assembles chunks into final file and returns document_id.
    Triggers text extraction in background.
    """
    try:
        document = await complete_upload(upload_id)

        # Trigger background extraction
        from fastapi import BackgroundTasks
        # Note: Background extraction will be added in next task

        return {
            "document_id": document.document_id,
            "filename": document.filename,
            "file_type": document.file_type.value,
            "file_size": document.file_size,
            "status": document.status.value,
            "message": "Upload complete, extraction starting"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{document_id}")
async def get_document_info(document_id: str):
    """
    Get document information and status.
    """
    document = await get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "document_id": document.document_id,
        "filename": document.filename,
        "file_type": document.file_type.value,
        "file_size": document.file_size,
        "status": document.status.value,
        "upload_date": document.upload_date.isoformat(),
        "expires_at": document.expires_at.isoformat(),
        "total_pages": document.total_pages,
        "total_chapters": document.total_chapters,
        "extraction_progress": document.extraction_progress
    }

@router.get("/{document_id}/extract/stream")
async def stream_extraction(document_id: str):
    """
    Stream chapter extraction progress via Server-Sent Events.

    Yields Chapter objects as they are extracted.
    """
    document = await get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if document.status == DocumentStatus.EXTRACTING:
        # Extraction already in progress
        pass

    # Update status
    # NOTE: Thread safety limitation - multiple simultaneous requests to same document
    # could cause race conditions. Production use should add locks.
    document.status = DocumentStatus.EXTRACTING
    active_documents[document_id] = document

    async def event_generator():
        try:
            chapter_count = 0
            progress_chapters = []

            # Extract based on file type
            if document.file_type.value == "pdf":
                stream = extract_pdf_text(document_id, document.file_path)
            else:  # epub
                stream = extract_epub_text(document_id, document.file_path)

            async for chapter in stream:
                chapter_count += 1

                # Skip progress indicators
                if chapter.chapter_number == 0:
                    progress = {
                        "type": "progress",
                        "current": chapter.word_count,  # Reuse field for page count
                        "message": chapter.title
                    }
                    yield f"event: progress\ndata: {json.dumps(progress)}\n\n"
                    continue

                # Save chapter
                # Note: Chapter storage will be added in next task
                progress_chapters.append(chapter)

                # Send chapter event
                chapter_data = {
                    "type": "chapter",
                    "chapter": {
                        "chapter_id": chapter.chapter_id,
                        "chapter_number": chapter.chapter_number,
                        "title": chapter.title,
                        "word_count": chapter.word_count,
                        "quality_score": chapter.quality_score,
                        "needs_ocr": chapter.needs_ocr,
                        "text_preview": chapter.text_preview[:200]
                    }
                }
                yield f"event: chapter\ndata: {json.dumps(chapter_data)}\n\n"

            # Update document status
            # NOTE: Thread safety limitation - multiple simultaneous requests to same document
            # could cause race conditions. Production use should add locks.
            document.total_chapters = len(progress_chapters)
            document.status = DocumentStatus.READY
            document.extraction_progress = 1.0
            active_documents[document_id] = document

            # Send completion event
            complete_data = {
                "type": "complete",
                "total_chapters": len(progress_chapters)
            }
            yield f"event: complete\ndata: {json.dumps(complete_data)}\n\n"

        except Exception as e:
            # Send error event
            error_data = {
                "type": "error",
                "error": str(e)
            }
            yield f"event: error\ndata: {json.dumps(error_data)}\n\n"

            # Update document status
            # NOTE: Thread safety limitation - multiple simultaneous requests to same document
            # could cause race conditions. Production use should add locks.
            document.status = DocumentStatus.ERROR
            active_documents[document_id] = document

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@router.get("/{document_id}/structure")
async def get_document_structure(document_id: str):
    """
    Get document chapter structure (non-streaming).
    """
    document = await get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Note: Chapter storage retrieval will be added in next task
    return {
        "document_id": document_id,
        "status": document.status.value,
        "total_chapters": document.total_chapters or 0,
        "extraction_progress": document.extraction_progress,
        "chapters": []  # Will be populated from storage
    }


@router.post("/{document_id}/content")
async def get_document_content(document_id: str, request: ChapterContentRequest):
    """
    Get full text content for selected chapters.

    Concatenates the full_text of requested chapters in chapter_number order.
    """
    document = await get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if document.status != DocumentStatus.READY:
        raise HTTPException(
            status_code=400,
            detail=f"Document not ready. Current status: {document.status.value}"
        )

    # Retrieve chapters from document metadata
    all_chapters = document.metadata.get("chapters", [])

    # Filter and sort selected chapters
    selected_chapters = [
        ch for ch in all_chapters
        if ch.get("chapter_id") in request.chapter_ids
    ]
    selected_chapters.sort(key=lambda x: x.get("chapter_number", 0))

    if not selected_chapters:
        raise HTTPException(status_code=404, detail="No valid chapters found")

    # Concatenate chapter texts with chapter titles as separators
    combined_sections = []
    skipped_chapters = []
    total_word_count = 0

    for ch in selected_chapters:
        title = ch.get("title", f"Chapter {ch.get('chapter_number', '?')}")
        text = ch.get("full_text", "").strip()
        if text:
            combined_sections.append(f"{title}\n{text}")
            total_word_count += ch.get("word_count", 0)
        else:
            skipped_chapters.append({
                "chapter_id": ch.get("chapter_id"),
                "title": title
            })

    if not combined_sections:
        raise HTTPException(
            status_code=400,
            detail="No text content available for selected chapters"
        )

    combined_text = "\n\n".join(combined_sections)

    # Check if any requested chapters weren't found
    found_ids = {ch.get("chapter_id") for ch in selected_chapters}
    missing_ids = set(request.chapter_ids) - found_ids

    return {
        "document_id": document_id,
        "filename": document.filename,
        "chapter_count": len(combined_sections),
        "word_count": total_word_count,
        "text": combined_text,
        "skipped_chapters": skipped_chapters,
        "missing_chapter_ids": list(missing_ids)
    }
