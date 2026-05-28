# text_extractor.py
import os
import re
import asyncio
from typing import AsyncGenerator, List, Callable, Optional, Dict, Any
from pathlib import Path
from models import Chapter, FileType, ExtractionMethod
import PyPDF2
import pdfplumber

# Progress callback type: (progress: float, message: str) -> None
ProgressCallback = Callable[[float, str], None]

# Configuration
EXTRACTION_PAGE_BATCH = int(os.getenv("EXTRACTION_PAGE_BATCH", "20"))

def _assess_text_quality(text: str) -> float:
    """
    Assess text extraction quality based on various indicators.

    Returns:
        Quality score between 0.0 and 1.0
    """
    if not text or len(text.strip()) == 0:
        return 0.0

    score = 1.0

    # Check for excessive special characters (OCR indicator)
    special_char_ratio = len(re.findall(r'[^a-zA-Z0-9\s\.,!?;:]', text)) / max(len(text), 1)
    if special_char_ratio > 0.3:
        score -= 0.4

    # Check for common OCR errors
    ocr_indicators = ['|', '>', '<', '[', ']', '{', '}', '\\', '/']
    ocr_count = sum(text.count(char) for char in ocr_indicators)
    if ocr_count > len(text) * 0.1:
        score -= 0.3

    # Check for reasonable word structure
    words = text.split()
    if words:
        avg_word_length = sum(len(w) for w in words) / len(words)
        if avg_word_length < 2 or avg_word_length > 15:
            score -= 0.2

    # Additional check: even small amounts of special chars should reduce score
    if 0 < special_char_ratio <= 0.1:
        score -= 0.1

    return max(0.0, min(1.0, score))


async def _extract_batch(
    chapters: List[Chapter],
    extract_fn: Callable,
    parallel: bool = True
) -> List[Chapter]:
    """
    Extract a batch of chapters.

    Args:
        chapters: List of Chapter objects to extract
        extract_fn: Async function to extract a chapter
        parallel: If True, extract all in parallel; otherwise sequential

    Returns:
        List of extracted Chapter objects
    """
    if parallel:
        tasks = [extract_fn(ch) for ch in chapters]
        return await asyncio.gather(*tasks)
    else:
        results = []
        for ch in chapters:
            result = await extract_fn(ch)
            results.append(result)
        return results


async def extract_chapters_parallel(
    document: 'Document',
    chapters: List[Chapter],
    extract_fn: Callable,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> List[Chapter]:
    """
    Extract chapters with adaptive parallelization.

    Small chapters (<5000 words) are extracted in parallel.
    Large chapters (>=5000 words) are extracted in batches of 4.

    Args:
        document: Document being extracted
        chapters: List of Chapter objects
        extract_fn: Async function that takes a Chapter and returns extracted Chapter
        progress_callback: Optional callback for progress updates

    Returns:
        List of extracted chapters, sorted by chapter_number
    """
    # Classify chapters by size
    SMALL_CHAPTER_THRESHOLD = 5000  # words
    LARGE_BATCH_SIZE = 4

    small_chapters = [c for c in chapters if c.word_count < SMALL_CHAPTER_THRESHOLD]
    large_chapters = [c for c in chapters if c.word_count >= SMALL_CHAPTER_THRESHOLD]

    results = []
    total_chapters = len(chapters)
    completed = 0

    # Small chapters: extract all in parallel
    if small_chapters:
        small_results = await _extract_batch(small_chapters, extract_fn, parallel=True)
        results.extend(small_results)
        completed += len(small_results)

        if progress_callback:
            progress = completed / total_chapters
            progress_callback(progress, f"Extracted {completed}/{total_chapters} chapters")

    # Large chapters: extract in batches
    if large_chapters:
        for i in range(0, len(large_chapters), LARGE_BATCH_SIZE):
            batch = large_chapters[i:i + LARGE_BATCH_SIZE]
            batch_results = await _extract_batch(batch, extract_fn, parallel=True)
            results.extend(batch_results)
            completed += len(batch_results)

            if progress_callback:
                progress = completed / total_chapters
                progress_callback(progress, f"Extracted {completed}/{total_chapters} chapters")

    # Sort results by chapter_number
    results.sort(key=lambda c: c.chapter_number)
    return results


def _detect_chapters_in_text(
    text: str,
    document_id: str
) -> List[Chapter]:
    """
    Detect chapter structure in extracted text using heuristics.

    Args:
        text: Full extracted text
        document_id: Document ID

    Returns:
        List of Chapter objects
    """
    chapters = []

    # Chapter detection patterns (try multiple)
    chapter_patterns = [
        r'^(Chapter\s+\d+[:\.\s]*(.+?))$',  # "Chapter 1: Title"
        r'^(CHAPTER\s+\d+[:\.\s]*(.+?))$',  # "CHAPTER 1: TITLE"
        r'^(\d+\.\s+(.+?))$',               # "1. Title"
        r'^(Part\s+\d+[:\.\s]*(.+?))$',     # "Part 1: Title"
    ]

    lines = text.split('\n')
    current_chapter_start = 0
    chapter_num = 0
    last_chapter_title = None

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # Check if line matches chapter pattern
        chapter_title = None
        for pattern in chapter_patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                chapter_title = match.group(1)
                break

        if chapter_title:
            # Save previous chapter if we have one
            if chapter_num > 0 and last_chapter_title:
                chapter_text = '\n'.join(lines[current_chapter_start:i])
                preview = chapter_text[:500].strip()

                chapters.append(Chapter(
                    document_id=document_id,
                    chapter_number=chapter_num,
                    title=last_chapter_title,
                    text_preview=preview,
                    full_text=chapter_text,
                    word_count=len(chapter_text.split()),
                    extraction_method=ExtractionMethod.BASIC
                ))

            last_chapter_title = chapter_title
            current_chapter_start = i
            chapter_num += 1

    # Add last chapter if we found chapters
    if chapter_num > 0 and last_chapter_title:
        chapter_text = '\n'.join(lines[current_chapter_start:])
        preview = chapter_text[:500].strip()

        chapters.append(Chapter(
            document_id=document_id,
            chapter_number=chapter_num,
            title=last_chapter_title,
            text_preview=preview,
            full_text=chapter_text,
            word_count=len(chapter_text.split()),
            extraction_method=ExtractionMethod.BASIC
        ))

    # If no chapters detected, create single chapter
    if not chapters:
        chapters.append(Chapter(
            document_id=document_id,
            chapter_number=1,
            title="Full Text",
            text_preview=text[:500].strip(),
            full_text=text,
            word_count=len(text.split()),
            extraction_method=ExtractionMethod.BASIC
        ))

    return chapters

async def extract_pdf_text(document_id: str, file_path: str) -> AsyncGenerator[Chapter, None]:
    """
    Extract text from PDF file, yielding chapters as they're discovered.

    Args:
        document_id: Document ID
        file_path: Path to PDF file

    Yields:
        Chapter objects as they're extracted
    """
    try:
        # Try pdfplumber first (better quality)

        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)
            all_text = []

            for page_num, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text()
                    if text:
                        all_text.append(text)
                except Exception:
                    all_text.append("")

                # Yield progress update every batch of pages
                if (page_num + 1) % EXTRACTION_PAGE_BATCH == 0:
                    yield Chapter(
                        document_id=document_id,
                        chapter_number=0,  # Progress indicator
                        title=f"Processing... ({page_num + 1}/{total_pages})",
                        text_preview="",
                        word_count=page_num + 1
                    )

            full_text = '\n\n'.join(all_text)

    except Exception as e:
        # Fallback to PyPDF2
        try:

            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                total_pages = len(pdf_reader.pages)
                all_text = []

                for page_num in range(total_pages):
                    try:
                        page = pdf_reader.pages[page_num]
                        text = page.extract_text()
                        if text:
                            all_text.append(text)
                    except Exception:
                        all_text.append("")

                    if (page_num + 1) % EXTRACTION_PAGE_BATCH == 0:
                        yield Chapter(
                            document_id=document_id,
                            chapter_number=0,
                            title=f"Processing... ({page_num + 1}/{total_pages})",
                            text_preview="",
                            word_count=page_num + 1
                        )

                full_text = '\n\n'.join(all_text)

        except Exception as e:
            # If both fail, raise error
            raise RuntimeError(f"PDF extraction failed: {e}")

    # Detect and yield chapters
    chapters = _detect_chapters_in_text(full_text, document_id)

    for chapter in chapters:
        # Assess quality
        chapter.quality_score = _assess_text_quality(chapter.full_text or "")
        chapter.needs_ocr = chapter.quality_score < 0.5

        # Estimate audio duration (150 words per minute average)
        if chapter.word_count > 0:
            chapter.estimated_audio_duration = (chapter.word_count / 150) * 60

        yield chapter

async def extract_epub_text(document_id: str, file_path: str) -> AsyncGenerator[Chapter, None]:
    """
    Extract text from EPUB file, yielding chapters as they're discovered.

    Args:
        document_id: Document ID
        file_path: Path to EPUB file

    Yields:
        Chapter objects as they're extracted
    """
    try:
        from ebooklib import epub, ITEM_DOCUMENT

        epub_book = epub.read_epub(file_path)
        all_text = []
        chapter_num = 0

        for item in epub_book.get_items():
            if item.get_type() == ITEM_DOCUMENT:
                try:
                    content = item.get_content()
                    # Extract text from HTML (basic)
                    text = re.sub(r'<[^>]+>', '\n', content.decode('utf-8'))
                    text = re.sub(r'\n+', '\n', text).strip()

                    if text and len(text) > 100:  # Skip very short sections
                        chapter = Chapter(
                            document_id=document_id,
                            chapter_number=chapter_num + 1,
                            title=item.get_name(),
                            text_preview=text[:500].strip(),
                            full_text=text,
                            word_count=len(text.split()),
                            extraction_method=ExtractionMethod.BASIC
                        )

                        # Assess quality
                        chapter.quality_score = _assess_text_quality(text)
                        chapter.needs_ocr = chapter.quality_score < 0.5

                        # Estimate duration
                        chapter.estimated_audio_duration = (chapter.word_count / 150) * 60

                        yield chapter
                        chapter_num += 1

                except Exception:
                    continue

    except Exception as e:
        raise RuntimeError(f"EPUB extraction failed: {e}")


def extract_pdf_text_blocking(
    document_id: str,
    file_path: str,
    progress_callback: Optional[ProgressCallback] = None
) -> List[Dict]:
    """
    Blocking version of PDF extraction for use in worker threads.

    Args:
        document_id: Document ID
        file_path: Path to PDF file
        progress_callback: Optional callback for progress updates

    Returns:
        List of chapter dictionaries
    """
    try:
        # Try pdfplumber first
        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)
            all_text = []

            for page_num, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text()
                    if text:
                        all_text.append(text)
                except Exception:
                    all_text.append("")

                # Report progress
                if progress_callback and total_pages > 0:
                    progress = (page_num + 1) / total_pages
                    message = f"Processing page {page_num + 1}/{total_pages}"
                    progress_callback(progress, message)

            full_text = '\n\n'.join(all_text)

    except Exception as e:
        # Fallback to PyPDF2
        try:
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                total_pages = len(pdf_reader.pages)
                all_text = []

                for page_num in range(total_pages):
                    try:
                        page = pdf_reader.pages[page_num]
                        text = page.extract_text()
                        if text:
                            all_text.append(text)
                    except Exception:
                        all_text.append("")

                    if progress_callback and total_pages > 0:
                        progress = (page_num + 1) / total_pages
                        message = f"Processing page {page_num + 1}/{total_pages}"
                        progress_callback(progress, message)

                full_text = '\n\n'.join(all_text)

        except Exception as e2:
            raise RuntimeError(f"PDF extraction failed: {e2}")

    # Detect chapters
    chapters = _detect_chapters_in_text(full_text, document_id)

    # Build result list
    result = []
    for chapter in chapters:
        # Assess quality
        chapter.quality_score = _assess_text_quality(chapter.full_text or "")
        chapter.needs_ocr = chapter.quality_score < 0.5

        # Estimate audio duration
        if chapter.word_count > 0:
            chapter.estimated_audio_duration = (chapter.word_count / 150) * 60

        result.append({
            "chapter_id": chapter.chapter_id,
            "document_id": chapter.document_id,
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "start_page": chapter.start_page,
            "end_page": chapter.end_page,
            "text_preview": chapter.text_preview[:200] if chapter.text_preview else "",
            "full_text": chapter.full_text or "",
            "word_count": chapter.word_count,
            "estimated_audio_duration": chapter.estimated_audio_duration,
            "quality_score": chapter.quality_score,
            "needs_ocr": chapter.needs_ocr,
            "extraction_method": chapter.extraction_method.value,
            "language": chapter.language
        })

    return result


def extract_epub_text_blocking(
    document_id: str,
    file_path: str,
    progress_callback: Optional[ProgressCallback] = None
) -> List[Dict]:
    """
    Blocking version of EPUB extraction for use in worker threads.

    Args:
        document_id: Document ID
        file_path: Path to EPUB file
        progress_callback: Optional callback for progress updates

    Returns:
        List of chapter dictionaries
    """
    try:
        from ebooklib import epub, ITEM_DOCUMENT

        chapters = []
        book = epub.read_epub(file_path)

        # Get all items
        all_items = list(book.get_items())
        total_items = len(all_items)

        for idx, item in enumerate(all_items):
            if item.get_type() == ITEM_DOCUMENT:
                # Extract text from HTML content
                content = item.get_content()
                # Simple text extraction (strip HTML tags)
                text = re.sub(r'<[^>]+>', '\n', content.decode('utf-8', errors='ignore'))
                text = ' '.join(text.split())

                if text.strip():
                    ch_num = len(chapters) + 1
                    chapter = Chapter(
                        chapter_id=f"{document_id}_ch_{ch_num}",
                        document_id=document_id,
                        chapter_number=ch_num,
                        title=f"Section {ch_num}",
                        text_preview=text[:200],
                        full_text=text,
                        word_count=len(text.split())
                    )

                    chapter.quality_score = _assess_text_quality(text)
                    chapter.needs_ocr = chapter.quality_score < 0.5

                    if chapter.word_count > 0:
                        chapter.estimated_audio_duration = (chapter.word_count / 150) * 60

                    chapters.append(chapter)

            # Report progress
            if progress_callback and total_items > 0:
                progress = (idx + 1) / total_items
                message = f"Processing section {idx + 1}/{total_items}"
                progress_callback(progress, message)

    except Exception as e:
        raise RuntimeError(f"EPUB extraction failed: {e}")

    # Build result list
    result = []
    for chapter in chapters:
        result.append({
            "chapter_id": chapter.chapter_id,
            "document_id": chapter.document_id,
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "start_page": chapter.start_page,
            "end_page": chapter.end_page,
            "text_preview": chapter.text_preview[:200] if chapter.text_preview else "",
            "full_text": chapter.full_text or "",
            "word_count": chapter.word_count,
            "estimated_audio_duration": chapter.estimated_audio_duration,
            "quality_score": chapter.quality_score,
            "needs_ocr": chapter.needs_ocr,
            "extraction_method": chapter.extraction_method.value,
            "language": chapter.language
        })

    return result


async def extract_chapter_with_ocr(
    document: 'Document',
    chapter_index: int,
    progress_callback: Optional[Callable[[float], None]] = None
) -> Chapter:
    """
    Extract a single chapter using OCR.

    Args:
        document: Document to extract from
        chapter_index: Index of chapter to extract
        progress_callback: Optional progress callback

    Returns:
        Chapter with OCR-extracted text
    """
    try:
        from pdf2image import convert_from_path
        from pytesseract import image_to_string
        from PIL import Image
    except ImportError as e:
        raise ImportError(
            f"OCR dependencies not available: {e}. "
            "Install with: pip install pytesseract pdf2image Pillow"
        )

    # Get chapters from metadata
    chapters = document.metadata.get("chapters", [])
    if chapter_index >= len(chapters):
        raise ValueError(f"Chapter {chapter_index} not found")

    chapter_data = chapters[chapter_index]

    # Convert PDF pages to images
    start_page = chapter_data.get("start_page", 1)
    end_page = chapter_data.get("end_page", start_page)

    images = convert_from_path(
        document.file_path,
        first_page=start_page,
        last_page=end_page,
    )

    # Run OCR on each page
    full_text = []
    for i, img in enumerate(images):
        if progress_callback:
            progress_callback((i + 1) / len(images))

        # OCR with Vietnamese + English
        text = image_to_string(
            img,
            lang='vie+eng',
            config='--psm 6'
        )
        full_text.append(text)

    combined_text = "\n".join(full_text)

    # Update chapter with OCR result
    chapter = Chapter(
        chapter_id=chapter_data.get("chapter_id", f"ch_{chapter_index}"),
        document_id=document.document_id,
        chapter_number=chapter_index,
        title=chapter_data.get("title", f"Chapter {chapter_index + 1}"),
        text_preview=combined_text[:500] if combined_text else "",
        full_text=combined_text,
        word_count=len(combined_text.split()) if combined_text else 0,
        extraction_method=ExtractionMethod.OCR,
        quality_score=1.0,
        needs_ocr=False,
        ocr_processed=True
    )

    return chapter
