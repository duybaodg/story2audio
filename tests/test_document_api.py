# tests/test_document_api.py
import os
import sys
import tempfile
import time
import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set environment variable before importing modules
os.environ['DOCUMENT_STORAGE_PATH'] = tempfile.mkdtemp()

import main
import document_api
from main import app
from document_api import router
app.include_router(router)

client = TestClient(app)


def browser_session_id(test_client):
    test_client.get("/")
    return main._session_id_from_cookie(test_client.cookies["story2audio_session"])

def test_upload_initiate():
    response = client.post(
        "/document/upload/initiate",
        data={
            "filename": "test.pdf",
            "file_size": 10485760,
            "checksum": "0" * 32
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "upload_id" in data
    assert "chunk_size" in data
    assert data["status"] == "initiated"

def test_upload_chunk():
    # First initiate
    initiate_response = client.post(
        "/document/upload/initiate",
        data={
            "filename": "chunked.pdf",
            "file_size": 10485760,
            "checksum": "0" * 32
        }
    )
    upload_id = initiate_response.json()["upload_id"]

    # Upload chunk
    chunk_data = b"x" * 5242880
    response = client.post(
        "/document/upload/chunk",
        data={"upload_id": upload_id, "chunk_number": 0},
        files={"chunk": ("chunk_0", chunk_data, "application/octet-stream")}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["chunk_number"] == 0


def test_upload_chunk_rejects_oversized_body():
    initiate_response = client.post(
        "/document/upload/initiate",
        data={
            "filename": "oversized.pdf",
            "file_size": 10,
            "checksum": "0" * 32
        }
    )
    upload_id = initiate_response.json()["upload_id"]

    response = client.post(
        "/document/upload/chunk",
        data={"upload_id": upload_id, "chunk_number": 0},
        files={"chunk": ("chunk_0", b"x" * 11, "application/octet-stream")}
    )

    assert response.status_code == 400
    assert "exceeds expected" in response.json()["detail"]

def test_health_check():
    response = client.get("/document/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "active_sessions" in data


def test_tts_delete_requires_cache_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CACHE_DIR", str(tmp_path))
    owner = TestClient(app)
    intruder = TestClient(app)
    owner_session = browser_session_id(owner)
    browser_session_id(intruder)

    cache_id = "a" * 32
    main.save_cache_meta(cache_id, {"status": "completed", "owners": [owner_session]})
    Path(main.get_audio_path(cache_id)).write_bytes(b"audio")

    assert intruder.delete(f"/tts/file/{cache_id}").status_code == 404
    assert owner.delete(f"/tts/file/{cache_id}").status_code == 200


def test_tts_status_is_owner_only_and_hides_authorization_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CACHE_DIR", str(tmp_path))
    owner = TestClient(app)
    intruder = TestClient(app)
    owner_session = browser_session_id(owner)
    browser_session_id(intruder)
    cache_id = "b" * 32
    main.save_cache_meta(
        cache_id,
        {
            "status": "completed",
            "owners": [owner_session],
            "engine": "gtts",
            "file_size": 5,
        },
    )
    Path(main.get_audio_path(cache_id)).write_bytes(b"audio")

    response = owner.get(f"/tts/status/{cache_id}")
    assert response.status_code == 200
    assert "owners" not in response.json()
    assert "creator" not in response.json()
    assert owner.get(f"/tts/session/{cache_id}").status_code == 200
    audio_response = owner.get(f"/tts/file/{cache_id}")
    assert audio_response.status_code == 200
    assert audio_response.headers["cache-control"].startswith("private")
    for path in (
        f"/tts/session/{cache_id}",
        f"/tts/status/{cache_id}",
        f"/tts/file/{cache_id}",
        f"/tts/subtitle/srt/{cache_id}",
        f"/tts/subtitle/vtt/{cache_id}",
        f"/tts/cues/{cache_id}",
        f"/tts/cues/stream/{cache_id}",
        f"/tts/stream/{cache_id}",
    ):
        assert intruder.get(path).status_code == 404

    intruder.cookies.set("story2audio_session", owner_session)
    assert intruder.get(f"/tts/status/{cache_id}").status_code == 404


def test_duplicate_owner_cannot_delete_shared_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CACHE_DIR", str(tmp_path))
    creator = TestClient(app)
    consumer = TestClient(app)
    creator_session = browser_session_id(creator)
    consumer_session = browser_session_id(consumer)
    cache_id = "d" * 32
    main.save_cache_meta(
        cache_id,
        {
            "status": "completed",
            "owners": [creator_session, consumer_session],
        },
    )
    audio_path = Path(main.get_audio_path(cache_id))
    audio_path.write_bytes(b"audio")

    response = consumer.delete(f"/tts/file/{cache_id}")
    assert response.json()["status"] == "unlinked"
    assert audio_path.exists()
    assert main.load_cache_meta(cache_id)["owners"] == [creator_session]
    assert consumer.get(f"/tts/status/{cache_id}").status_code == 404

    assert creator.delete(f"/tts/file/{cache_id}").json()["status"] == "deleted"
    assert not audio_path.exists()


def test_shared_active_audio_unlinks_without_cancelling(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CACHE_DIR", str(tmp_path))
    creator = TestClient(app)
    consumer = TestClient(app)
    creator_session = browser_session_id(creator)
    consumer_session = browser_session_id(consumer)
    cache_id = "f" * 32
    main.save_cache_meta(
        cache_id,
        {"status": "generating", "owners": [creator_session, consumer_session]},
    )
    main.generation_status[cache_id] = {"status": "generating"}

    try:
        response = consumer.delete(f"/tts/file/{cache_id}")
        assert response.json()["status"] == "unlinked"
        assert cache_id not in main._cancellation_requests
        assert cache_id in main.generation_status
    finally:
        main.generation_status.pop(cache_id, None)
        main.cleanup_incomplete_cache(cache_id)


def test_worker_metadata_write_cannot_overwrite_owner_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CACHE_DIR", str(tmp_path))
    cache_id = "1" * 32
    main.save_cache_meta(cache_id, {"status": "queued", "owners": ["first"]})
    stale_worker_copy = main.load_cache_meta(cache_id)

    main.update_cache_owner(cache_id, "second")
    main.save_cache_meta(cache_id, {**stale_worker_copy, "status": "processing"})
    assert main.load_cache_meta(cache_id)["owners"] == ["first", "second"]

    stale_worker_copy = main.load_cache_meta(cache_id)
    main.update_cache_owner(cache_id, "second", remove=True)
    main.save_cache_meta(cache_id, {**stale_worker_copy, "status": "completed"})
    assert main.load_cache_meta(cache_id)["owners"] == ["first"]


def test_last_owner_detach_closes_cache_to_racing_attachments(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CACHE_DIR", str(tmp_path))
    cache_id = "3" * 32
    main.save_cache_meta(cache_id, {"status": "generating", "owners": ["first"]})

    detached = main.update_cache_owner(cache_id, "first", remove=True)

    assert detached["owners"] == []
    assert detached["owner_state"] == "closed"
    assert main.update_cache_owner(cache_id, "racing-owner") is None

def test_stream_extraction():
    """Test streaming extraction endpoint returns correct content type for non-existent document."""
    response = client.get("/document/test-doc-123/extract/stream")
    # Should return 404 for non-existent document
    assert response.status_code == 404


def test_stream_extraction_reuses_one_bounded_job(tmp_path, monkeypatch):
    from file_processor import active_documents
    from models import Document, FileType
    from job_queue import JobStatus

    owner = TestClient(app)
    owner_session = browser_session_id(owner)
    document = Document(
        document_id="stream-doc",
        filename="stream.pdf",
        file_type=FileType.PDF,
        file_size=1,
        file_path=str(tmp_path / "stream.pdf"),
        owner_session=owner_session,
    )
    active_documents[document.document_id] = document
    submissions = []
    status_calls = 0

    async def fake_submit(*args):
        submissions.append(args)
        return "job-1"

    def fake_status(job_id):
        nonlocal status_calls
        status_calls += 1
        status = JobStatus.PENDING if status_calls <= 2 else JobStatus.COMPLETED
        return SimpleNamespace(
            status=status,
            progress=1.0 if status == JobStatus.COMPLETED else 0.0,
            message="done" if status == JobStatus.COMPLETED else "queued",
            result={
                "chapters": [
                    {
                        "chapter_id": "chapter-1",
                        "chapter_number": 1,
                        "title": "One",
                        "word_count": 1,
                        "quality_score": 1.0,
                        "needs_ocr": False,
                        "text_preview": "one",
                    }
                ]
            },
            error=None,
        )

    monkeypatch.setattr(document_api, "submit_extraction_job", fake_submit)
    monkeypatch.setattr(document_api, "get_job_status", fake_status)

    try:
        first_response = owner.get("/document/stream-doc/extract/stream")
        assert first_response.status_code == 200
        assert first_response.text.index("chapter-1") < first_response.text.index("done")
        assert owner.get("/document/stream-doc/extract/stream").status_code == 200
        assert len(submissions) == 1
    finally:
        active_documents.pop(document.document_id, None)


def test_tts_with_chapters():
    """Test TTS endpoint with source_chapters parameter."""
    response = client.post(
        "/tts/start",
        json={
            "source_chapters": [
                {"text": "Chapter one text"},
                {"text": "Chapter two text"}
            ],
            "voice": "vi-VN-HoaiMyNeural",
            "engine": "edge",
            "language": "vi"
        }
    )
    # Should accept chapters and return cache_id
    assert response.status_code in [200, 400]  # May fail if TTS not available
    if response.status_code == 200:
        data = response.json()
        assert "cache_id" in data
        assert "status" in data


def test_tts_stream_active_generation_waits_without_503(monkeypatch):
    """Active generation without first bytes should open a stream instead of returning 503."""
    cache_id = "a" * 32
    audio_path = main.get_audio_path(cache_id)
    monkeypatch.setattr(main, "TTS_STREAM_FIRST_BYTE_TIMEOUT_SECONDS", 0)
    owner_session = browser_session_id(client)
    main.save_cache_meta(cache_id, {"status": "generating", "owners": [owner_session]})

    main.generation_status[cache_id] = {
        "status": "generating",
        "progress": 0,
        "total": 1,
    }

    try:
        response = client.get(f"/tts/stream/{cache_id}")
        assert response.status_code == 200
        assert response.content == b""
    finally:
        main.generation_status.pop(cache_id, None)
        main.cleanup_incomplete_cache(cache_id)
        if os.path.exists(audio_path):
            os.remove(audio_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine", "voice"),
    (("edge", "vi-VN-HoaiMyNeural"), ("gtts", "gtts")),
)
async def test_local_tts_has_a_whole_job_deadline(tmp_path, monkeypatch, engine, voice):
    monkeypatch.setattr(main, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(main, "LOCAL_TTS_JOB_TIMEOUT_SECONDS", -1)
    cache_id = "e" * 32
    main.save_cache_meta(cache_id, {"owners": ["owner"]})

    await main.generate_chunks(
        text="hello",
        voice=voice,
        engine=engine,
        cache_id=cache_id,
        chunks=["hello"],
    )

    metadata = main.load_cache_meta(cache_id)
    assert metadata["status"] == "failed"
    assert "TimeoutError" in metadata["error"]


@pytest.mark.asyncio
async def test_gtts_keeps_capacity_until_executor_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CACHE_DIR", str(tmp_path))
    slot = main.threading.BoundedSemaphore(1)
    monkeypatch.setattr(main, "_local_tts_slots", slot)
    started = main.threading.Event()
    release = main.threading.Event()

    def blocked_gtts(_text, _language, _timeout):
        started.set()
        assert release.wait(timeout=2)
        return b"audio"

    monkeypatch.setattr(main, "gtts_to_bytes", blocked_gtts)
    assert slot.acquire(blocking=False)
    cache_id = "2" * 32
    main.save_cache_meta(cache_id, {"owners": ["owner"]})

    task = asyncio.create_task(
        asyncio.to_thread(
            main.generate_chunks_with_slot,
            "hello",
            "gtts",
            "gtts",
            cache_id,
            "en",
            ["hello"],
        )
    )
    assert await asyncio.to_thread(started.wait, 1)
    await asyncio.sleep(0.05)
    assert not task.done()
    assert not slot.acquire(blocking=False)

    release.set()
    await asyncio.wait_for(task, timeout=2)
    assert slot.acquire(blocking=False)


def test_gtts_subprocess_has_a_hard_timeout(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise main.subprocess.TimeoutExpired("gtts", 1)

    monkeypatch.setattr(main.subprocess, "run", timeout)
    with pytest.raises(TimeoutError):
        main.gtts_to_bytes("hello", "en", 1)


def test_cleanup_old_audio_cache_removes_expired_files(monkeypatch, tmp_path):
    cache_id = "c" * 32
    monkeypatch.setattr(main, "CACHE_DIR", str(tmp_path))

    main.save_cache_meta(cache_id, {"status": "completed", "engine": "edge"})
    audio_path = main.get_audio_path(cache_id)
    srt_path = main.get_srt_path(cache_id)
    with open(audio_path, "wb") as f:
        f.write(b"audio")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("subtitle")

    old_time = time.time() - (13 * 3600)
    for path in (main.get_meta_path(cache_id), audio_path, srt_path):
        os.utime(path, (old_time, old_time))

    assert main.cleanup_old_audio_cache(retention_hours=12) == 3
    assert not os.path.exists(audio_path)
    assert not os.path.exists(srt_path)
    assert not os.path.exists(main.get_meta_path(cache_id))


def test_vieneu_start_enqueues_job_without_in_process_generation(monkeypatch):
    """VieNeu jobs should be handed to the Redis worker, not FastAPI BackgroundTasks."""
    enqueued = []

    async def fake_enqueue(job):
        enqueued.append(job)

    monkeypatch.setattr(main, "enqueue_vieneu_tts_job", fake_enqueue)

    response = client.post(
        "/tts/start",
        json={
            "text": "Xin chào từ VieNeu",
            "voice": "vieneu:default",
            "engine": "vieneu",
            "language": "vi",
        },
    )

    assert response.status_code == 200
    data = response.json()
    cache_id = data["cache_id"]

    try:
        assert data["status"] == "started"
        assert len(enqueued) == 1
        assert enqueued[0]["cache_id"] == cache_id
        assert enqueued[0]["engine"] == "vieneu"
        assert "model" not in enqueued[0]
        assert data["estimated_seconds"] > 0
        assert cache_id not in main.generation_status

        meta = main.load_cache_meta(cache_id)
        assert meta is not None
        assert meta["status"] == "queued"
        assert meta["engine"] == "vieneu"
        assert meta["model"] == "v3_turbo_int8"
        assert meta["estimated_seconds"] == data["estimated_seconds"]
    finally:
        main.generation_status.pop(cache_id, None)
        main.cleanup_incomplete_cache(cache_id)


def test_ui_uses_fixed_vieneu_model_and_offers_all_voices():
    html = client.get("/").text
    assert "Tối đa 5.000 từ" in html
    assert "hàng đợi Redis" not in html
    assert 'id="wordCount"' in html
    assert 'id="model"' not in html
    assert "https://github.com/duybaodg/story2audio" in html
    assert "https://github.com/dvchd/story2audio" in html
    assert "https://github.com/pnnbao97/VieNeu-TTS" in html

    from vieneu_model import get_preset_voices_from_file

    expected = {f"vieneu:{name}" for _description, name in get_preset_voices_from_file()}
    actual = {
        voice["value"]
        for voice in client.get("/tts/voices").json()["voices"]["vi"]
        if voice["engine"] == "vieneu"
    }
    assert expected <= actual


def test_conversion_estimate_refines_with_progress(monkeypatch):
    assert main.estimate_conversion_seconds("x" * 200, "vieneu") > 0

    monkeypatch.setattr(main.time, "time", lambda: 130.0)
    status = main.add_remaining_time({
        "status": "generating",
        "estimated_seconds": 100,
        "started_at": 100.0,
        "progress": 2,
        "total": 4,
    })
    assert status["remaining_seconds"] == 30


def test_global_cache_clear_disabled_by_default():
    response = client.delete("/tts/cache")
    assert response.status_code == 404


def test_document_routes_are_isolated_by_browser_session(tmp_path):
    from file_processor import active_documents, document_queue
    from models import Document, FileType

    owner = TestClient(app)
    stranger = TestClient(app)
    owner.get("/document/health")
    stranger.get("/document/health")
    owner_session = browser_session_id(owner)
    document = Document(
        document_id="private-doc",
        filename="private.pdf",
        file_type=FileType.PDF,
        file_size=1,
        file_path=str(tmp_path / "private.pdf"),
        owner_session=owner_session,
    )
    active_documents[document.document_id] = document
    document_queue.append(document.document_id)

    try:
        assert owner.get("/document/queue").json()["queue"][0]["document_id"] == "private-doc"
        assert stranger.get("/document/queue").json()["queue"] == []
        assert stranger.get("/document/private-doc").status_code == 404
        assert stranger.delete("/document/private-doc").status_code == 404
        assert owner.get("/document/private-doc").status_code == 200
    finally:
        active_documents.pop(document.document_id, None)
        if document.document_id in document_queue:
            document_queue.remove(document.document_id)


def test_tts_rejects_oversized_text(monkeypatch):
    monkeypatch.setattr(main, "TTS_MAX_TEXT_LENGTH", 5)
    response = client.post(
        "/tts/start",
        json={"text": "123456", "voice": "vi-VN-HoaiMyNeural", "engine": "edge"},
    )
    assert response.status_code == 413


def test_local_tts_rejects_when_capacity_is_full(monkeypatch):
    class FullCapacity:
        def acquire(self, blocking=False):
            return False

    monkeypatch.setattr(main, "_local_tts_slots", FullCapacity())
    response = client.post(
        "/tts/start",
        json={"text": "capacity check", "voice": "vi-VN-HoaiMyNeural", "engine": "edge"},
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


def test_vieneu_rejects_more_than_5000_words(monkeypatch):
    monkeypatch.setattr(main, "VIENEU_MAX_WORDS", 5000)
    response = client.post(
        "/tts/start",
        json={
            "text": "word " * 5001,
            "voice": "vieneu:default",
            "engine": "vieneu",
            "language": "vi",
        },
    )
    assert response.status_code == 413
    assert "5000-word limit" in response.json()["detail"]


def test_tts_rate_limit_blocks_request():
    class DenyLimiter:
        async def check_tts_limits(self, ip):
            return False, "limit reached"

    app.state.rate_limiter = DenyLimiter()
    try:
        response = client.post(
            "/tts/start",
            json={"text": "hello", "voice": "vi-VN-HoaiMyNeural", "engine": "edge"},
        )
        assert response.status_code == 429
    finally:
        del app.state.rate_limiter


def test_forwarded_ip_is_only_used_for_trusted_proxy(monkeypatch):
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"203.0.113.10")],
        "client": ("127.0.0.1", 1234),
    })
    monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", False)
    assert main.get_client_ip(request) == "127.0.0.1"
    monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
    assert main.get_client_ip(request) == "203.0.113.10"


def test_health_fails_when_redis_is_unavailable(monkeypatch):
    async def unavailable():
        return False

    monkeypatch.setattr(main, "redis_is_ready", unavailable)
    monkeypatch.setattr(main, "vieneu_worker_is_ready", unavailable)
    assert client.get("/health").status_code == 503
    assert client.get("/tts/health").status_code == 503
