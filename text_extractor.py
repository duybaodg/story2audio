# text_extractor.py
import os
import re
import asyncio
from typing import AsyncGenerator, List
from pathlib import Path
from models import Chapter, FileType, ExtractionMethod
import PyPDF2
import pdfplumber

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
        from ebooklib import epub

        epub_book = epub.read_epub(file_path)
        all_text = []
        chapter_num = 0

        for item in epub_book.get_items():
            if item.get_type() == 9:  # ebooklib.ITEM_DOCUMENT = 9
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
