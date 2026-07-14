# Sample Document Files

Place sample PDF and EPUB files in this directory for integration testing.

## Recommended Test Files

### Basic Testing
- **Small PDF (1-5 pages)**: For basic upload and extraction testing
- **Simple EPUB**: For EPUB structure validation

### Advanced Testing
- **Large PDF (50+ pages)**: For performance testing and chunk handling
- **EPUB with multiple chapters**: For chapter structure detection
- **PDF with complex formatting**: For text extraction quality assessment
- **PDF with images/diagrams**: For OCR recommendation testing

### Edge Cases
- **Scanned PDF**: For OCR testing (future phase)
- **PDF with mixed content**: Text + images
- **EPUB with embedded fonts**: For rendering validation
- **Very large document (100+ MB)**: For testing file size limits

## File Size Categories

- **Small**: < 1 MB (basic functionality)
- **Medium**: 1-10 MB (typical use case)
- **Large**: 10-50 MB (performance testing)
- **Very Large**: > 50 MB (should be rejected)

## Naming Convention

Use descriptive names to help identify test cases:
- `small-basic.pdf` - Simple PDF document
- `medium-chapters.epub` - EPUB with multiple chapters
- `large-scanned.pdf` - Scanned document for OCR testing
- `complex-formatting.pdf` - Document with complex layout

## Notes

- Do not commit copyrighted material
- Use public domain or self-generated content
- Keep file sizes reasonable for repository storage
- Document expected behavior for each test file
