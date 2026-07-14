# document_api.py
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
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
    get_expected_chunk_size,
    get_document,
    get_queue,
    delete_document,
    active_sessions,
    active_documents
)
from text_extractor import extract_pdf_text, extract_epub_text
from job_queue import (
    submit_extraction_job,
    get_job_status,
    retry_job,
    cancel_job,
    ExtractionJob,
    JobStatus
)


class ChapterContentRequest(BaseModel):
    chapter_ids: List[str] = Field(min_items=1, description="List of chapter IDs to retrieve")


router = APIRouter(prefix="/document", tags=["document"])
_CHUNK_READ_SIZE = 1024 * 1024
_MAX_CONCURRENT_CHUNKS_PER_UPLOAD = 5
_chunk_lock = asyncio.Lock()
_active_chunk_counts: dict[str, int] = {}


def _session_id(request: Request) -> str:
    session_id = getattr(request.state, "session_id", None) or request.cookies.get("story2audio_session")
    if not session_id:
        raise HTTPException(status_code=401, detail="Browser session required")
    return session_id


def _owned_upload(request: Request, upload_id: str):
    session = active_sessions.get(upload_id)
    if not session or session.owner_session != _session_id(request):
        raise HTTPException(status_code=404, detail="Upload session not found")
    return session


async def _owned_document(request: Request, document_id: str):
    document = await get_document(document_id, _session_id(request))
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


async def _owned_job(request: Request, job_id: str):
    job = get_job_status(job_id)
    if not job or not await get_document(job.document_id, _session_id(request)):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def _read_upload_chunk(chunk: UploadFile, expected_size: int) -> bytes:
    data = bytearray()
    while True:
        part = await chunk.read(_CHUNK_READ_SIZE)
        if not part:
            break
        data.extend(part)
        if len(data) > expected_size:
            raise ValueError(
                f"Chunk size exceeds expected {expected_size} bytes"
            )
    return bytes(data)


async def _reserve_chunk_slot(upload_id: str) -> None:
    async with _chunk_lock:
        current = _active_chunk_counts.get(upload_id, 0)
        if current >= _MAX_CONCURRENT_CHUNKS_PER_UPLOAD:
            raise HTTPException(
                status_code=429,
                detail="Too many concurrent chunks for this upload"
            )
        _active_chunk_counts[upload_id] = current + 1


async def _release_chunk_slot(upload_id: str) -> None:
    async with _chunk_lock:
        current = _active_chunk_counts.get(upload_id, 0)
        if current <= 1:
            _active_chunk_counts.pop(upload_id, None)
        else:
            _active_chunk_counts[upload_id] = current - 1

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
    request: Request,
    filename: str = Form(...),
    file_size: int = Form(...),
    checksum: str = Form(...)
):
    """
    Initiate a chunked file upload.

    Returns upload_id and chunk size for subsequent chunk uploads.
    """
    try:
        session = await initiate_upload(filename, file_size, checksum, _session_id(request))
        return {
            "upload_id": session.upload_id,
            "chunk_size": session.chunk_size,
            "status": "initiated"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError:
        raise HTTPException(status_code=507, detail="Upload storage error")
    except Exception:
        raise HTTPException(status_code=500, detail="Upload initiation failed")

@router.post("/upload/chunk")
async def upload_chunk(
    request: Request,
    upload_id: str = Form(...),
    chunk_number: int = Form(...),
    chunk: UploadFile = File(...)
):
    """
    Upload a single chunk of the file.

    Returns success status and next chunk number.
    """
    await _reserve_chunk_slot(upload_id)
    try:
        _owned_upload(request, upload_id)
        expected_size = get_expected_chunk_size(upload_id, chunk_number)
        chunk_data = await _read_upload_chunk(chunk, expected_size)
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
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(status_code=507, detail="Upload storage error")
    except Exception:
        raise HTTPException(status_code=500, detail="Chunk upload failed")
    finally:
        await _release_chunk_slot(upload_id)

@router.post("/upload/complete")
async def upload_complete(request: Request, upload_id: str = Form(...)):
    """
    Complete the upload process.

    Assembles chunks into final file and returns document_id.
    Triggers text extraction in background.
    """
    try:
        _owned_upload(request, upload_id)
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
    except OSError:
        raise HTTPException(status_code=507, detail="Upload storage error")
    except Exception:
        raise HTTPException(status_code=500, detail="Upload completion failed")

@router.get("/queue")
async def get_document_queue(request: Request):
    """
    Get all documents in the upload queue.
    """
    return {"queue": await get_queue(_session_id(request))}

@router.get("/{document_id}")
async def get_document_info(request: Request, document_id: str):
    """
    Get document information and status.
    """
    document = await _owned_document(request, document_id)

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
        "extraction_progress": document.extraction_progress,
        "metadata": document.metadata  # Include metadata (contains chapters)
    }

@router.get("/{document_id}/extract/stream")
async def stream_extraction(request: Request, document_id: str):
    """
    Stream chapter extraction progress via Server-Sent Events.

    Yields Chapter objects as they are extracted.
    """
    document = await _owned_document(request, document_id)

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

            # Store chapters in metadata for content endpoint retrieval
            document.metadata["chapters"] = [
                {
                    "chapter_id": ch.chapter_id,
                    "chapter_number": ch.chapter_number,
                    "title": ch.title,
                    "word_count": ch.word_count,
                    "full_text": getattr(ch, 'full_text', ch.text_preview),
                    "quality_score": ch.quality_score,
                    "needs_ocr": ch.needs_ocr
                }
                for ch in progress_chapters
            ]

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
async def get_document_structure(request: Request, document_id: str):
    """
    Get document chapter structure (non-streaming).
    """
    document = await _owned_document(request, document_id)

    # Get chapters from document metadata
    chapters = document.metadata.get("chapters", [])

    return {
        "document_id": document_id,
        "status": document.status.value,
        "total_chapters": document.total_chapters or len(chapters),
        "extraction_progress": document.extraction_progress,
        "chapters": chapters
    }


@router.delete("/{document_id}")
async def delete_document_from_queue(request: Request, document_id: str):
    """
    Delete a document from the queue.
    """
    success = await delete_document(document_id, _session_id(request))
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "Document deleted"}


@router.post("/{document_id}/content")
async def get_document_content(document_id: str, body: ChapterContentRequest, request: Request):
    """
    Get full text content for selected chapters.

    Concatenates the full_text of requested chapters in chapter_number order.
    """
    document = await _owned_document(request, document_id)

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
        if ch.get("chapter_id") in body.chapter_ids
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
    missing_ids = set(body.chapter_ids) - found_ids

    return {
        "document_id": document_id,
        "filename": document.filename,
        "chapter_count": len(combined_sections),
        "word_count": total_word_count,
        "text": combined_text,
        "skipped_chapters": skipped_chapters,
        "missing_chapter_ids": list(missing_ids)
    }


# Job Queue endpoints

@router.post("/job/{document_id}/extract")
async def submit_job_extraction(request: Request, document_id: str):
    """
    Submit a document extraction job to the worker queue.

    Returns immediately with job_id for status polling.
    """
    document = await _owned_document(request, document_id)

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


@router.get("/job/{job_id}/status")
async def get_job_status_endpoint(request: Request, job_id: str):
    """
    Get the status of an extraction job.
    """
    job = await _owned_job(request, job_id)

    return job.model_dump()


@router.get("/job/{job_id}/result")
async def get_job_result(request: Request, job_id: str):
    """
    Get the extraction result from a completed job.
    """
    job = await _owned_job(request, job_id)

    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job not completed. Current status: {job.status.value}"
        )

    return job.result


@router.post("/job/{job_id}/retry")
async def retry_job_endpoint(request: Request, job_id: str):
    """
    Retry a failed extraction job.
    """
    await _owned_job(request, job_id)
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


@router.delete("/job/{job_id}")
async def cancel_job_endpoint(request: Request, job_id: str):
    """
    Cancel a pending or running job.
    """
    await _owned_job(request, job_id)
    success = cancel_job(job_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Job not found, already completed, or cannot be cancelled"
        )

    return {"message": "Job cancelled"}
