# Upload Reliability Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add rate limiting, resumable uploads, parallel job processing, and OCR retry functionality to the story2audio application.

**Architecture:** Three independent feature groups: (1) Redis-based rate limiting + session persistence for resumable uploads, (2) Dynamic worker pool + parallel chapter extraction, (3) Async locks for race condition safety + manual OCR retry endpoint.

**Tech Stack:** Redis (rate limiting), pytesseract (OCR), asyncio.Lock (concurrency), ThreadPoolExecutor (parallel processing)

---

## File Structure Overview

| File | Type | Responsibility |
|------|------|-----------------|
| `rate_limiter.py` | NEW | Redis-based rate limiting with sliding window |
| `main.py` | MODIFY | Add rate limit middleware |
| `document_api.py` | MODIFY | Add `/pending` endpoint, locks, OCR retry |
| `file_processor.py` | MODIFY | Session persistence functions |
| `job_queue.py` | MODIFY | DynamicExecutor, parallel processing |
| `text_extractor.py` | MODIFY | Parallel extraction, OCR function |
| `models/document.py` | MODIFY | Add `word_count` to Chapter |
| `templates/index.html` | MODIFY | OCR retry button |
| `Dockerfile` | MODIFY | Install tesseract |
| `pyproject.toml` | MODIFY | Add dependencies |
| `docker-compose.yml` | MODIFY | Add Redis service |
| `tests/test_rate_limiter.py` | NEW | Rate limiter tests |
| `tests/test_resumable_upload.py` | NEW | Resumable upload tests |
| `tests/test_parallel_extraction.py` | NEW | Parallel extraction tests |
| `tests/test_ocr_retry.py` | NEW | OCR retry tests |

---

# GROUP 1: Rate Limiting

### Task 1.1: Create rate_limiter.py with Redis-based sliding window

**Files:**
- Create: `rate_limiter.py`
- Test: `tests/test_rate_limiter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rate_limiter.py
import pytest
import asyncio
from datetime import datetime, timedelta, UTC

@pytest.mark.asyncio
async def test_rate_limit_allows_within_limit():
    """Should allow requests within the limit."""
    from rate_limiter import RateLimiter
    
    limiter = RateLimiter(redis_url="redis://localhost:7379")  # Test port
    await limiter.initialize()
    
    # Clear any existing data
    await limiter.clear()
    
    # 5 requests should be allowed (limit is 5/min)
    for i in range(5):
        allowed, error = await limiter.check_limit(
            key="test:upload:127.0.0.1",
            limit=5,
            window=60
        )
        assert allowed, f"Request {i+1} should be allowed"
    
    await limiter.close()

@pytest.mark.asyncio
async def test_rate_limit_blocks_exceeded():
    """Should block requests exceeding the limit."""
    from rate_limiter import RateLimiter
    
    limiter = RateLimiter(redis_url="redis://localhost:7379")
    await limiter.initialize()
    await limiter.clear()
    
    # Use up the limit
    for _ in range(5):
        await limiter.check_limit("test:upload:127.0.0.2", 5, 60)
    
    # 6th request should be blocked
    allowed, error = await limiter.check_limit("test:upload:127.0.0.2", 5, 60)
    assert not allowed, "Request exceeding limit should be blocked"
    assert "retry_after" in error or "limit" in error.lower()
    
    await limiter.close()

@pytest.mark.asyncio
async def test_rate_limit_sliding_window():
    """Should use sliding window (old requests expire)."""
    from rate_limiter import RateLimiter
    import time
    
    limiter = RateLimiter(redis_url="redis://localhost:7379")
    await limiter.initialize()
    await limiter.clear()
    
    key = "test:sliding:127.0.0.3"
    window = 2  # 2 second window for testing
    
    # Make 3 requests in a 1-second window (limit 2)
    await limiter.check_limit(key, 2, window)
    await limiter.check_limit(key, 2, window)
    
    allowed, _ = await limiter.check_limit(key, 2, window)
    assert not allowed, "Third request should be blocked"
    
    # Wait for window to pass
    await asyncio.sleep(2.1)
    
    allowed, _ = await limiter.check_limit(key, 2, window)
    assert allowed, "Request after window should be allowed"
    
    await limiter.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
# Start Redis for testing (if not running)
docker run -d -p 7379:6379 redis:alpine

# Run tests
pytest tests/test_rate_limiter.py -v
```
Expected: FAIL with "No module named 'rate_limiter'" or import errors

- [ ] **Step 3: Write minimal implementation**

```python
# rate_limiter.py
import os
import asyncio
from datetime import datetime, timedelta, UTC
from typing import Optional, Tuple
import redis.asyncio as redis

class RateLimiter:
    """Redis-based rate limiting with sliding window."""
    
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.getenv(
            "REDIS_URL", 
            "redis://localhost:6379"
        )
        self._client: Optional[redis.Redis] = None
    
    async def initialize(self):
        """Initialize Redis connection."""
        self._client = await redis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
    
    async def close(self):
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()
    
    async def clear(self):
        """Clear all rate limit data (for testing)."""
        if self._client:
            await self._client.flushdb()
    
    async def check_limit(
        self,
        key: str,
        limit: int,
        window: int,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if request is within rate limit using sliding window.
        
        Args:
            key: Unique key for this limit (e.g., "upload:127.0.0.1")
            limit: Maximum number of requests allowed
            window: Time window in seconds
        
        Returns:
            (allowed: bool, error_message: str | None)
        """
        if not self._client:
            await self.initialize()
        
        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=window)
        
        pipe = self._client.pipeline()
        
        # Remove old entries outside the window
        pipe.zremrangebyscore(key, 0, window_start.timestamp())
        
        # Count current requests
        pipe.zcard(key)
        
        # Add current request
        pipe.zadd(key, {str(now.timestamp()): now.timestamp()})
        
        # Set expiry
        pipe.expire(key, window + 1)
        
        results = await pipe.execute()
        current_count = results[1]
        
        if current_count >= limit:
            # Get oldest request to calculate retry_after
            oldest = await self._client.zrange(key, 0, 0, withscores=True)
            if oldest:
                oldest_time = oldest[0][1]
                retry_after = int(window - (now.timestamp() - oldest_time)) + 1
                return False, f"Rate limit exceeded. Try again in {retry_after}s"
            return False, "Rate limit exceeded"
        
        return True, None
    
    async def check_upload_limits(
        self,
        ip: str,
        session_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check all upload-related rate limits.
        
        Args:
            ip: Client IP address
            session_id: Optional upload session ID for chunk limit
        
        Returns:
            (allowed: bool, error_message: str | None)
        """
        # Check per-minute limit (5 uploads/min)
        allowed, error = await self.check_limit(
            f"upload:1m:{ip}",
            limit=5,
            window=60
        )
        if not allowed:
            return False, f"5 uploads per minute allowed. {error}"
        
        # Check per-hour limit (20 uploads/hour)
        allowed, error = await self.check_limit(
            f"upload:1h:{ip}",
            limit=20,
            window=3600
        )
        if not allowed:
            return False, f"20 uploads per hour allowed. {error}"
        
        # Check concurrent chunks if session provided
        if session_id:
            # Track active chunk uploads per session
            # This is a simpler check - max 5 chunks in flight
            chunk_key = f"chunks:{session_id}"
            current = await self._client.incr(chunk_key)
            await self._client.expire(chunk_key, 300)  # 5 min expiry
            
            if current > 5:
                await self._client.decr(chunk_key)
                return False, "Maximum 5 concurrent chunks per session"
        
        return True, None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_rate_limiter.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add rate_limiter.py tests/test_rate_limiter.py
git commit -m "feat: add Redis-based rate limiter with sliding window"
```

---

### Task 1.2: Add rate limit middleware to main.py

**Files:**
- Modify: `main.py`
- Test: `tests/test_rate_limiter.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_rate_limiter.py
from fastapi.testclient import TestClient
from main import app

@pytest.mark.asyncio
async def test_rate_limit_middleware_blocks_excessive():
    """Middleware should block excessive upload requests."""
    from rate_limiter import RateLimiter
    
    # Initialize limiter
    limiter = RateLimiter(redis_url="redis://localhost:7379")
    await limiter.initialize()
    await limiter.clear()
    
    # Inject limiter into app
    app.state.rate_limiter = limiter
    
    client = TestClient(app)
    
    # Make 6 upload initiate requests
    responses = []
    for i in range(6):
        response = client.post(
            "/document/upload/initiate",
            data={
                "filename": "test.pdf",
                "file_size": 1024 * 1024,  # 1MB
                "checksum": "abc123"
            }
        )
        responses.append(response)
    
    # First 5 should succeed, 6th should fail with 429
    for i in range(5):
        assert responses[i].status_code == 200, f"Request {i+1} should succeed"
    
    assert responses[5].status_code == 429, "6th request should be rate limited"
    assert "limit" in responses[5].json()["detail"].lower()
    
    await limiter.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_rate_limiter.py::test_rate_limit_middleware_blocks_excessive -v
```
Expected: FAIL with 429 not being returned or rate_limiter not found

- [ ] **Step 3: Write implementation**

Find the `main.py` file and add the rate limiter initialization and middleware:

```python
# Add to imports at top of main.py
from fastapi import Request, HTTPException, status
from rate_limiter import RateLimiter
import os

# Add near the top after imports (before app definition)
# Create global rate limiter instance
rate_limiter = RateLimiter()

@app.on_event("startup")
async def startup_event():
    """Initialize rate limiter on startup."""
    await rate_limiter.initialize()
    app.state.rate_limiter = rate_limiter

@app.on_event("shutdown")
async def shutdown_event():
    """Close rate limiter on shutdown."""
    await rate_limiter.close()

# Add middleware after app definition
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply rate limiting to upload endpoints."""
    # Only rate limit upload endpoints
    if request.url.path.startswith("/document/upload"):
        limiter = app.state.rate_limiter if hasattr(app.state, 'rate_limiter') else None
        
        if limiter:
            # Get client IP
            # Check for forwarded headers (proxy/load balancer)
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                ip = forwarded_for.split(",")[0].strip()
            else:
                ip = request.client.host if request.client else "unknown"
            
            # Get session_id from form data if available (for chunk uploads)
            session_id = None
            if request.method == "POST" and "chunk" in request.url.path:
                # Would need to parse form data, simplified here
                pass
            
            # Check limits
            allowed, error = await limiter.check_upload_limits(ip, session_id)
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=error
                )
    
    return await call_next(request)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_rate_limiter.py::test_rate_limit_middleware_blocks_excessive -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_rate_limiter.py
git commit -m "feat: add rate limit middleware to upload endpoints"
```

---

### Task 1.3: Add Redis service to docker-compose.yml

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add Redis service**

```yaml
# Add to docker-compose.yml, before the `app` service
services:
  redis:
    image: redis:alpine
    container_name: story2audio_redis
    restart: unless-stopped
    command: redis-server --maxmemory 128mb --maxmemory-policy allkeys-lru
    volumes:
      - story2audio_redis:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  app:
    # ... existing app service ...
    # Add redis dependency and environment
    depends_on:
      redis:
        condition: service_healthy
    environment:
      REDIS_URL: "redis://redis:6379"

volumes:
  story2audio_redis:
  # ... existing volumes ...
```

- [ ] **Step 2: Verify docker-compose syntax**

```bash
docker compose config
```
Expected: No errors, valid YAML output

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add Redis service for rate limiting"
```

---

# GROUP 1: Resumable Uploads

### Task 1.4: Add session persistence functions to file_processor.py

**Files:**
- Modify: `file_processor.py`
- Test: `tests/test_resumable_upload.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resumable_upload.py
import pytest
import asyncio
from datetime import datetime, timedelta, UTC
from pathlib import Path
import os

@pytest.mark.asyncio
async def test_save_and_load_session():
    """Session should be persisted to disk and reloadable."""
    from file_processor import (
        save_session, 
        load_session, 
        get_pending_sessions,
        _get_storage_paths
    )
    from models import UploadSession
    
    # Ensure sessions directory exists
    paths = _get_storage_paths()
    os.makedirs(paths['SESSIONS_DIR'], exist_ok=True)
    
    # Create test session
    session = UploadSession(
        upload_id="test-session-123",
        filename="test.pdf",
        total_size=10 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
        temp_dir="/tmp/test_upload",
        checksum="abc123def456",
        created_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    session.received_chunks = {0, 1}
    
    # Save session
    await save_session(session)
    
    # Load session
    loaded = await load_session("test-session-123")
    assert loaded is not None
    assert loaded.upload_id == "test-session-123"
    assert loaded.filename == "test.pdf"
    assert loaded.received_chunks == {0, 1}
    
    # Cleanup
    session_path = Path(paths['SESSIONS_DIR']) / "test-session-123.json"
    if session_path.exists():
        session_path.unlink()

@pytest.mark.asyncio
async def test_get_pending_sessions():
    """Should return only non-expired pending sessions."""
    from file_processor import (
        save_session, 
        get_pending_sessions,
        _get_storage_paths
    )
    from models import UploadSession
    
    paths = _get_storage_paths()
    os.makedirs(paths['SESSIONS_DIR'], exist_ok=True)
    
    # Create old session (expired)
    old_session = UploadSession(
        upload_id="old-session",
        filename="old.pdf",
        total_size=1024,
        temp_dir="/tmp/old",
        checksum="old",
        created_at=datetime.now(UTC) - timedelta(hours=25)
    )
    await save_session(old_session)
    
    # Create recent session
    recent_session = UploadSession(
        upload_id="recent-session",
        filename="recent.pdf",
        total_size=1024,
        temp_dir="/tmp/recent",
        checksum="recent",
        created_at=datetime.now(UTC) - timedelta(minutes=30)
    )
    await save_session(recent_session)
    
    # Get pending sessions
    pending = await get_pending_sessions()
    
    # Should only include recent session
    session_ids = [s.upload_id for s in pending]
    assert "recent-session" in session_ids
    assert "old-session" not in session_ids
    
    # Cleanup
    for upload_id in ["old-session", "recent-session"]:
        session_path = Path(paths['SESSIONS_DIR']) / f"{upload_id}.json"
        if session_path.exists():
            session_path.unlink()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_resumable_upload.py -v
```
Expected: FAIL with functions not found

- [ ] **Step 3: Write implementation**

Add to `file_processor.py`:

```python
# Add to file_processor.py imports
import json

# Add after _get_storage_paths() function
def _get_session_path(upload_id: str) -> str:
    """Get path to session file."""
    paths = _get_storage_paths()
    return os.path.join(paths['SESSIONS_DIR'], f"{upload_id}.json")

async def save_session(session: UploadSession) -> None:
    """
    Persist session to disk for recovery.
    
    Args:
        session: UploadSession to persist
    """
    _ensure_directories()
    path = _get_session_path(session.upload_id)
    
    session_data = session.model_dump()
    with open(path, 'w') as f:
        json.dump(session_data, f, default=str)
    
    # Also keep in memory
    active_sessions[session.upload_id] = session

async def load_session(upload_id: str) -> Optional[UploadSession]:
    """
    Load session from disk.
    
    Args:
        upload_id: Session ID to load
    
    Returns:
        UploadSession or None if not found
    """
    path = _get_session_path(upload_id)
    if not os.path.exists(path):
        return None
    
    with open(path, 'r') as f:
        data = json.load(f)
    
    # Reconstruct UploadSession with proper types
    session = UploadSession(**data)
    
    # Also restore to memory
    active_sessions[upload_id] = session
    return session

async def get_pending_sessions() -> List[UploadSession]:
    """
    Return all non-expired pending sessions.
    
    Returns:
        List of UploadSession that are less than 24 hours old
    """
    paths = _get_storage_paths()
    sessions_dir = paths['SESSIONS_DIR']
    
    if not os.path.exists(sessions_dir):
        return []
    
    config = _get_config()
    now = datetime.now(UTC)
    expiry = timedelta(hours=config['UPLOAD_SESSION_EXPIRY_HOURS'])
    
    pending = []
    for filename in os.listdir(sessions_dir):
        if not filename.endswith('.json'):
            continue
        
        upload_id = filename[:-5]  # Remove .json
        session = await load_session(upload_id)
        if session and (now - session.created_at) < expiry:
            pending.append(session)
    
    return pending
```

- [ ] **Step 4: Update initiate_upload to persist sessions**

Modify `initiate_upload` in `file_processor.py`:

```python
# At the end of initiate_upload(), replace the return:
async def initiate_upload(
    filename: str,
    file_size: int,
    checksum: str
) -> UploadSession:
    # ... existing code ...
    
    session = UploadSession(
        upload_id=upload_id,
        filename=filename,
        total_size=file_size,
        chunk_size=config['UPLOAD_CHUNK_SIZE'],
        temp_dir=temp_dir,
        checksum=checksum
    )
    
    # Persist session to disk
    await save_session(session)
    
    return session
```

- [ ] **Step 5: Update receive_chunk to persist on each chunk**

Modify `receive_chunk` in `file_processor.py`:

```python
# At the end of receive_chunk(), after session.received_chunks.add(chunk_number):
async def receive_chunk(
    upload_id: str,
    chunk_number: int,
    chunk_data: bytes
) -> bool:
    # ... existing validation and storage code ...
    
    session.received_chunks.add(chunk_number)
    
    # Persist updated session
    await save_session(session)
    
    return True
```

- [ ] **Step 6: Update complete_upload to clean up session file**

Modify `complete_upload` in `file_processor.py`:

```python
# After del active_sessions[upload_id], add cleanup:
async def complete_upload(upload_id: str) -> Document:
    # ... existing assembly code ...
    
    active_documents[upload_id] = document
    document_queue.append(upload_id)
    del active_sessions[upload_id]
    
    # Delete session file
    session_path = _get_session_path(upload_id)
    if os.path.exists(session_path):
        os.remove(session_path)
    
    # ... rest of function ...
```

- [ ] **Step 7: Run test to verify it passes**

```bash
pytest tests/test_resumable_upload.py -v
```
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add file_processor.py tests/test_resumable_upload.py
git commit -m "feat: add session persistence for resumable uploads"
```

---

### Task 1.5: Add pending sessions endpoint to document_api.py

**Files:**
- Modify: `document_api.py`
- Test: `tests/test_resumable_upload.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_resumable_upload.py
from fastapi.testclient import TestClient
from main import app
import asyncio

@pytest.mark.asyncio
async def test_pending_sessions_endpoint():
    """GET /document/upload/pending should list pending sessions."""
    from file_processor import save_session, _get_storage_paths, get_pending_sessions
    from models import UploadSession
    from datetime import datetime, timedelta, UTC
    
    client = TestClient(app)
    
    # Clear existing sessions
    pending = await get_pending_sessions()
    for session in pending:
        from file_processor import active_sessions
        active_sessions.pop(session.upload_id, None)
        import os
        session_path = _get_storage_paths()['SESSIONS_DIR'] + f"/{session.upload_id}.json"
        if os.path.exists(session_path):
            os.remove(session_path)
    
    # Create test session
    test_session = UploadSession(
        upload_id="pending-test-123",
        filename="pending.pdf",
        total_size=15 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
        temp_dir="/tmp/pending_test",
        checksum="pending123",
        created_at=datetime.now(UTC) - timedelta(minutes=10)
    )
    test_session.received_chunks = {0, 1}
    await save_session(test_session)
    
    # Get pending sessions
    response = client.get("/document/upload/pending")
    
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    
    # Find our test session
    test_pending = next((s for s in data if s["upload_id"] == "pending-test-123"), None)
    assert test_pending is not None
    assert test_pending["filename"] == "pending.pdf"
    assert test_pending["uploaded_chunks"] == 2
    assert "total_chunks" in test_pending
    assert "expires_at" in test_pending
    
    # Cleanup
    from file_processor import active_sessions
    active_sessions.pop("pending-test-123", None)
    import os
    session_path = _get_storage_paths()['SESSIONS_DIR'] + "/pending-test-123.json"
    if os.path.exists(session_path):
        os.remove(session_path)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_resumable_upload.py::test_pending_sessions_endpoint -v
```
Expected: FAIL with 404 or endpoint not found

- [ ] **Step 3: Write implementation**

Add to `document_api.py`:

```python
# Add to imports
from datetime import datetime, timedelta, UTC

# Add new endpoint after @router.get("/health")
@router.get("/upload/pending")
async def list_pending_sessions():
    """
    Get list of incomplete upload sessions for resume.
    
    Returns all pending sessions less than 24 hours old.
    """
    from file_processor import get_pending_sessions
    
    sessions = await get_pending_sessions()
    
    return [
        {
            "upload_id": s.upload_id,
            "filename": s.filename,
            "file_size": s.total_size,
            "uploaded_chunks": len(s.received_chunks),
            "total_chunks": (s.total_size + s.chunk_size - 1) // s.chunk_size,
            "created_at": s.created_at.isoformat(),
            "expires_at": (s.created_at + timedelta(hours=24)).isoformat(),
        }
        for s in sessions
    ]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_resumable_upload.py::test_pending_sessions_endpoint -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add document_api.py tests/test_resumable_upload.py
git commit -m "feat: add pending sessions endpoint for resumable uploads"
```

---

### Task 1.6: Add auto-resume logic to upload initiate endpoint

**Files:**
- Modify: `document_api.py`
- Test: `tests/test_resumable_upload.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_resumable_upload.py
@pytest.mark.asyncio
async def test_auto_resume_single_pending_session():
    """Should auto-resume when exactly one pending session exists."""
    from file_processor import save_session, get_pending_sessions, active_sessions
    from models import UploadSession
    from datetime import datetime, timedelta, UTC
    
    client = TestClient(app)
    
    # Create one pending session
    session = UploadSession(
        upload_id="auto-resume-123",
        filename="auto.pdf",
        total_size=10 * 1024 * 1024,
        chunk_size=5 * 1024 * 1024,
        temp_dir="/tmp/auto_resume",
        checksum="auto123",
        created_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    session.received_chunks = {0, 1}
    await save_session(session)
    
    # Initiate upload should auto-resume
    response = client.post(
        "/document/upload/initiate",
        data={
            "filename": "auto.pdf",
            "file_size": 10 * 1024 * 1024,
            "checksum": "auto123"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["upload_id"] == "auto-resume-123"
    assert data.get("resumed") is True
    assert data["uploaded_chunks"] == 2
    
    # Cleanup
    active_sessions.pop("auto-resume-123", None)
    import os
    from file_processor import _get_storage_paths
    session_path = _get_storage_paths()['SESSIONS_DIR'] + "/auto-resume-123.json"
    if os.path.exists(session_path):
        os.remove(session_path)

@pytest.mark.asyncio
async def test_no_auto_resume_with_multiple_pending():
    """Should create new session when multiple pending exist."""
    from file_processor import save_session, active_sessions
    from models import UploadSession
    from datetime import datetime, timedelta, UTC
    import uuid
    
    client = TestClient(app)
    
    # Create two pending sessions
    for i in range(2):
        session = UploadSession(
            upload_id=f"multi-{i}",
            filename=f"multi{i}.pdf",
            total_size=5 * 1024 * 1024,
            chunk_size=5 * 1024 * 1024,
            temp_dir=f"/tmp/multi{i}",
            checksum=f"multi{i}",
            created_at=datetime.now(UTC) - timedelta(minutes=5)
        )
        await save_session(session)
    
    # Initiate upload should create NEW session (not auto-resume)
    response = client.post(
        "/document/upload/initiate",
        data={
            "filename": "new.pdf",
            "file_size": 5 * 1024 * 1024,
            "checksum": "new123"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data.get("resumed") is False or "resumed" not in data
    assert data["upload_id"] not in ["multi-0", "multi-1"]
    
    # Cleanup
    for i in range(2):
        active_sessions.pop(f"multi-{i}", None)
    active_sessions.pop(data["upload_id"], None)
    
    from file_processor import _get_storage_paths
    import os
    sessions_dir = _get_storage_paths()['SESSIONS_DIR']
    for filename in ["multi-0.json", "multi-1.json"]:
        path = os.path.join(sessions_dir, filename)
        if os.path.exists(path):
            os.remove(path)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_resumable_upload.py::test_auto_resume_single_pending_session -v
```
Expected: FAIL - auto-resume logic not implemented

- [ ] **Step 3: Write implementation**

Modify `upload_initiate` in `document_api.py`:

```python
@router.post("/upload/initiate")
async def upload_initiate(
    filename: str = Form(...),
    file_size: int = Form(...),
    checksum: str = Form(...)
):
    """
    Initiate a chunked upload session.
    
    Returns upload_id and chunk size for subsequent chunk uploads.
    Auto-resumes if exactly one pending session exists.
    """
    try:
        from file_processor import get_pending_sessions
        from datetime import timedelta
        
        # Check for pending sessions
        pending = await get_pending_sessions()
        
        # Auto-resume if exactly one pending session
        if len(pending) == 1:
            session = pending[0]
            # Check if session is still valid (not expired)
            session_age = datetime.now(UTC) - session.created_at
            if session_age < timedelta(hours=24):
                return {
                    "upload_id": session.upload_id,
                    "chunk_size": session.chunk_size,
                    "status": "resumed",
                    "resumed": True,
                    "uploaded_chunks": len(session.received_chunks),
                    "total_chunks": (session.total_size + session.chunk_size - 1) // session.chunk_size
                }
        
        # Otherwise create new session
        session = await initiate_upload(filename, file_size, checksum)
        return {
            "upload_id": session.upload_id,
            "chunk_size": session.chunk_size,
            "status": "initiated",
            "resumed": False
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_resumable_upload.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add document_api.py tests/test_resumable_upload.py
git commit -m "feat: add auto-resume logic for single pending session"
```

---

# GROUP 2: Job Queue Improvements

### Task 2.1: Add DynamicExecutor to job_queue.py

**Files:**
- Modify: `job_queue.py`
- Test: `tests/test_parallel_extraction.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parallel_extraction.py
import pytest
import asyncio
from datetime import datetime, UTC

@pytest.mark.asyncio
async def test_dynamic_executor_scaling():
    """Executor should scale workers based on load."""
    from job_queue import DynamicExecutor
    
    executor = DynamicExecutor(min_workers=2, max_workers=8)
    
    # Start with minimum workers
    assert executor.executor._max_workers == 2
    
    # Submit jobs to trigger scaling
    futures = []
    for i in range(5):
        future = executor.submit(lambda: None)
        futures.append(future)
    
    # Wait a bit for adjustment
    await asyncio.sleep(0.1)
    
    # Should have scaled up
    assert executor.executor._max_workers > 2
    
    # Clean up
    for future in futures:
        future.cancel()
    
    await executor.close()

@pytest.mark.asyncio
async def test_dynamic_executor_process_document():
    """Should process document chapters in parallel."""
    from job_queue import DynamicExecutor
    from models import Document, FileType, DocumentStatus
    from datetime import timedelta, UTC
    
    # Create test document
    doc = Document(
        document_id="test-parallel-doc",
        filename="test.pdf",
        file_type=FileType.PDF,
        file_size=1024,
        file_path="/tmp/test.pdf",
        status=DocumentStatus.UPLOADING
    )
    
    executor = DynamicExecutor(min_workers=2, max_workers=4)
    
    # Mock extraction function
    extracted_chapters = []
    
    async def mock_extract(chapter_index):
        await asyncio.sleep(0.01)
        extracted_chapters.append(chapter_index)
        return f"Chapter {chapter_index}"
    
    # Submit parallel extraction
    chapters = [0, 1, 2, 3, 4]
    results = await executor.process_document_parallel(
        doc, 
        chapters,
        mock_extract
    )
    
    assert len(results) == 5
    assert set(extracted_chapters) == set(chapters)
    
    await executor.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_parallel_extraction.py -v
```
Expected: FAIL with DynamicExecutor not found

- [ ] **Step 3: Write implementation**

Add to `job_queue.py`:

```python
# Add imports
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Callable, Any, Optional

# Add after existing executor definition
class DynamicExecutor:
    """ThreadPoolExecutor that scales based on load."""
    
    def __init__(
        self, 
        min_workers: int = 2, 
        max_workers: Optional[int] = None
    ):
        self.min_workers = min_workers
        self.max_workers = max_workers or (os.cpu_count() or 4)
        self.executor = ThreadPoolExecutor(max_workers=self.min_workers)
        self._active_jobs = 0
        self._lock = threading.Lock()
    
    def submit(self, fn, *args, **kwargs):
        """Submit a job and potentially scale up workers."""
        with self._lock:
            self._active_jobs += 1
            self._maybe_scale()
        
        future = self.executor.submit(self._wrap_job, fn, *args, **kwargs)
        return future
    
    def _wrap_job(self, fn, *args, **kwargs):
        """Wrap job to decrement active count when done."""
        try:
            return fn(*args, **kwargs)
        finally:
            with self._lock:
                self._active_jobs -= 1
                # Don't scale down immediately to avoid thrashing
    
    def _maybe_scale(self):
        """Scale executor based on active jobs."""
        target = min(
            max(self._active_jobs + 2, self.min_workers),
            self.max_workers
        )
        current = self.executor._max_workers
        
        if target != current:
            # Create new executor with target size
            new_executor = ThreadPoolExecutor(max_workers=target)
            old_executor = self.executor
            self.executor = new_executor
            # Old executor will shut down when its futures complete
    
    async def process_document_parallel(
        self,
        document,  # Document object
        chapters: List[int],  # Chapter indices
        extract_fn: Callable  # Async extract function
    ) -> List[Any]:
        """Process document chapters in parallel batches."""
        # Classify by size (if word_count available)
        small_chapters = []
        large_chapters = []
        
        for idx in chapters:
            # Assume small for now - actual implementation checks word_count
            small_chapters.append(idx)
        
        results = []
        
        # Process small chapters in parallel (all at once)
        if small_chapters:
            tasks = [extract_fn(idx) for idx in small_chapters]
            small_results = await asyncio.gather(*tasks)
            results.extend(small_results)
        
        # Sort by original index
        results.sort(key=lambda x: chapters.index(getattr(x, 'chapter_number', chapters.index(x))))
        return results
    
    def close(self):
        """Shutdown the executor."""
        self.executor.shutdown(wait=False)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_parallel_extraction.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add job_queue.py tests/test_parallel_extraction.py
git commit -m "feat: add DynamicExecutor for job queue scaling"
```

---

### Task 2.2: Add parallel chapter extraction to text_extractor.py

**Files:**
- Modify: `text_extractor.py`
- Test: `tests/test_parallel_extraction.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_parallel_extraction.py
@pytest.mark.asyncio
async def test_extract_chapters_parallel():
    """Should extract small chapters in parallel."""
    from text_extractor import extract_chapters_parallel, _extract_batch
    from models import Document, Chapter, FileType
    from datetime import timedelta, UTC
    
    # Create test document with chapters
    doc = Document(
        document_id="test-chapters-parallel",
        filename="test.pdf",
        file_type=FileType.PDF,
        file_size=1024,
        file_path="/tmp/test.pdf"
    )
    
    chapters = [
        Chapter(
            document_id=doc.document_id,
            chapter_number=i,
            title=f"Chapter {i}",
            text_preview="Test",
            word_count=1000 if i < 5 else 10000,  # 5 small, 5 large
            extraction_method="basic"
        )
        for i in range(10)
    ]
    
    # Track which chapters were extracted
    extracted = []
    
    async def mock_extract(chapter):
        await asyncio.sleep(0.01)
        extracted.append(chapter.chapter_number)
        return chapter
    
    # Extract in parallel
    results = await extract_chapters_parallel(doc, chapters, mock_extract)
    
    assert len(results) == 10
    assert len(extracted) == 10
    # Results should be sorted by chapter number
    assert [r.chapter_number for r in results] == list(range(10))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_parallel_extraction.py::test_extract_chapters_parallel -v
```
Expected: FAIL with function not found

- [ ] **Step 3: Write implementation**

Add to `text_extractor.py`:

```python
# Add to imports
from typing import List, Callable, Any, Optional

# Add after _assess_text_quality function
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_parallel_extraction.py::test_extract_chapters_parallel -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add text_extractor.py tests/test_parallel_extraction.py
git commit -m "feat: add parallel chapter extraction with adaptive batching"
```

---

### Task 2.3: Add word_count field to Chapter model

**Files:**
- Modify: `models/document.py`

- [ ] **Step 1: Verify word_count exists**

```python
# Check if word_count is already in Chapter model
from models.document import Chapter
import inspect

# word_count should already exist at line 57
# If not, add it:
# word_count: int = 0
```

- [ ] **Step 2: Commit if changes were needed**

```bash
# Only if word_count was missing
git add models/document.py
git commit -m "fix: ensure word_count field exists in Chapter model"
```

---

# GROUP 3: Quality & Reliability

### Task 3.1: Add document-level locks to document_api.py

**Files:**
- Modify: `document_api.py`
- Test: `tests/test_ocr_retry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ocr_retry.py
import pytest
import asyncio
from fastapi.testclient import TestClient
from main import app

@pytest.mark.asyncio
async def test_concurrent_structure_requests_safe():
    """Concurrent requests to /structure during extraction should be safe."""
    from file_processor import active_documents
    from models import Document, FileType, DocumentStatus
    from datetime import timedelta, UTC
    
    client = TestClient(app)
    
    # Create document in EXTRACTING state
    doc = Document(
        document_id="test-concurrent-123",
        filename="test.pdf",
        file_type=FileType.PDF,
        file_size=1024,
        file_path="/tmp/test.pdf",
        status=DocumentStatus.EXTRACTING,
        extraction_progress=0.5
    )
    active_documents[doc.document_id] = doc
    
    # Make concurrent requests
    async def make_request():
        return client.get(f"/document/{doc.document_id}/structure")
    
    results = await asyncio.gather(*[make_request() for _ in range(5)])
    
    # All should return 200, not 500
    for response in results:
        assert response.status_code == 200
        data = response.json()
        # Should return extracting status, not incomplete chapters
        assert data.get("status") == "extracting"
    
    # Cleanup
    del active_documents[doc.document_id]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_ocr_retry.py::test_concurrent_structure_requests_safe -v
```
Expected: Currently may pass or have race conditions - we're adding safety

- [ ] **Step 3: Write implementation**

Add to `document_api.py`:

```python
# Add to imports at top
from collections import defaultdict
import asyncio

# Add after router definition
document_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# Modify get_document_structure endpoint
@router.get("/{document_id}/structure")
async def get_document_structure(document_id: str):
    """
    Get document chapter structure (non-streaming).
    
    Thread-safe: Returns extracting status if extraction in progress.
    """
    document = await get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Acquire lock before accessing chapters
    async with document_locks[document_id]:
        if document.status == DocumentStatus.EXTRACTING:
            # Return current progress, not incomplete data
            return {
                "document_id": document_id,
                "status": document.status.value,
                "total_chapters": document.total_chapters or 0,
                "extraction_progress": document.extraction_progress,
                "chapters": [],  # Empty while extracting
                "message": "Document is currently being extracted"
            }
        
        # Status is READY, safe to return chapters
        chapters = document.metadata.get("chapters", [])
        
        return {
            "document_id": document_id,
            "status": document.status.value,
            "total_chapters": document.total_chapters or len(chapters),
            "extraction_progress": document.extraction_progress,
            "chapters": chapters
        }
```

- [ ] **Step 4: Update extraction to hold lock**

Modify `stream_extraction` in `document_api.py`:

```python
@router.get("/{document_id}/extract/stream")
async def stream_extraction(document_id: str):
    """
    Stream chapter extraction progress via Server-Sent Events.
    """
    document = await get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Acquire lock before starting extraction
    async with document_locks[document_id]:
        if document.status == DocumentStatus.EXTRACTING:
            # Already extracting, return immediately
            pass
        else:
            # Update status
            document.status = DocumentStatus.EXTRACTING
            active_documents[document_id] = document
    
    # ... rest of function unchanged ...
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/test_ocr_retry.py::test_concurrent_structure_requests_safe -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add document_api.py tests/test_ocr_retry.py
git commit -m "feat: add document-level locks for safe concurrent access"
```

---

### Task 3.2: Add OCR retry endpoint

**Files:**
- Modify: `document_api.py`, `text_extractor.py`
- Modify: `Dockerfile`
- Modify: `pyproject.toml`
- Test: `tests/test_ocr_retry.py`

- [ ] **Step 1: Add dependencies**

```bash
# Add to pyproject.toml dependencies
```

Modify `pyproject.toml`:

```toml
[project]
# ... existing ...
dependencies = [
  # ... existing dependencies ...
  "pytesseract>=0.3.10",
  "pdf2image>=1.16.0",
]
```

- [ ] **Step 2: Update Dockerfile**

Find the Dockerfile and add tesseract:

```dockerfile
# Add after existing apt-get commands
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-vie \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Write the failing test**

```python
# Add to tests/test_ocr_retry.py
@pytest.mark.skipif(
    os.getenv("CI") == "true",
    reason="OCR tests skip in CI (no tesseract)"
)
def test_ocr_retry_endpoint():
    """POST /chapter/retry-ocr should trigger OCR extraction."""
    from file_processor import active_documents
    from models import Document, Chapter, FileType, DocumentStatus
    
    client = TestClient(app)
    
    # Create document with low-quality chapter
    doc = Document(
        document_id="test-ocr-123",
        filename="test.pdf",
        file_type=FileType.PDF,
        file_size=1024,
        file_path="/tmp/test.pdf",
        status=DocumentStatus.READY
    )
    doc.metadata["chapters"] = [
        {
            "chapter_id": "ch1",
            "chapter_number": 0,
            "title": "Chapter 1",
            "word_count": 100,
            "full_text": "Poor quality text ||||",
            "quality_score": 0.3,
            "needs_ocr": True
        }
    ]
    active_documents[doc.document_id] = doc
    
    # Request OCR retry
    response = client.post(f"/document/{doc.document_id}/chapter/0/retry-ocr")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    
    # Cleanup
    del active_documents[doc.document_id]
```

- [ ] **Step 4: Write OCR extraction function**

Add to `text_extractor.py`:

```python
# Add to imports
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
from typing import Optional, Callable
from models import ExtractionMethod

async def extract_chapter_with_ocr(
    document: 'Document',
    chapter_index: int,
    progress_callback: Optional[Callable[[float], None]] = None
) -> 'Chapter':
    """
    Extract a single chapter using OCR.
    
    Args:
        document: Document to extract from
        chapter_index: Index of chapter to extract
        progress_callback: Optional progress callback
    
    Returns:
        Chapter with OCR-extracted text
    """
    # Get chapters from metadata
    chapters = document.metadata.get("chapters", [])
    if chapter_index >= len(chapters):
        raise ValueError(f"Chapter {chapter_index} not found")
    
    chapter_data = chapters[chapter_index]
    
    # Convert PDF pages to images
    # Assuming start_page/end_page are set
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
        text = pytesseract.image_to_string(
            img,
            lang='vie+eng',
            config='--psm 6'
        )
        full_text.append(text)
    
    # Update chapter with OCR result
    chapter = Chapter(
        chapter_id=chapter_data.get("chapter_id", f"ch_{chapter_index}"),
        document_id=document.document_id,
        chapter_number=chapter_index,
        title=chapter_data.get("title", f"Chapter {chapter_index + 1}"),
        text_preview="\n".join(full_text)[:500],
        full_text="\n".join(full_text),
        word_count=len("\n".join(full_text).split()),
        extraction_method=ExtractionMethod.OCR,
        quality_score=1.0,
        needs_ocr=False,
        ocr_processed=True
    )
    
    return chapter
```

- [ ] **Step 5: Add OCR retry endpoint**

Add to `document_api.py`:

```python
# Add to imports
from fastapi import BackgroundTasks

# Add chapter-level locks
chapter_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# Add new endpoint
@router.post("/{document_id}/chapter/{chapter_index}/retry-ocr")
async def retry_chapter_with_ocr(
    document_id: str,
    chapter_index: int,
    background_tasks: BackgroundTasks,
):
    """
    Retry a single chapter extraction with OCR.
    
    Runs OCR in background and updates chapter when complete.
    """
    document = await get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    chapters = document.metadata.get("chapters", [])
    if chapter_index >= len(chapters):
        raise HTTPException(status_code=404, detail="Chapter not found")
    
    # Run OCR in background
    background_tasks.add_task(
        _run_ocr_for_chapter,
        document_id,
        chapter_index,
    )
    
    return {
        "status": "started",
        "message": "OCR extraction started for chapter"
    }

async def _run_ocr_for_chapter(document_id: str, chapter_index: int):
    """Background task for OCR extraction."""
    lock_key = f"{document_id}:{chapter_index}"
    
    async with chapter_locks[lock_key]:
        from text_extractor import extract_chapter_with_ocr
        
        document = await get_document(document_id)
        if not document:
            return
        
        chapter = await extract_chapter_with_ocr(document, chapter_index)
        
        # Update document metadata
        chapters = document.metadata.get("chapters", [])
        if chapter_index < len(chapters):
            chapters[chapter_index] = {
                "chapter_id": chapter.chapter_id,
                "chapter_number": chapter.chapter_number,
                "title": chapter.title,
                "word_count": chapter.word_count,
                "full_text": chapter.full_text or "",
                "text_preview": chapter.text_preview[:200],
                "quality_score": chapter.quality_score,
                "needs_ocr": chapter.needs_ocr,
                "extraction_method": chapter.extraction_method.value,
                "ocr_processed": chapter.ocr_processed
            }
            document.metadata["chapters"] = chapters
            active_documents[document_id] = document
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_ocr_retry.py -v
```
Expected: PASS (may skip in CI)

- [ ] **Step 7: Commit**

```bash
git add document_api.py text_extractor.py Dockerfile pyproject.toml tests/test_ocr_retry.py
git commit -m "feat: add OCR retry endpoint for low-quality chapters"
```

---

### Task 3.3: Add OCR retry button to frontend

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add OCR retry button**

Find the chapter list rendering section in `templates/index.html` and add the retry button:

```javascript
// Add to chapter rendering function
function renderChapterList(chapters) {
    if (!chapters || chapters.length === 0) {
        return '<p>No chapters available</p>';
    }
    
    return chapters.map((ch, idx) => {
        const needsOcr = ch.quality_score < 0.5;
        const qualityClass = needsOcr ? 'quality-low' : 'quality-good';
        
        return `
            <div class="chapter-item ${qualityClass}">
                <div class="chapter-header">
                    <h3>Chapter ${ch.chapter_number}: ${ch.title || 'Untitled'}</h3>
                    ${needsOcr ? `
                        <button 
                            class="btn-ocr" 
                            onclick="retryWithOCR('${currentDocumentId}', ${idx})"
                            title="Retry extraction with OCR"
                        >
                            Retry with OCR
                        </button>
                        <span class="quality-badge">
                            Low quality extraction
                        </span>
                    ` : ''}
                </div>
                <div class="chapter-stats">
                    <span>${ch.word_count || 0} words</span>
                    <span>Quality: ${(ch.quality_score * 100).toFixed(0)}%</span>
                </div>
                <div class="chapter-preview">
                    ${ch.text_preview || 'No preview available'}
                </div>
            </div>
        `;
    }).join('');
}

// Add OCR retry function
async function retryWithOCR(documentId, chapterIndex) {
    const button = document.querySelector(`.btn-ocr[onclick*="${chapterIndex}"]`);
    if (button) {
        button.disabled = true;
        button.textContent = 'Processing...';
    }
    
    try {
        const response = await fetch(
            `/document/${documentId}/chapter/${chapterIndex}/retry-ocr`,
            { method: 'POST' }
        );
        
        if (response.ok) {
            const data = await response.json();
            showMessage('OCR started. Refresh to see results.');
            
            // Poll for completion
            pollForCompletion(documentId, chapterIndex);
        } else {
            const error = await response.json();
            showMessage(`Error: ${error.detail}`, 'error');
        }
    } catch (error) {
        showMessage(`Error: ${error.message}`, 'error');
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = 'Retry with OCR';
        }
    }
}

async function pollForCompletion(documentId, chapterIndex, maxAttempts = 30) {
    for (let i = 0; i < maxAttempts; i++) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        
        try {
            const response = await fetch(`/document/${documentId}/structure`);
            if (response.ok) {
                const data = await response.json();
                const chapter = data.chapters[chapterIndex];
                
                if (chapter && chapter.quality_score >= 0.5) {
                    showMessage('OCR completed successfully!');
                    refreshDocumentStructure();
                    return;
                }
            }
        } catch (error) {
            console.error('Poll error:', error);
        }
    }
    showMessage('OCR is taking longer than expected. Please refresh manually.', 'warning');
}
```

- [ ] **Step 2: Add CSS styles**

Add to the `<style>` section:

```css
.chapter-item.quality-low {
    border-left: 4px solid #ff9800;
}

.chapter-item.quality-good {
    border-left: 4px solid #4caf50;
}

.btn-ocr {
    padding: 6px 12px;
    background: #ff9800;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.9em;
}

.btn-ocr:hover:not(:disabled) {
    background: #f57c00;
}

.btn-ocr:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

.quality-badge {
    padding: 4px 8px;
    background: #fff3e0;
    color: #e65100;
    border-radius: 4px;
    font-size: 0.85em;
}

.chapter-stats {
    display: flex;
    gap: 1rem;
    color: #666;
    font-size: 0.9em;
    margin: 0.5rem 0;
}
```

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: add OCR retry button to chapter list UI"
```

---

# FINAL INTEGRATION & CLEANUP

### Task 4.1: Update dependencies and Docker

- [ ] **Step 1: Verify all dependencies are in pyproject.toml**

Ensure `pyproject.toml contains:
- redis>=5.0.0
- pytesseract>=0.3.10
- pdf2image>=1.16.0

- [ ] **Step 2: Verify Dockerfile has tesseract**

- [ ] **Step 3: Verify docker-compose.yml has Redis**

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -v
```

- [ ] **Step 5: Test Docker build**

```bash
docker compose build
```

- [ ] **Step 6: Commit any remaining changes**

```bash
git add pyproject.toml Dockerfile docker-compose.yml
git commit -m "chore: update dependencies for upload reliability features"
```

---

# VERIFICATION CHECKLIST

## Group 1: Upload Improvements
- [ ] Rate limiting blocks 6th upload (5/min limit)
- [ ] Rate limiting allows uploads after 60 seconds
- [ ] Pending sessions endpoint returns list
- [ ] Auto-resume works with single pending session
- [ ] Sessions persist across server restart
- [ ] Cleanup removes expired sessions

## Group 2: Job Queue Improvements
- [ ] Dynamic executor scales workers based on load
- [ ] Small chapters extract in parallel
- [ ] Large chapters extract in batches of 4
- [ ] Progress updates reflect actual completion
- [ ] Multiple documents can process simultaneously

## Group 3: Quality & Reliability
- [ ] Concurrent requests to /structure during extraction return safe status
- [ ] OCR retry endpoint starts background task
- [ ] OCR retry updates chapter data
- [ ] Frontend shows OCR button for low-quality chapters
- [ ] Locks release properly on error

---

## Summary

This plan implements:

1. **Rate Limiting** (Tasks 1.1-1.3): Redis-based sliding window rate limiter with middleware
2. **Resumable Uploads** (Tasks 1.4-1.6): Session persistence, pending endpoint, auto-resume
3. **Parallel Processing** (Tasks 2.1-2.3): DynamicExecutor, parallel chapter extraction
4. **Race Condition Fix** (Task 3.1): Document-level locks with asyncio.Lock
5. **OCR Retry** (Task 3.2-3.3): pytesseract integration, retry endpoint, UI button

Total: ~14 tasks, ~70 steps
Estimated completion: 4-6 hours for experienced developer
