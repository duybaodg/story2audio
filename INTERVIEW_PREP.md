# Ebook2Audio Interview Preparation

## Project review

This is a strong junior-to-mid full-stack/backend portfolio project. It demonstrates more than CRUD: asynchronous work, streaming, caching, file validation, resource controls, concurrency, crash recovery, security boundaries, Docker, and CI/CD.

The main interview risk is calling it “fully production-ready” or “horizontally scalable.” It currently has deliberate single-web-process and local-storage assumptions.

Verified on 15 September 2026:

- 81 tests passed.
- 2 real PDF/EPUB integration tests are skipped.
- Python and JavaScript syntax checks passed.
- Docker Compose configuration passed.
- No source files were changed during the review.

Key implementation files:

- main.py — FastAPI routes, TTS orchestration, streaming, subtitles, and caching.
- tts_queue.py — Redis VieNeu queue, cancellation, heartbeat, and recovery primitives.
- tts_worker.py — dedicated sequential VieNeu worker.
- document_api.py — upload, extraction, document, and extraction-job routes.
- file_processor.py — upload validation, assembly, persistence, and cleanup.
- job_queue.py — bounded extraction workers and persisted job status.
- static/app.js — browser upload, streaming, playback, subtitles, and UI state.
- docker-compose.yml — Redis, web, and optional VieNeu-worker services.

## Your 60-second opening answer

> Ebook2Audio is a FastAPI application that converts pasted text or uploaded PDF and EPUB documents into audio. It supports Edge TTS, gTTS, and a Vietnamese VieNeu model.
>
> The interesting engineering work is around long-running generation. Text is divided into natural chunks, audio is written incrementally, and the browser begins playback through MediaSource before the full file is finished. Edge TTS word-boundary events are converted into live subtitles delivered with Server-Sent Events.
>
> VieNeu is resource-heavy and its runtime is not safely parallel, so I separated it from the web process into a single-model worker using a Redis queue. The application also includes resumable file uploads, persisted extraction jobs, signed browser sessions, shared-cache ownership, rate limiting, crash recovery, Docker deployment, and CI/CD.
>
> The current design targets a small single-server deployment. If traffic required horizontal scaling, I would move job and document state out of process, use object storage, and make queue admission atomic.

## Architecture to memorize

    Browser
      ├── Text ──> FastAPI ──> Edge/gTTS background generation
      │                       └── Redis queue ──> VieNeu worker
      │
      ├── PDF/EPUB ──> chunk upload ──> validation/assembly
      │                                └── extraction thread pool
      │
      └── Playback <── growing MP3 file + status polling
          Subtitles <── SSE cue stream

    Persistent volumes: audio cache, documents, jobs, model cache
    Redis: rate limits, VieNeu queue, cancellation, worker heartbeat

## Core interview questions and prepared answers

### Product and ownership

**1. What problem does this solve?**

It lets users turn long-form text and ebooks into playable audio without waiting for the whole conversion. It also supports chapter selection and live subtitles for Edge TTS.

**2. Who is the target user?**

Vietnamese readers are the primary audience because of VieNeu, but Edge TTS and gTTS extend support to English, Japanese, Chinese, Korean, French, and German.

**3. Did you build this project from scratch?**

Do not claim that. The repository explicitly credits dvchd/story2audio.

> I began with an existing MIT-licensed project and extended it. My specific contributions were [state only your actual work]. I kept attribution in the README and UI. I can explain my changes down to their failure modes and tests.

**4. What exactly did you contribute?**

Prepare an exact list based on your own work. Possible areas visible in later history include document upload, VieNeu integration, Redis worker separation, security improvements, resource limits, UI work, and deployment automation. Only claim the ones you personally implemented.

**5. Why is this more than an API wrapper?**

The providers perform synthesis, but the application owns chunking, provider selection, scheduling, cache consistency, incremental playback, subtitle construction, upload integrity, extraction, ownership, cancellation, and deployment.

**6. What was the hardest engineering problem?**

A good answer:

> The hardest part was coordinating long-running generation with streaming, cancellation, caching, and multiple browser sessions. The output is useful before it is complete, but incomplete output must never be treated as a valid cache entry. I solved that with explicit metadata states, file-size validation, atomic metadata replacement, ownership tracking, and stable completion checks in the stream.

**7. What was the most important design decision?**

Separating VieNeu from FastAPI. A heavy, non-thread-safe model should not consume web-process memory or block request handling.

**8. How do you measure success?**

Current engineering measures are generation success rate, time to first audio, total conversion time, cache-hit rate, queue wait time, extraction failure rate, and cancellation rate. The project does not currently expose those as production metrics.

### Backend architecture

**9. Why FastAPI?**

It provides typed request models, automatic OpenAPI documentation, async endpoints, and streaming responses. Those fit network-bound TTS calls and SSE well.

**10. How do you avoid blocking FastAPI’s event loop?**

Edge TTS is asynchronous. Blocking gTTS work runs in a subprocess through an executor, document parsing runs in a ThreadPoolExecutor, and VieNeu runs in a separate worker.

**11. Why use FastAPI BackgroundTasks for Edge and gTTS?**

It is simple and sufficient for the current single-server scope. The trade-off is that these jobs are not durable across a web-process crash.

**12. Why does VieNeu use a different execution path?**

VieNeu has a large memory footprint and uses a runtime that is not safely parallel. Redis decouples admission from execution, while one worker owns one warmed model.

**13. What are the main application states?**

TTS metadata uses states such as queued, processing, generating, completed, failed, and stopped. Documents use uploading, extracting, ready, and error.

**14. Why persist status to JSON if some state is in memory?**

Memory gives quick access during local generation; JSON survives restarts and lets the separate worker communicate progress through shared storage. This is pragmatic but not a substitute for a database at larger scale.

**15. Why no database?**

For a small single-server deployment, local JSON and mounted volumes minimize operational complexity. A database becomes necessary for multiple web replicas, transactional updates, querying, user accounts, or longer retention.

**16. How are errors represented?**

Input problems generally return 400 or 413, rate limits return 429, capacity/dependency problems return 503, storage errors return 507, and unauthorized resource lookups intentionally return 404.

**17. Why return 404 rather than 403 for another user’s cache?**

It avoids confirming that another user’s resource exists, reducing enumeration leakage.

**18. What do the health endpoints test?**

/health checks Redis availability. /tts/health checks the worker’s Redis heartbeat. Docker additionally checks a worker-ready file created after model initialization.

### Caching

**19. How is a cache ID generated?**

It hashes normalized text, voice, engine, language, and, for VieNeu, the model and audio-quality variant.

**20. Why use content-addressed caching?**

Identical synthesis requests can reuse one result. It also naturally provides a deterministic deduplication key.

**21. Why use MD5? Isn’t MD5 insecure?**

> MD5 is not used for passwords, signatures, or trust. It is used as a compact deterministic cache key and upload integrity checksum. Collision resistance is not treated as an authorization control.

**22. How do you know a cached file is valid?**

The metadata must say completed; the audio must exist, be non-empty, and match the recorded final size. Edge cache hits also require the subtitle artifacts.

**23. What happens when two users request identical content?**

They share the generated artifact but receive separate ownership entries. Removing it from one session only unlinks that session. The last owner can cancel or delete it.

**24. How did you prevent ownership races?**

Owner updates and metadata writes are serialized with a cross-process file lock. Worker updates preserve authorization fields, and an owner_state=closed marker prevents a new request from attaching during final deletion.

**25. Why use atomic rename for metadata?**

Writing a temporary file and calling os.replace prevents readers from observing partially written JSON after a crash or concurrent read.

**26. What invalidates a cache?**

Missing or mismatched files, incomplete metadata, incompatible VieNeu container versions, missing Edge subtitle artifacts, explicit deletion, or retention cleanup.

**27. What is a cache limitation?**

Edge/gTTS cache keys do not contain a provider or application version, so provider behavior changes could reuse an older result until retention cleanup.

### TTS and audio processing

**28. Compare the three TTS engines.**

- Edge: asynchronous, multilingual, word timing, live subtitles.
- gTTS: simple fallback, no timing data, isolated in a killable subprocess.
- VieNeu: higher-quality Vietnamese output, local heavy model, sequential worker, no word timings.

**29. Why split text into chunks?**

Providers have practical input limits, shorter chunks reduce time to first audio, failures affect less work, and cancellation can be checked between chunks.

**30. How are natural boundaries preserved?**

The main splitter prioritizes paragraphs and multilingual sentence endings, then punctuation and whitespace. It hard-cuts only when no safer boundary fits.

**31. Why treat CJK text differently?**

CJK characters carry more information per character and often do not use spaces. The code uses shorter limits and weighted character lengths.

**32. Why does VieNeu use roughly 500-character chunks?**

It balances synthesis reliability, latency, and natural phrasing for the current model. The size remains configurable because model and hardware performance can differ.

**33. Why process VieNeu sequentially?**

The underlying llama.cpp-based runtime is not thread-safe in this use and duplicate model instances are memory-expensive. Reliability and bounded memory were prioritized over throughput.

**34. Why warm the model?**

Warmup pays initialization and first-inference costs before the worker advertises readiness, reducing latency and surprises on the first real request.

**35. Why INT8?**

It reduces memory and compute requirements enough for a small server. The trade-off may be a slight quality difference compared with higher precision.

**36. How is VieNeu audio improved?**

The pipeline converts model output to int16, conditionally normalizes loudness, applies short fades, inserts pauses between chunks, and encodes 128 kbps or 192 kbps MP3.

**37. Why use constant-bitrate MP3?**

Concatenating independently encoded variable-bitrate chunks can leave multiple Xing/VBR headers and confuse players. CBR plus stripping later ID3 headers is more predictable.

**38. Why run a final FFmpeg remux?**

It normalizes the concatenated MP3 container into a single coherent final file. Replacement is atomic so the previous output is not partially overwritten.

**39. How does live audio work?**

Generation appends audio to a cache file. /tts/stream/{cache_id} repeatedly reads newly available bytes and yields them. The frontend feeds those bytes to a MediaSource buffer.

**40. How do you prevent the live stream from ending too early?**

The stream compares sent bytes with the recorded final size and requires repeated stable completion checks before closing.

**41. What happens when MediaSource is unavailable?**

The frontend falls back to status polling and direct playback after completion.

**42. How are subtitles generated?**

Edge emits word or sentence boundary events. The server groups them into readable cues, offsets each chunk using accumulated audio duration, streams incremental JSONL cues over SSE, and finally writes JSON, SRT, and WebVTT.

**43. Why parse MP3 frames?**

Boundary timestamps restart for each TTS chunk. MP3 frame duration provides the cumulative offset needed to align the next chunk’s subtitle cues.

**44. Why don’t gTTS and VieNeu have subtitles?**

Their current integrations do not provide reliable word-level timing. Inventing timings from word counts would produce misleading synchronization.

**45. Does lossless WAV currently work end to end?**

This is an interviewer trap.

> The audio helper supports WAV, and the UI still shows the option, but /tts/start currently converts a lossless request to high-quality MP3 because the cache and download endpoints are MP3-oriented. I would either remove the UI option or finish WAV-aware paths and media types.

### Redis, concurrency, and reliability

**46. Explain the VieNeu queue.**

The web app serializes a job and LPUSHes it. The worker uses BRPOPLPUSH to atomically move it into a processing list. Successful or handled-failed jobs are acknowledged with LREM.

**47. Is it FIFO?**

Yes: producers push on the left and the worker pops from the right.

**48. Is delivery exactly once?**

No.

> It is closer to at-least-once during process crashes. An in-flight job stays in the processing list and is requeued when the worker restarts. The generation path checks for a valid completed cache, making replay safe in common cases.

**49. What happens if the worker crashes midway through a job?**

The raw job remains in the processing list. On restart it is recovered to the queue. Partial runtime files are removed when generation restarts.

**50. What if it crashes after completing the output but before acknowledging Redis?**

The job is recovered, but cache validation sees a completed valid artifact and generation returns without recomputing.

**51. Are ordinary job failures automatically retried?**

No. The worker persists a failed state and acknowledges the job. Repeating the same request can clean the incomplete cache and enqueue a new attempt.

**52. How is cancellation implemented?**

Local jobs use an in-memory flag; VieNeu jobs use a Redis cancellation key with TTL. Both are checked between chunks.

**53. Does cancellation stop the current model call immediately?**

No. It takes effect at the next chunk boundary. Immediate interruption would require provider-specific process isolation or cooperative cancellation.

**54. How is backpressure implemented?**

VieNeu has a maximum Redis queue size. Local Edge/gTTS generation uses a bounded semaphore and returns 503 with Retry-After when full.

**55. Is the VieNeu queue-size check atomic?**

No. LLEN followed by LPUSH can exceed the cap when multiple web replicas enqueue simultaneously. The code documents replacing it with a Lua transaction if replicas are added.

**56. Why is the rate limiter atomic but the queue cap isn’t?**

The rate limiter already uses one Lua script to check all windows and insert the request together. Queue admission currently assumes one web process, so the simpler operation was accepted.

**57. How is the worker monitored?**

A daemon thread refreshes a Redis heartbeat with a TTL. If the worker stops, the key expires and /tts/health returns 503.

**58. What locks exist?**

- Model lock: serializes inference.
- Per-cache async lock: avoids duplicate generation in one process.
- Metadata file lock: protects cross-process owner/status updates.
- Extraction-start lock: avoids duplicate jobs in one web process.
- Chunk-slot lock: limits concurrent upload chunks per upload.

**59. What reliability weakness remains for Edge/gTTS?**

Those jobs belong to the web process. A restart can leave persisted metadata saying processing without a running job. Moving all providers to a durable queue would solve that.

### Document upload and extraction

**60. Walk through an upload.**

The browser computes MD5, initiates a session, uploads sequential 5 MB chunks, retries failures, and requests completion. The server assembles the chunks, verifies size and checksum, detects the real content type, stores the document, and starts extraction.

**61. Why chunk uploads?**

They bound individual request sizes, make retries cheaper, and provide useful progress for files up to 50 MB.

**62. Are duplicate chunk requests safe?**

Yes. An identical duplicate is accepted. A duplicate with different content is rejected.

**63. Why verify both extension and file content?**

Extensions and MIME headers are user-controlled. The server checks PDF magic bytes or EPUB ZIP structure and requires the detected type to match the declared extension.

**64. How do you defend against ZIP bombs?**

EPUB validation limits entry count, each expanded entry, total expanded size, and compression ratio. It also reads entries under limits before EbookLib parses them.

**65. How is PDF extraction performed?**

It tries pdfplumber first and falls back to PyPDF2 if parsing fails. Extracted text is capped to prevent memory and storage exhaustion.

**66. How are chapters detected?**

PDF text uses regex heuristics for headings such as “Chapter 1,” “Part 1,” and numeric headings. If none are found, the full document becomes one chapter. EPUB documents primarily use document items as sections.

**67. Is OCR implemented?**

No.

> The project calculates a heuristic quality score and flags needs_ocr, but it does not perform OCR. OCR would be a separate pipeline with stricter resource limits.

**68. How is extraction progress streamed?**

Extraction runs once in the bounded thread pool and writes progress and discovered chapters to the job JSON. SSE polls that persisted job and emits new progress/chapter events.

**69. What happens if two clients open the extraction stream?**

For the same document and process, _ensure_extraction_job lets later streams join the existing job instead of starting duplicate extraction.

**70. How are extraction jobs recovered after a crash?**

Persisted jobs left in RUNNING state are marked failed at startup. They can then be retried.

**71. Is the extraction timeout a hard timeout?**

No. It is checked when the extractor calls the progress callback. A parser stuck inside one blocking call may exceed it. Hard isolation would require a subprocess.

**72. Are upload sessions durable?**

Completed documents are persisted and recovered. In-progress upload sessions live in memory and are not resumable across a server restart.

### Frontend and streaming

**73. Why use SSE rather than WebSockets?**

Progress and subtitle data are one-way server-to-browser streams. SSE provides native reconnection, event types, and Last-Event-ID without WebSocket lifecycle complexity.

**74. Why isn’t audio also sent through SSE?**

SSE is text-event oriented. Audio uses a normal binary streaming response consumed through the Fetch API and MediaSource.

**75. How does subtitle resumption work?**

The server reads Last-Event-ID and skips cues whose index has already been delivered.

**76. How are subtitles synchronized during playback?**

The frontend compares audio.currentTime with sorted cue start/end times inside requestAnimationFrame.

**77. What upload retry algorithm is used?**

The browser makes three attempts and waits 1, 2, then 3 seconds. Despite a comment calling it exponential, the current implementation is linear backoff.

**78. How is XSS reduced?**

Dynamic user-facing text generally uses textContent, and HTML-producing paths use escaping. Uploaded content is not intentionally rendered as raw HTML.

**79. What accessibility support exists?**

There are semantic labels, native form controls, an aria-live word count, modal roles, and hidden decorative icons. A full keyboard/focus and screen-reader audit is still needed.

### Security

**80. What is your threat model?**

The main threats are unauthorized access to uploaded text/audio, path traversal, malicious files, decompression bombs, resource exhaustion, forged sessions, cache deletion races, proxy-header spoofing, and exposed deployment secrets.

**81. How are browser sessions protected?**

A random session ID is signed with HMAC-SHA256. The cookie is HTTP-only, SameSite Lax, and optionally Secure. Tampered or malformed cookies are replaced.

**82. Is this full authentication?**

No. It is anonymous browser-session isolation, not user identity. Clearing cookies loses access, and there is no account recovery or authorization hierarchy.

**83. What happens if SESSION_SECRET is not configured?**

A random secret is generated at startup, so existing cookies stop working after restart. HTTPS mode requires a stable secret of at least 32 characters.

**84. How is path traversal prevented?**

Cache IDs must be exactly 32 lowercase hexadecimal characters. Uploaded filenames are reduced to safe basenames and checked for invalid characters and supported extensions.

**85. How are spoofed client IPs prevented?**

X-Forwarded-For is ignored unless TRUST_PROXY_HEADERS is enabled. Deployment guidance requires a trusted reverse proxy that overwrites the header.

**86. How does rate limiting work?**

Redis sorted sets represent sliding windows. A Lua script cleans old entries, checks both limits, and inserts only if every window allows the request.

**87. What resource-exhaustion controls exist?**

Request rate limits, file-size limits, extracted-text limits, EPUB expansion limits, VieNeu word limits, TTS character limits, queue caps, local semaphores, deadlines, retention cleanup, and Docker CPU/memory/PID limits.

**88. Is the global cache-clear endpoint secure?**

It is disabled by default and returns 404. If enabled, it has no separate admin authentication, so it should remain disabled except in tightly controlled maintenance conditions.

**89. Is CSRF fully addressed?**

Not explicitly. SameSite cookies and same-origin browser behavior reduce exposure, but an authenticated multi-user version should add CSRF protection for state-changing cookie-authenticated endpoints.

**90. Is audio encrypted at rest?**

No. It is protected by application ownership checks and host filesystem permissions, not encryption. Sensitive multi-tenant deployment would need stronger storage controls.

### Testing and CI/CD

**91. What does the test suite cover?**

Models, uploads, checksum and file validation, EPUB bounds, extraction heuristics, job persistence/recovery/cancellation, rate-limit atomicity, queue caps, session isolation, cache-owner races, concurrency limits, deadlines, health checks, and VieNeu configuration.

**92. What test result can you quote?**

> The current suite has 81 passing tests and two skipped tests. Python compilation, JavaScript syntax, and Docker Compose validation also pass.

**93. Why are two tests skipped?**

They require real sample PDF and EPUB files. This is a known gap; current extractor tests mainly exercise helpers and mocked paths.

**94. What important tests are missing?**

- Real PDF/EPUB fixture integration tests.
- Real Redis queue/worker integration.
- Real Edge/gTTS/VieNeu synthesis tests in an isolated environment.
- Browser end-to-end tests for MSE and SSE.
- Crash tests spanning multiple processes.
- Load and memory tests.

**95. Why mock the model in unit tests?**

Real model downloads are large, slow, hardware-dependent, and unsuitable for every CI run. A separate optional integration suite should test the actual model.

**96. What warnings exist today?**

PyPDF2 is deprecated in favor of pypdf; Pydantic min_items should become min_length; FastAPI startup/shutdown decorators should migrate to lifespan handlers.

**97. Explain the Dockerfile.**

It uses a builder stage to resolve locked dependencies, copies only the virtual environment into a slim runtime, installs FFmpeg, creates runtime directories, and runs as a non-root user.

**98. Explain Docker Compose.**

It runs Redis, the FastAPI app, and an optional VieNeu worker profile. Named volumes share audio and model state, while CPU, memory, and PID limits bound each service.

**99. How does CI/CD work?**

Pull requests run tests, syntax checks, Compose validation, and an image build. A successful push to main deploys the exact tested commit over SSH using a detached checkout and docker compose up --wait.

**100. Is deployment zero-downtime?**

No. Compose rebuilds and replaces services on one server. Zero-downtime deployment would require multiple instances, readiness-based traffic switching, and external shared state.

**101. How would you roll back?**

Check out a previously verified commit on the server and rebuild the Compose stack. Persistent volumes survive the application rollback, subject to data-format compatibility.

**102. What observability is available?**

Structured logging is limited, health endpoints exist, and job metadata exposes progress. There are no Prometheus metrics, distributed traces, alerting, or dashboards yet.

## Questions designed to catch bluffing

Answer these exactly and honestly:

- **Is it horizontally scalable?** Not as-is.
- **Is the Redis queue exactly once?** No.
- **Is lossless output functional end to end?** No; it is currently downgraded.
- **Does needs_ocr mean OCR happens?** No.
- **Are local Edge/gTTS jobs durable?** No.
- **Are in-progress uploads recoverable after restart?** No.
- **Is MD5 an authorization/security mechanism?** No.
- **Is extraction timeout guaranteed to interrupt a stuck parser?** No.
- **Are upload retries exponential?** No; the waits are linear.
- **Does /document/health deeply verify storage and workers?** No.
- **Are real documents and models tested in normal CI?** No.
- **Is every module actively used?** No; vietnamese_text_processor.py and several audio helpers appear currently unwired.

## Strong “what would you improve?” answer

> First, I would fix correctness gaps before adding infrastructure: remove or complete the WAV option, add real PDF/EPUB fixtures, migrate FastAPI lifecycle hooks, strengthen the chapter request schema, and recover or fail stale local TTS jobs after restart.
>
> If usage then justified horizontal scaling, I would move job/document metadata to Postgres or Redis, audio to object storage, make queue admission atomic, and run all providers through durable workers. I would add metrics for queue time, first-byte latency, generation duration, failures, and cache hits. I would not add Kubernetes or Kafka before the workload required them.

## Best STAR story from this repository

**Situation:** Identical TTS requests shared one cached artifact across browser sessions.

**Task:** Prevent one session from deleting or cancelling audio still used by another session, including races with worker metadata writes.

**Action:** Signed anonymous session cookies were added; every cache route checks ownership; owner mutation was placed under a cross-process file lock; worker status writes preserve authorization fields; and a closed state prevents a new attachment from racing final deletion.

**Result:** The regression tests prove strangers receive 404, one owner only unlinks itself, the final owner deletes, and stale worker writes cannot restore removed owners.

## Final preparation checklist

- Replace the contribution placeholder with your exact personal work.
- Practice the 60-second introduction until it sounds conversational.
- Be able to draw the architecture without reading this file.
- Explain one concurrency bug, one security fix, and one production limitation.
- Never claim exactly-once delivery, full authentication, OCR, working WAV output, or horizontal scaling.
- Quote the current test result accurately: 81 passed and 2 skipped.

The Ponytail approach influenced this review by separating current guarantees from speculative scaling work: the present single-server design is defensible, provided you state its limits clearly.
