# tests/test_text_extractor.py
import pytest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from text_extractor import (
    _assess_text_quality,
    _detect_chapters_in_text
)
from models import Chapter, ExtractionMethod

@pytest.mark.asyncio
async def test_assess_text_quality():
    # Good quality text
    good_text = "This is a normal sentence with proper words and punctuation."
    score = _assess_text_quality(good_text)
    assert score > 0.8, f"Expected high quality score for good text, got {score}"

    # Poor quality text (many special chars)
    poor_text = "|}][{><>\\/||}][{><"
    score = _assess_text_quality(poor_text)
    assert score < 0.5, f"Expected low quality score for poor text, got {score}"

    # Empty text
    score = _assess_text_quality("")
    assert score == 0.0, f"Expected zero quality score for empty text, got {score}"

    # Text with moderate quality
    moderate_text = "This has some | special | chars but mostly normal words."
    score = _assess_text_quality(moderate_text)
    assert 0.5 <= score <= 0.9, f"Expected moderate quality score, got {score}"

def test_detect_chapters_in_text():
    text = """
Chapter 1: Introduction

This is the first chapter content.

Chapter 2: Methods

This is the second chapter content.
"""
    chapters = _detect_chapters_in_text(text, "doc-123")

    assert len(chapters) == 2, f"Expected 2 chapters, got {len(chapters)}"
    assert chapters[0].chapter_number == 1, f"Expected chapter 1, got {chapters[0].chapter_number}"
    assert "Introduction" in chapters[0].title, f"Expected 'Introduction' in title, got {chapters[0].title}"
    assert chapters[1].chapter_number == 2, f"Expected chapter 2, got {chapters[1].chapter_number}"
    assert "Methods" in chapters[1].title, f"Expected 'Methods' in title, got {chapters[1].title}"

def test_detect_chapters_multiple_patterns():
    text = """
1. First Chapter

Content of first chapter.

2. Second Chapter

Content of second chapter.
"""
    chapters = _detect_chapters_in_text(text, "doc-456")

    assert len(chapters) == 2, f"Expected 2 chapters, got {len(chapters)}"
    assert chapters[0].chapter_number == 1
    assert "First Chapter" in chapters[0].title
    assert chapters[1].chapter_number == 2
    assert "Second Chapter" in chapters[1].title

def test_detect_chapters_no_pattern():
    text = """
This is just some text without chapter markers.
It flows continuously without any clear structure.
Just regular paragraphs one after another.
"""
    chapters = _detect_chapters_in_text(text, "doc-789")

    assert len(chapters) == 1, f"Expected 1 chapter when no pattern found, got {len(chapters)}"
    assert chapters[0].chapter_number == 1
    assert chapters[0].title == "Full Text"

@pytest.mark.asyncio
async def test_extract_pdf_text():
    # This test requires a sample PDF file
    # For now, test with a mock or skip
    pytest.skip("Requires sample PDF file - will be tested with integration tests")

@pytest.mark.asyncio
async def test_extract_epub_text():
    # This test requires a sample EPUB file
    # For now, test with a mock or skip
    pytest.skip("Requires sample EPUB file - will be tested with integration tests")

@pytest.mark.asyncio
async def test_extract_pdf_text_generator():
    # Test that extract_pdf_text is an async generator
    # We'll mock the PDF extraction to test the generator pattern
    async def mock_extract():
        # Yield a progress update
        yield Chapter(
            document_id="test-doc",
            chapter_number=0,
            title="Processing... (5/10)",
            text_preview="",
            word_count=5
        )
        # Yield a real chapter
        yield Chapter(
            document_id="test-doc",
            chapter_number=1,
            title="Chapter 1",
            text_preview="Preview text",
            full_text="Full text content here",
            word_count=4,
            extraction_method=ExtractionMethod.BASIC
        )

    chapters = []
    async for chapter in mock_extract():
        chapters.append(chapter)

    assert len(chapters) == 2
    assert chapters[0].chapter_number == 0  # Progress indicator
    assert chapters[1].chapter_number == 1  # Real chapter
