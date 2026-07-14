# Document Upload UI Design

**Date:** 2026-04-17
**Status:** Approved
**Related Feature:** Document Upload API (backend implemented)

---

## Overview

Add a user interface for the document upload feature (PDF/EPUB) that was previously backend-only. The UI enables users to upload documents, preview chapter structure, select content, and convert to audio with live subtitles.

---

## Architecture

### Tab-Based Layout

Two stacked cards with tab navigation:

```
┌─────────────────────────────────────────────────┐
│  [ 📄 Paste Text ]  [ 📁 Upload Document ]      │  ← Tab selector
├─────────────────────────────────────────────────┤
│                                                  │
│  (existing textarea + controls)                  │
│  or upload UI (depending on active tab)          │
│                                                  │
└─────────────────────────────────────────────────┘
```

- **Paste Text tab** — existing workflow (type/paste → convert)
- **Upload Document tab** — new workflow (upload → extract → select → preview → convert)
- Both workflows share the same controls (language, voice, speed, convert button) and output (audio player, subtitles, downloads)

### Workflow Summary

```
Upload Tab:
  1. Drag & drop or browse for PDF/EPUB
  2. Chunked upload with progress
  3. Server-side extraction with progress streaming
  4. Chapter tree display with selection
  5. Preview selected content
  6. Auto-switch to Paste Text tab with content loaded
  7. User can edit, then convert with existing controls
```

---

## Upload Interface

### Visual Design

```
┌─────────────────────────────────────────────────┐
│  [ 📄 Paste Text ]  [ 📁 Upload Document ]      │
├─────────────────────────────────────────────────┤
│                                                  │
│  ┌──────────────────────────────────────────┐  │
│  │   📂 Drag & drop PDF or EPUB here         │  │
│  │   or click to browse                     │  │
│  │                                          │  │
│  │   [Browse Files]                         │  │
│  │                                          │  │
│  │   Max file size: 50 MB                   │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### Upload States

1. **Idle** — Drop zone with browse button
2. **Validating** — Client-side check (file type: `.pdf`, `.epub`; size: ≤50MB)
3. **Uploading** — Chunked upload progress
   - Display: `Chunk X/Y · XX MB / YY MB`
   - Progress bar updates per chunk
4. **Extracting** — Server-side extraction progress
   - SSE stream from `/document/{id}/extract/stream`
   - Display: `Extracting page X/Y`
5. **Ready** — Chapter tree appears

---

## Chapter Tree & Selection

### Visual Design

```
┌─────────────────────────────────────────────────┐
│  [ 📄 Paste Text ]  [ 📁 Upload Document ]      │
├─────────────────────────────────────────────────┤
│                                                  │
│  ✓ ebook.pdf — 238 pages · 12 chapters found    │
│                                                  │
│  ┌──────────────────────────────────────────┐  │
│  │  ▼ Part I: The Beginning  (12,340 words) │  │
│  │    ├─ [✓] Chapter 1: Introduction        │  │
│  │    │    (1,240 words, ~5 min)            │  │
│  │    ├─ [✓] Chapter 2: The Discovery      │  │
│  │    │    (2,100 words, ~9 min)            │  │
│  │    └─ [  ] Chapter 3: First Steps        │  │
│  │         (980 words, ~4 min)              │  │
│  │                                          │  │
│  │  ▶ Part II: The Journey  (18,920 words)  │  │
│  │                                          │  │
│  │  ▶ Appendix  (2,100 words)               │  │
│  └──────────────────────────────────────────┘  │
│                                                  │
│  [Select All] [Clear Selection]                 │
│                                                  │
│  3 chapters selected · ~4,320 words · ~18 min   │
│                                                  │
│            [Preview Selected → Convert]         │
│                                                  │
└─────────────────────────────────────────────────┘
```

### Features

- **Collapsible sections** — Click ▶/▼ to expand/collapse
- **Checkboxes** — Select individual chapters or entire sections
- **Metadata per chapter** — Word count, estimated TTS duration
- **Bulk actions** — Select All / Clear Selection buttons
- **Summary bar** — Total selected: chapter count, word count, estimated duration

### Duration Calculation

Approximate TTS duration: `words / 150` (average reading speed)

---

## Preview & Convert Flow

### Transition to Preview

When user clicks **Preview Selected**:

1. **Switch to Paste Text tab** automatically
2. **Populate textarea** with combined text from selected chapters
3. **Add source indicator** above textarea

### Preview UI

```
┌─────────────────────────────────────────────────┐
│  [ 📄 Paste Text ]  [ 📁 Upload Document ]      │
├─────────────────────────────────────────────────┤
│                                                  │
│  📋 From: ebook.pdf — Chapters 1, 2, 4          │
│       [Change selection]                         │
│                                                  │
│  ┌──────────────────────────────────────────┐  │
│  │  Chapter 1: Introduction                  │  │
│  │  [extracted text content...]             │  │
│  │                                          │  │
│  │  Chapter 2: The Discovery                │  │
│  │  [extracted text content...]             │  │
│  │                                          │  │
│  └──────────────────────────────────────────┘  │
│                                                  │
│  [Language: ▼] [Voice: ▼] [Speed: ▼]           │
│            [▶ Convert to Audio]                 │
│                                                  │
└─────────────────────────────────────────────────┘
```

### User Actions in Preview

- **Edit text** — Modify extracted content before conversion (useful for OCR fixes)
- **Change selection** — Return to Upload tab to modify chapter selection
- **Convert** — Use existing conversion controls

---

## Error Handling

### Validation Errors (Pre-upload)

Shown for: invalid file type, file > 50MB

```
┌─────────────────────────────────────────────────┐
│  ⚠️ Invalid File                               │
│                                                  │
│  • Only PDF and EPUB files are supported        │
│  • Maximum file size: 50 MB                     │
│                                                  │
│  Your file is 67.5 MB. Please split it and      │
│  upload each part separately.                   │
│                                                  │
│                      [OK]                        │
└─────────────────────────────────────────────────┘
```

### Upload Errors (During Transfer)

Shown for: network failure, server error during chunk upload

```
┌─────────────────────────────────────────────────┐
│  ⚠️ Upload Failed                               │
│                                                  │
│  Connection lost during chunk 3/6.              │
│  Would you like to retry?                       │
│                                                  │
│              [Cancel]  [Retry Upload]           │
└─────────────────────────────────────────────────┘
```

Retry should resume from the last successful chunk.

### Extraction Warnings

Shown when: text quality score is low (potential OCR issues)

```
┌─────────────────────────────────────────────────┐
│  ⚠️ Text Quality Warning                        │
│                                                  │
│  Chapters 3, 5 may have poor OCR quality.       │
│  Consider reviewing the preview before          │
│  converting to audio.                           │
│                                                  │
│                      [Continue]                 │
└─────────────────────────────────────────────────┘
```

### No Chapters Detected

Shown when: document structure cannot be parsed

```
┌─────────────────────────────────────────────────┐
│  ℹ️ No Chapters Detected                        │
│                                                  │
│  We couldn't detect chapter structure.          │
│  The entire document will be treated as         │
│  a single chapter.                              │
│                                                  │
│              [Cancel]  [Continue]               │
└─────────────────────────────────────────────────┘
```

---

## API Integration

### Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/document/upload/initiate` | POST | Start upload session |
| `/document/upload/chunk` | POST | Upload file chunk |
| `/document/upload/complete` | POST | Finalize upload, start extraction |
| `/document/{id}/extract/stream` | GET | SSE stream for extraction progress |
| `/document/{id}/structure` | GET | Get chapter hierarchy |
| `/document/{id}/content` | POST | Get text content for selected chapters |

### Chunk Upload Flow

```javascript
1. POST /document/upload/initiate
   → { upload_id, chunk_size }

2. POST /document/upload/chunk (repeat for each chunk)
   → FormData: { upload_id, chunk_number, chunk: File }

3. POST /document/upload/complete
   → { document_id }

4. GET /document/{document_id}/extract/stream
   → SSE: {"type": "progress", "current": 5, "total": 120}

5. GET /document/{document_id}/structure
   → { chapters: [{id, title, start_page, end_page, word_count}, ...] }

6. POST /document/{document_id}/content
   → { text: "combined content..." }
```

---

## Technical Considerations

### State Management

- **Tab state** — Simple CSS display toggle (`active` class on tabs)
- **Upload state** — Track in JS object: `{ idle, validating, uploading, extracting, ready }`
- **Chunk progress** — Track: `{ uploadId, totalChunks, completedChunks, bytesSent }`
- **Extraction progress** — SSE event listener updates progress bar
- **Chapter selection** — Set of selected chapter IDs, preserved when switching tabs

### Client-Side Validation

```javascript
function validateFile(file) {
    const validTypes = ['application/pdf', 'application/epub+zip'];
    const maxSize = 50 * 1024 * 1024; // 50MB

    if (!validTypes.includes(file.type)) {
        return { valid: false, error: 'Invalid file type' };
    }
    if (file.size > maxSize) {
        return { valid: false, error: 'File exceeds 50MB limit' };
    }
    return { valid: true };
}
```

### Chunk Upload Implementation

- Chunk size: 5MB (matching backend)
- Parallel upload: No — sequential to maintain order
- Retry logic: Retry failed chunks up to 3 times
- Resume capability: Track completed chunks, resume on retry

### Cross-Tab Communication

- Selected chapter content stored in global variable
- When switching to Paste Text tab, populate textarea from stored content
- Source indicator shows document name and selected chapters

---

## Files to Modify

| File | Changes |
|------|---------|
| `templates/index.html` | Add tab structure, upload UI, chapter tree, error modals |
| `static/` | Optionally add icons/styles (can inline in HTML for simplicity) |

---

## Testing Checklist

- [ ] Drag and drop PDF file successfully uploads
- [ ] File picker opens and accepts PDF/EPUB
- [ ] File > 50MB shows validation error
- [ ] Invalid file type shows validation error
- [ ] Upload progress displays correctly (chunk X/Y)
- [ ] Extraction progress streams via SSE
- [ ] Chapter tree renders correctly with hierarchy
- [ ] Collapsing/expanding sections works
- [ ] Selecting/deselecting chapters updates summary
- [ ] Select All / Clear All buttons work
- [ ] Preview Selected switches tabs and populates textarea
- [ ] Change selection returns to Upload tab with preserved selections
- [ ] Converted audio plays with subtitles
- [ ] Upload retry works after network failure
- [ ] Error modals display and dismiss correctly

---

## Future Enhancements (Out of Scope)

- Batch upload multiple documents
- Save/upload session persistence across page reloads
- Chapter reordering before conversion
- Split/merge chapters
- Custom chapter breaks
- OCR settings panel for image-heavy PDFs
