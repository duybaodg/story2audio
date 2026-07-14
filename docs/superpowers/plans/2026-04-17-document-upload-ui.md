# Document Upload UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a user interface for uploading PDF/EPUB documents, previewing chapter structure, selecting content, and converting to audio with live subtitles.

**Architecture:** Single-page tab-based UI. The "Paste Text" tab contains existing functionality. The "Upload Document" tab adds drag-and-drop upload, chunked progress tracking, collapsible chapter tree, and preview workflow. Both tabs share conversion controls and audio output.

**Tech Stack:** Vanilla JavaScript, CSS (embedded in HTML), FastAPI backend (already implemented)

---

## Task 1: Add Content Endpoint to Backend

**Files:**
- Modify: `document_api.py`

**Purpose:** Enable frontend to retrieve full text of selected chapters for preview and conversion.

- [ ] **Step 1: Add ChapterContentRequest schema**

```python
from pydantic import BaseModel

class ChapterContentRequest(BaseModel):
    chapter_ids: List[str]
```

Add this import and schema near the top of `document_api.py`, after the existing imports.

- [ ] **Step 2: Add content retrieval endpoint**

```python
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
        raise HTTPException(status_code=400, detail="No valid chapters found")

    # Concatenate chapter texts with chapter titles as separators
    combined_sections = []
    for ch in selected_chapters:
        title = ch.get("title", f"Chapter {ch.get('chapter_number', '?')}")
        text = ch.get("full_text", "").strip()
        if text:
            combined_sections.append(f"{title}\n{text}")

    combined_text = "\n\n".join(combined_sections)

    return {
        "document_id": document_id,
        "filename": document.filename,
        "chapter_count": len(selected_chapters),
        "word_count": sum(ch.get("word_count", 0) for ch in selected_chapters),
        "text": combined_text
    }
```

Add this endpoint at the end of `document_api.py`, before the file ends.

- [ ] **Step 3: Test the endpoint manually**

Run: `uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload`

Then test with curl after uploading a document.

- [ ] **Step 4: Commit**

```bash
git add document_api.py
git commit -m "feat: add document content endpoint for selected chapters"
```

---

## Task 2: Add Tab Structure to HTML

**Files:**
- Modify: `templates/index.html:230-280`

**Purpose:** Create tab navigation between "Paste Text" and "Upload Document" workflows.

- [ ] **Step 1: Add tab navigation HTML**

Find the `<h1>` element and the `.card` div. Insert the tab navigation between them.

- [ ] **Step 2: Add tab navigation CSS**

Add to the `<style>` section.

- [ ] **Step 3: Add tab switching JavaScript**

Add to the `<script>` section.

- [ ] **Step 4: Test tab switching**

- [ ] **Step 5: Commit**

---

## Task 3: Add Upload UI Components

**Files:**
- Modify: `templates/index.html`

**Purpose:** Create drag-and-drop upload zone, file picker, and progress indicators.

- [ ] **Step 1: Add upload UI HTML**

- [ ] **Step 2: Add upload UI CSS**

- [ ] **Step 3: Test the upload UI layout**

- [ ] **Step 4: Commit**

---

## Task 4: Implement File Validation and Upload Logic

**Files:**
- Modify: `templates/index.html` (JavaScript section)

**Purpose:** Validate files client-side and implement chunked upload with progress.

- [ ] **Step 1: Add file validation function**

- [ ] **Step 2: Add initiate upload function**

- [ ] **Step 3: Add chunk upload function**

- [ ] **Step 4: Add complete upload function**

- [ ] **Step 5: Test validation manually**

- [ ] **Step 6: Commit**

---

## Task 5: Connect Upload UI to Upload Logic

**Files:**
- Modify: `templates/index.html` (JavaScript section)

**Purpose:** Wire up drag-and-drop, file picker, and progress display to upload functions.

- [ ] **Step 1: Add drag-and-drop handlers**

- [ ] **Step 2: Add file selection handler**

- [ ] **Step 3: Add error display functions**

- [ ] **Step 4: Test file selection and upload start**

- [ ] **Step 5: Commit**

---

## Task 6: Implement Extraction Progress Streaming

**Files:**
- Modify: `templates/index.html` (JavaScript section)

**Purpose:** Connect to SSE endpoint for real-time extraction progress.

- [ ] **Step 1: Add extraction stream handler**

- [ ] **Step 2: Add no chapters dialog**

- [ ] **Step 3: Test extraction progress**

- [ ] **Step 4: Commit**

---

## Task 7: Implement Chapter Tree Rendering

**Files:**
- Modify: `templates/index.html` (JavaScript section)

**Purpose:** Display collapsible chapter tree with checkboxes and metadata.

- [ ] **Step 1: Add chapter tree state**

- [ ] **Step 2: Add chapter tree rendering function**

- [ ] **Step 3: Add group and chapter rendering**

- [ ] **Step 4: Add chapter event listeners**

- [ ] **Step 5: Add selection management functions**

- [ ] **Step 6: Test chapter tree**

- [ ] **Step 7: Commit**

---

## Task 8: Implement Preview and Convert Flow

**Files:**
- Modify: `templates/index.html` (JavaScript section)

**Purpose:** Fetch selected chapters' content, switch to Paste Text tab, populate textarea.

- [ ] **Step 1: Add preview function**

- [ ] **Step 2: Add source indicator CSS**

- [ ] **Step 3: Modify convert button to handle document source**

- [ ] **Step 4: Test preview flow**

- [ ] **Step 5: Commit**

---

## Task 9: Add Error Modals and Refine UX

**Files:**
- Modify: `templates/index.html`

**Purpose:** Replace alert() dialogs with proper modal overlays for better UX.

- [ ] **Step 1: Add modal HTML**

- [ ] **Step 2: Add modal CSS**

- [ ] **Step 3: Add modal JavaScript**

- [ ] **Step 4: Replace alert calls with modals**

- [ ] **Step 5: Test modals**

- [ ] **Step 6: Final commit**

---

## Task 10: End-to-End Testing

**Files:**
- Test: Manual testing via browser

**Purpose:** Verify complete workflow from upload to audio conversion.

- [ ] **Step 1: Test complete upload workflow**

- [ ] **Step 2: Test error scenarios**

- [ ] **Step 3: Test tab switching**

- [ ] **Step 4: Test bulk selection**

- [ ] **Step 5: Test chapter tree interaction**

- [ ] **Step 6: Test audio conversion with document source**

- [ ] **Step 7: Test concurrent workflows**

- [ ] **Step 8: Document any issues**

- [ ] **Step 9: Final verification commit**

---
