# Project Review Findings

Review date: 2026-06-01

Scope: backend FastAPI app, document upload/extraction flow, TTS generation, tests, and the embedded frontend in `templates/index.html`.

## Backend Findings

### Critical

- `tests/test_job_queue.py` cannot be collected by pytest.
  - Location: `tests/test_job_queue.py:186`, `tests/test_job_queue.py:199`
  - Problem: `test_cancel_job()` is a normal function but uses `await`.
  - Impact: the whole test suite stops during collection.
  - Fix: make it `async def` and add `@pytest.mark.asyncio`. Also add `pytest-asyncio` to dev dependencies because pytest currently warns that `pytest.mark.asyncio` is unknown.

### High

- `/tts/session/{cache_id}` raises `TypeError` for valid cache IDs.
  - Location: `main.py:150`, `main.py:152`, `main.py:352`
  - Problem: `verify_session()` calls `get_audio_path(cache_id, "mp3")` and `get_audio_path(cache_id, "wav")`, but `get_audio_path()` only accepts one argument.
  - Impact: session verification returns 500 instead of cache metadata or 404.
  - Fix: either extend `get_audio_path()` to accept an extension or use separate path construction for `.mp3` and `.wav`.

- Edge subtitle cues are duplicated.
  - Location: `main.py:1506`, `main.py:1513`, `main.py:1515`, `main.py:1520`
  - Problem: the code that indexes, appends, writes JSONL, and updates `subtitle_cues` runs twice for every Edge chunk.
  - Impact: duplicate subtitle cues, inflated cue counts, and incorrect cue indexes.
  - Fix: remove the second duplicated block.

- Repeat `/tts/start` calls can corrupt an active generation.
  - Location: `main.py:1451`, `main.py:1683`, `main.py:1692`
  - Problem: generation sets status to `generating`, but the duplicate-request guard only treats `queued` and `processing` as active. A repeat request can call `cleanup_incomplete_cache()` while the first worker is writing files.
  - Impact: active generation files can be deleted or corrupted.
  - Fix: include `generating` in the active-status guard and centralize active generation states.

### Medium

- Document cleanup is registered but not actually scheduled.
  - Location: `main.py:2139`, `main.py:2141`, `file_processor.py:402`, `file_processor.py:416`
  - Problem: startup creates a local `BackgroundTasks` instance and registers cleanup on it, but FastAPI never executes that object.
  - Impact: expired document/session cleanup scheduler does not run.
  - Fix: start it with `asyncio.create_task(start_cleanup_scheduler())` or move it into a lifespan task.

- Upload completion response claims extraction is starting, but no extraction is started.
  - Location: `document_api.py:112`, `document_api.py:122`
  - Problem: `upload_complete()` has a TODO import for `BackgroundTasks` and returns `"Upload complete, extraction starting"`, but it does not start extraction.
  - Impact: UI/API messaging is misleading; documents remain `uploading` until another endpoint starts extraction.
  - Fix: either submit an extraction job after upload completion or change the message/status to reflect queue-based extraction.

- `uv.lock` is stale relative to `pyproject.toml`.
  - Location: `pyproject.toml:28`, `uv.lock` package metadata near the `ebook2audio` package section
  - Problem: `pyproject.toml` declares `redis>=5.0.0`, but `uv.lock` does not include Redis in the project dependency metadata.
  - Impact: locked installs can miss a runtime dependency used by `rate_limiter.py` and `main.py`.
  - Fix: intentionally regenerate `uv.lock` with `uv lock` or `uv sync` and commit the resulting lockfile.

- Chunk upload accepts invalid chunk numbers.
  - Location: `file_processor.py:198`, `file_processor.py:216`
  - Problem: any `chunk_number >= total_full_chunks` is treated as the last chunk, and negative values are not rejected.
  - Impact: out-of-range files such as `chunk_-1` or `chunk_99` can be written.
  - Fix: compute `expected_chunks` and reject `chunk_number < 0` or `chunk_number >= expected_chunks`.

### Low

- Pydantic v2 deprecation warning for `min_items`.
  - Location: `document_api.py:31`
  - Problem: `Field(min_items=1)` is deprecated in Pydantic v2.
  - Fix: use `Field(min_length=1)`.

- FastAPI `on_event` is deprecated.
  - Location: `main.py:2136`, `main.py:2168`
  - Problem: FastAPI warns that `on_event` should be replaced by lifespan handlers.
  - Fix: migrate startup/shutdown work into an app lifespan context.

## Frontend Findings

Main file: `templates/index.html`

### Critical

- Queue retry calls the wrong backend endpoint identifier and submits another job.
  - Location: `templates/index.html:1507`, `templates/index.html:1512`, `templates/index.html:1520`, `templates/index.html:1523`
  - Problem: `retryFailedJob(docId)` calls `/document/job/${docId}/retry`, but the backend retry endpoint expects a `job_id`. The returned `new_job_id` is ignored, and `startExtractionWithJob()` submits another new extraction job.
  - Impact: retry from the UI cannot work correctly and may create duplicate jobs.
  - Fix: store the failed job ID in queue state or expose it from backend queue data, then poll the returned `new_job_id` directly.

### High

- Session persistence is mostly non-functional.
  - Location: `templates/index.html:3591`, `templates/index.html:3595`, `templates/index.html:3596`, `templates/index.html:3607`, `templates/index.html:1020`, `templates/index.html:1029`
  - Problems:
    - `getCurrentSessionState()` queries `select[name="voice"]` and `select[name="engine"]`, but the selects only have IDs.
    - It reads `window.currentCacheId`, while the normal convert flow stores `currentCacheId` as a lexical variable.
    - `getCurrentSessionState()` is never called in normal use.
  - Impact: normal user progress is not saved/restored reliably.
  - Fix: use the existing element IDs, use one cache ID state source, and call `saveSession(getCurrentSessionState())` on meaningful state changes.

- Stop/delete generation controls are dead.
  - Location: `templates/index.html:1059`, `templates/index.html:3720`, `templates/index.html:3776`
  - Problem: buttons and handlers exist, but `showGenerationActions()` is never called. The handlers also depend on `window.currentCacheId`, which the main conversion flow does not set.
  - Impact: users cannot stop/delete active generation from the visible UI.
  - Fix: call `showGenerationActions(currentCacheId)` when generation starts, call `hideGenerationActions()` on completion/failure, and unify `currentCacheId` state.

- VieNeu audio quality selector has no backend effect.
  - Location: `templates/index.html:1033`, `templates/index.html:3411`, `main.py` `TTSRequest`
  - Problem: frontend sends `audio_quality`, but backend request schema and `start_tts()` do not use it.
  - Impact: UI suggests quality control exists, but it is ignored.
  - Fix: add the field to `TTSRequest` and pass it into generation, or remove/hide the control.

### Medium

- Modal helper can inject untrusted HTML.
  - Location: `templates/index.html:1851`, `templates/index.html:1807`, `templates/index.html:2792`
  - Problem: `showModal()` writes `options.message` with `innerHTML`; some messages can include backend error text.
  - Impact: possible XSS if an error message includes HTML.
  - Fix: render messages with `textContent`, and handle line breaks by creating text nodes plus `<br>` elements.

- `md5ArrayBuffer()` leaks globals.
  - Location: `templates/index.html:2071`, `templates/index.html:2080`, `templates/index.html:2085`
  - Problem: `lWordCount` is assigned without `let` or `const` in `convertToWordArray()`.
  - Impact: browser non-strict mode creates a global; strict mode would throw. This is fragile in upload checksum logic.
  - Fix: declare `let lWordCount = 0;` inside `convertToWordArray()`.

- The "no chapters found" fallback cannot retrieve content.
  - Location: `templates/index.html:2518`, `templates/index.html:2528`, `templates/index.html:2534`
  - Problem: the fallback creates a fake chapter ID like `${documentId}_full`, then routes through content retrieval. The backend only knows extracted chapter IDs from metadata.
  - Impact: conversion from this fallback path will fail to load document content.
  - Fix: either have the backend create/store a real full-document chapter, or add a backend endpoint for full document content.

- Queue refresh during job polling is nondeterministic.
  - Location: `templates/index.html:1425`
  - Problem: queue refresh uses `Math.random() < 0.2`.
  - Impact: UI freshness depends on chance and is hard to reason about/test.
  - Fix: refresh on status changes, on completion/failure, or with a fixed cadence.

### Low

- `node --check` passes for the embedded JavaScript.
  - This means the frontend issues above are runtime/state/API-contract issues, not syntax errors.

## Duplicated Or Unused Backend Code

- Duplicate extraction implementation exists in `text_extractor.py`.
  - Async streaming path: `text_extractor.py:231`
  - Blocking worker path: `text_extractor.py:366` and `text_extractor.py:470`
  - Problem: PDF/EPUB extraction and chapter serialization logic are implemented in separate async and blocking paths.
  - Suggested cleanup: factor common extraction/chapter serialization helpers and keep only thin wrappers for streaming/worker execution.

- Test router registration is duplicated.
  - Location: `tests/test_document_api.py:16`, `tests/test_document_api.py:18`
  - Problem: importing `app` from `main` already includes the document router, then the test includes the same router again.
  - Suggested cleanup: remove the extra `app.include_router(router)` from the test.

- Likely unused runtime functions:
  - `file_processor.py:65` `load_session()`
  - `file_processor.py:94` `get_pending_sessions()`
  - `text_extractor.py:81` `extract_chapters_parallel()`
  - `text_extractor.py:555` `extract_chapter_with_ocr()`
  - These are referenced by docs/cache artifacts but not active runtime code found in this review.

- Unused imports found by AST scan:
  - `main.py`: `timedelta`, `Union`, `get_file_extension`
  - `document_api.py`: `Optional`, `asyncio`, `Document`, `Chapter`, `ExtractionJob`, local `BackgroundTasks`
  - `file_processor.py`: `DocumentStatus`
  - `job_queue.py`: `Path`
  - `text_extractor.py`: `Any`, `Path`, `FileType`, `Image`
  - `vieneu_audio_quality.py`: `detect_nonsilent`
  - `vieneu_model.py`: `Optional`, `Dict`
  - `vietnamese_text_processor.py`: `Optional`

## Duplicated Or Unused Frontend Code

- Dead SSE extraction UI path.
  - Location: `templates/index.html:2419`
  - Problem: `startExtraction()` and related SSE extraction progress code appear unused. Upload completion now switches to the queue, and extraction uses job polling.
  - Suggested cleanup: remove this path if queue-based extraction is the intended workflow.

- Mostly dead inline chapter tree path.
  - Location: `templates/index.html:2540` through the selection/preview helpers around `templates/index.html:2806`
  - Problem: `showChapterModal()` supersedes the inline `chapterContainer` workflow.
  - Suggested cleanup: remove `renderChapterTree()`, `groupChapters()`, `renderGroup()`, `renderChapter()`, `setupChapterListeners()`, `previewSelectedChapters()`, and related selection state if modal selection is the intended workflow.

- Unused frontend helper.
  - Location: `templates/index.html:2192`
  - Problem: `md5()` wrapper appears unused; upload uses `md5ArrayBuffer()` directly.

- Unused CSS class.
  - Location: `templates/index.html:304`
  - Problem: `.upload-error` is defined but never used.

- Duplicated modal button CSS.
  - Main stylesheet: `templates/index.html:595`
  - Dynamically injected modal chapter styles: `templates/index.html:1722`
  - Problem: `.modal-btn` and `.modal-btn.primary` are defined twice.
  - Suggested cleanup: keep all modal styles in the main stylesheet.

## Verification Performed

- Ran `python -m py_compile main.py document_api.py file_processor.py job_queue.py text_extractor.py rate_limiter.py tests/test_job_queue.py`.
  - Result: failed with `SyntaxError: 'await' outside async function` in `tests/test_job_queue.py:199`.

- Ran `uv run pytest -q`.
  - Result: failed during test collection on the same syntax error.
  - Additional warnings: missing `pytest-asyncio`, Pydantic `min_items` deprecation, FastAPI `on_event` deprecation.

- Extracted the embedded frontend script and ran `node --check`.
  - Result: JavaScript syntax passed.

## Notes

- `uv run pytest` updated `uv.lock` while resolving dependencies during review. That incidental lockfile change was restored; the working tree was left without tracked modifications before this findings file was added.
