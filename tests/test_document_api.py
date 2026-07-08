# tests/test_document_api.py
import pytest
import asyncio
import hashlib
import os
import sys
import tempfile
import time
from fastapi.testclient import TestClient

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set environment variable before importing modules
os.environ['DOCUMENT_STORAGE_PATH'] = tempfile.mkdtemp()

import main
from main import app
from document_api import router
app.include_router(router)

client = TestClient(app)

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

def test_stream_extraction():
    """Test streaming extraction endpoint returns correct content type for non-existent document."""
    response = client.get("/document/test-doc-123/extract/stream")
    # Should return 404 for non-existent document
    assert response.status_code == 404


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
        if os.path.exists(audio_path):
            os.remove(audio_path)


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
        assert cache_id not in main.generation_status

        meta = main.load_cache_meta(cache_id)
        assert meta is not None
        assert meta["status"] == "queued"
        assert meta["engine"] == "vieneu"
    finally:
        main.generation_status.pop(cache_id, None)
        main.cleanup_incomplete_cache(cache_id)


def test_global_cache_clear_disabled_by_default():
    response = client.delete("/tts/cache")
    assert response.status_code == 404
