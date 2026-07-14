"""
Vietnamese Text Processor for Smart Chunking

Provides intelligent text segmentation for TTS that respects:
- Vietnamese sentence boundaries
- Proper noun phrases (names, places)
- Natural phrase boundaries
- Pause insertion for natural speech rhythm
"""

import re
import logging
from typing import List, Tuple, Optional

logger = logging.getLogger("ebook2audio")


# Vietnamese-specific sentence patterns
VIETNAMESE_SENTENCE_DELIMITERS = [
    r'[.!?…]+\s+',               # Standard endings with space
    r'[.!?…]+\s*"\s*',           # Quote endings
    r'[.!?…]+\s*\)\s*',          # Parenthesis endings
    r'[.!?…]+\s*\]\s*',          # Bracket endings
    r'[.!?…]+\s*}\s*',           # Brace endings
    r'\n\n+',                    # Paragraph breaks
    r'\n\s*\n',                  # Paragraph with spaces
]

# Patterns that indicate a proper noun (Vietnamese names)
# Vietnamese family names commonly appear at the start of proper nouns
VIETNAMESE_FAMILY_NAMES = [
    'Nguyễn', 'Trần', 'Lê', 'Phạm', 'Hoàng', 'Huỳnh', 'Phan', 'Vũ', 'Võ', 'Đặng',
    'Bùi', 'Đỗ', 'Hồ', 'Ngô', 'Dương', 'Lý', 'Đào', 'Đoàn', 'Triệu', 'Lâm',
    'Trịnh', 'Đinh', 'Lại', 'Cao', 'Miêu', 'Tiêu', 'Trương', 'Tô', 'Điền',
    'Quách', 'Kim', 'Châu', 'Hà', 'Mai', 'Thái', 'Thôi', 'Quan', 'Hạng',
]

# Build regex pattern for family names
FAMILY_NAME_PATTERN = r'\b(?:' + '|'.join(VIETNAMESE_FAMILY_NAMES) + r')\b'

# Vietnamese character classes - comprehensive set of diacritics
# All Vietnamese vowels with all tone marks
VIETNAMESE_UPPER = r"A-ZÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ"
VIETNAMESE_LOWER = r"a-zàáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ"

# Proper noun pattern: Capitalized word sequence (2-4 words)
# Includes Vietnamese diacritics - more comprehensive pattern
PROPER_NOUN_PATTERN = r'''
    (?:
        [''' + VIETNAMESE_UPPER + r'''][''' + VIETNAMESE_LOWER + r''']*  # First capitalized word
        (?:
            \s+[''' + VIETNAMESE_UPPER + r'''][''' + VIETNAMESE_LOWER + r''']*  # More capitalized words
        ){1,3}
    )
'''

# Patterns that indicate safe split points within sentences
SAFE_SPLIT_PATTERNS = [
    r',\s*',                    # Comma
    r';\s*',                    # Semicolon
    r':\s*',                    # Colon
    r'\s+-\s+',                 # Em dash
    r'\s—\s+',                  # Em dash (Unicode)
    r'\s–\s+',                  # En dash
    r' mà\s+',                  # "mà" (but) conjunction
    r' và\s+',                  # "và" (and) conjunction
    r' hoặc\s+',                # "hoặc" (or) conjunction
    r' thì\s+',                 # "thì" (then) conjunction
    r' là\s+',                  # "là" (is) - sometimes safe
    r' của\s+',                 # "của" (of) - sometimes safe
]

# Patterns that should NEVER be split (protected phrases)
PROTECTED_PATTERNS = [
    FAMILY_NAME_PATTERN + r'(?:\s+[A-ZĐ][a-záàảãạăắằẳẵặâấầẩẫậ]+){1,2}',  # Full names
    r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',  # Dates
    r'\b\d+[.,]\d+\b',         # Decimal numbers
    r'\b[A-ZĐ]{2,}\b',          # Acronyms
]


class VietnameseTextProcessor:
    """Processes Vietnamese text for intelligent TTS chunking."""

    def __init__(
        self,
        max_chunk_size: int = 1200,
        max_sentences_per_chunk: int = 8,
        pause_duration_ms: int = 300,
    ):
        """
        Initialize the text processor.

        Args:
            max_chunk_size: Maximum effective character count per chunk
            max_sentences_per_chunk: Maximum sentences per chunk
            pause_duration_ms: Default silence duration between chunks
        """
        self.max_chunk_size = max_chunk_size
        self.max_sentences_per_chunk = max_sentences_per_chunk
        self.pause_duration_ms = pause_duration_ms

        # Compile regex patterns
        self.sentence_split_re = self._compile_sentence_splitter()
        self.proper_noun_re = re.compile(PROPER_NOUN_PATTERN, re.VERBOSE | re.UNICODE)
        self.protected_re = self._compile_protected_patterns()
        self.safe_split_re = re.compile(
            '|'.join(SAFE_SPLIT_PATTERNS),
            re.UNICODE
        )

    def _compile_sentence_splitter(self) -> re.Pattern:
        """Compile regex for detecting sentence boundaries."""
        # Match sentence endings followed by space or quote start
        pattern = '|'.join(VIETNAMESE_SENTENCE_DELIMITERS)
        return re.compile(pattern, re.MULTILINE | re.UNICODE)

    def _compile_protected_patterns(self) -> re.Pattern:
        """Compile regex for detecting protected phrases."""
        pattern = '|'.join(f'({p})' for p in PROTECTED_PATTERNS)
        return re.compile(pattern, re.UNICODE)

    def detect_proper_nouns(self, text: str) -> List[Tuple[int, int, str]]:
        """
        Detect proper noun phrases in text.

        Args:
            text: Input text

        Returns:
            List of tuples (start_pos, end_pos, matched_text)
        """
        proper_nouns = []

        # Find family name + given name combinations
        for match in self.proper_noun_re.finditer(text):
            proper_nouns.append((match.start(), match.end(), match.group()))

        # Find dates
        date_pattern = re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b')
        for match in date_pattern.finditer(text):
            proper_nouns.append((match.start(), match.end(), match.group()))

        # Find acronyms
        acronym_pattern = re.compile(r'\b[A-ZĐ]{2,}\b')
        for match in acronym_pattern.finditer(text):
            proper_nouns.append((match.start(), match.end(), match.group()))

        # Sort by position
        proper_nouns.sort(key=lambda x: x[0])
        return proper_nouns

    def is_inside_proper_noun(self, text: str, position: int) -> bool:
        """
        Check if a position is inside a proper noun phrase.

        Args:
            text: Input text
            position: Character position to check

        Returns:
            True if position is inside a protected phrase
        """
        for start, end, _ in self.detect_proper_nouns(text):
            if start <= position < end:
                return True
        return False

    def find_safe_split_points(self, text: str) -> List[int]:
        """
        Find all safe positions to split text within a sentence.

        Args:
            text: Input text

        Returns:
            List of character positions that are safe split points
        """
        safe_points = []

        for match in self.safe_split_re.finditer(text):
            split_pos = match.end()  # Split after the delimiter
            if not self.is_inside_proper_noun(text, split_pos):
                safe_points.append(split_pos)

        return safe_points

    def split_into_sentences(self, text: str) -> List[str]:
        """
        Split text into sentences using Vietnamese-aware patterns.

        Args:
            text: Input text

        Returns:
            List of sentences (with surrounding whitespace trimmed)
        """
        # Split by sentence delimiters
        sentences = []
        last_end = 0

        for match in self.sentence_split_re.finditer(text):
            sentence_end = match.end()
            sentence = text[last_end:sentence_end].strip()

            if sentence:
                sentences.append(sentence)

            last_end = sentence_end

        # Add remaining text
        if last_end < len(text):
            remaining = text[last_end:].strip()
            if remaining:
                sentences.append(remaining)

        return sentences if sentences else [text] if text.strip() else []

    def effective_length(self, text: str) -> int:
        """
        Calculate effective character length (accounting for Vietnamese characters).

        Vietnamese characters with diacritics count as 1, CJK counts as 2.

        Args:
            text: Input text

        Returns:
            Effective character count
        """
        if not text:
            return 0

        count = 0
        for char in text:
            if char.isspace():
                continue
            # Vietnamese and other Latin-based characters
            if ord(char) < 0x4E00:  # Below CJK range
                count += 1
            else:  # CJK characters
                count += 2
        return count

    def find_phrase_boundary(self, sentence: str, max_pos: int) -> int:
        """
        Find a safe position to split a sentence without breaking phrases.

        Args:
            sentence: Sentence to split
            max_pos: Maximum character position to consider

        Returns:
            Safe split position, or 0 if no safe split found
        """
        if max_pos >= len(sentence):
            return len(sentence)

        # Look for safe split points before max_pos
        safe_points = self.find_safe_split_points(sentence)

        # Find the closest safe point before max_pos
        for pos in reversed(safe_points):
            if pos <= max_pos:
                return pos

        # No safe split found, check if we're in a proper noun
        if self.is_inside_proper_noun(sentence, max_pos):
            # Find the start of the proper noun
            for start, end, _ in self.detect_proper_nouns(sentence):
                if start <= max_pos < end:
                    # Split before the proper noun
                    return max(0, start - 1)

        # Fall back to splitting at max_pos
        return max_pos

    def chunk_text(self, text: str) -> List[str]:
        """
        Split text into intelligent chunks for TTS.

        Args:
            text: Input text to chunk

        Returns:
            List of text chunks with pause metadata
        """
        if not text or not text.strip():
            return []

        # Normalize whitespace
        text = re.sub(r'\r\n', '\n', text)
        text = re.sub(r'\r', '\n', text)
        text = text.strip()

        # Split into sentences first
        sentences = self.split_into_sentences(text)

        if not sentences:
            return [text] if text else []

        chunks = []
        current_chunk = ""
        current_sentences = 0

        for sentence in sentences:
            # Check if sentence alone exceeds max size
            if self.effective_length(sentence) > self.max_chunk_size:
                # Flush current chunk first
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                    current_sentences = 0

                # Split long sentence at phrase boundaries
                sub_chunks = self._split_long_sentence(sentence)
                chunks.extend(sub_chunks)
                continue

            # Check if adding sentence would exceed limits
            potential_chunk = current_chunk + " " + sentence if current_chunk else sentence
            potential_length = self.effective_length(potential_chunk)

            if (potential_length <= self.max_chunk_size and
                current_sentences + 1 <= self.max_sentences_per_chunk):
                # Add to current chunk
                current_chunk = potential_chunk
                current_sentences += 1
            else:
                # Start new chunk
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
                current_sentences = 1

        # Add final chunk
        if current_chunk:
            chunks.append(current_chunk.strip())

        return [c for c in chunks if c]

    def _split_long_sentence(self, sentence: str) -> List[str]:
        """
        Split a sentence that exceeds max_chunk_size.

        Args:
            sentence: Long sentence to split

        Returns:
            List of sentence fragments
        """
        chunks = []
        remaining = sentence

        while self.effective_length(remaining) > self.max_chunk_size:
            # Find safe split point
            split_pos = self.find_phrase_boundary(
                remaining,
                int(self.max_chunk_size * 0.9)  # Leave some margin
            )

            if split_pos <= 0:
                # Emergency split at character limit
                split_pos = int(self.max_chunk_size * 0.8)

            chunk = remaining[:split_pos].strip()
            if chunk:
                chunks.append(chunk)

            remaining = remaining[split_pos:].strip()

        if remaining:
            chunks.append(remaining)

        return chunks


# Convenience functions for backward compatibility

def create_vietnamese_chunks(
    text: str,
    max_chunk_size: int = 1200,
    max_sentences_per_chunk: int = 8,
    pause_duration_ms: int = 300,
) -> List[str]:
    """
    Create intelligent chunks from Vietnamese text.

    Args:
        text: Input text
        max_chunk_size: Maximum effective character count per chunk
        max_sentences_per_chunk: Maximum sentences per chunk
        pause_duration_ms: Default silence duration (for metadata)

    Returns:
        List of text chunks
    """
    processor = VietnameseTextProcessor(
        max_chunk_size=max_chunk_size,
        max_sentences_per_chunk=max_sentences_per_chunk,
        pause_duration_ms=pause_duration_ms,
    )
    return processor.chunk_text(text)


def detect_proper_nouns_in_text(text: str) -> List[str]:
    """
    Extract proper nouns from Vietnamese text.

    Args:
        text: Input text

    Returns:
        List of proper noun phrases found
    """
    processor = VietnameseTextProcessor()
    nouns = processor.detect_proper_nouns(text)
    return [noun[2] for noun in nouns]
