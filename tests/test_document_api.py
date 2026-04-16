# tests/test_document_api.py
import pytest
import asyncio
import hashlib
import os
import sys
import tempfile
from fastapi.testclient import TestClient

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set environment variable before importing modules
os.environ['DOCUMENT_STORAGE_PATH'] = tempfile.mkdtemp()

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
            "checksum": "abc123"
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
            "checksum": "abc123"
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

def test_health_check():
    response = client.get("/document/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "active_sessions" in data
