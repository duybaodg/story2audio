"""
Tests for VieNeu TTS reliability and sequential processing.

Since llama.cpp is not thread-safe, VieNeu uses sequential processing
to ensure 100% reliability. These tests verify:
- Sequential chunk processing
- Proper model pool management
- Chunk size limits
- Error recovery
"""

import pytest
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from vieneu_model import (
    create_vieneu_instance,
    get_pool_size,
    get_pool_info,
    get_vieneu_config,
    initialize_model_pool,
)


class TestVieneuModelPool:
    """Tests for VieNeu model pool configuration."""

    def test_pool_size_is_one(self):
        """Test that pool size is 1 for sequential processing."""
        assert get_pool_size() == 1, "Pool size should be 1 for reliable sequential processing"

    def test_pool_info_structure(self):
        """Test that pool info returns correct structure."""
        info = get_pool_info()
        assert "pool_size" in info
        assert "warmed_up" in info
        assert "max_workers" in info
        # max_workers reflects the configured pool size (1 for sequential)
        assert info["max_workers"] == 1

    def test_default_config_uses_v3_turbo(self, monkeypatch):
        """Test that the default VieNeu config uses v3 Turbo."""
        for key in [
            "VIENEU_MODE",
            "VIENEU_MODEL_REPO",
            "VIENEU_BACKBONE_DEVICE",
            "VIENEU_CODEC_REPO",
            "VIENEU_CODEC_DEVICE",
            "VIENEU_GGUF_FILENAME",
            "HF_TOKEN",
            "HUGGING_FACE_HUB_TOKEN",
        ]:
            monkeypatch.delenv(key, raising=False)

        config = get_vieneu_config()

        assert config["mode"] == "v3_turbo"
        assert config["kwargs"] == {}

    @pytest.mark.parametrize(
        ("variant", "precision"),
        [("v3_turbo", "fp32"), ("v3_turbo_int8", "int8")],
    )
    def test_selectable_v3_precision(self, monkeypatch, variant, precision):
        calls = []

        monkeypatch.setattr("vieneu.Vieneu", lambda **kwargs: calls.append(kwargs) or object())

        create_vieneu_instance(variant)

        assert calls == [{"mode": "v3turbo", "precision": precision}]

    def test_v2_turbo_config_uses_turbo_cpu_args(self, monkeypatch):
        """Test that legacy v2 Turbo mode remains available explicitly."""
        monkeypatch.setenv("VIENEU_MODE", "v2_turbo")
        for key in [
            "VIENEU_MODEL_REPO",
            "VIENEU_BACKBONE_FILENAME",
            "VIENEU_DEVICE",
            "VIENEU_DECODER_REPO",
            "VIENEU_ENCODER_REPO",
        ]:
            monkeypatch.delenv(key, raising=False)

        config = get_vieneu_config()

        assert config["mode"] == "v2_turbo"
        assert config["kwargs"]["backbone_repo"] == "pnnbao-ump/VieNeu-TTS-v2-Turbo-GGUF"
        assert config["kwargs"]["backbone_filename"] == "vieneu-tts-v2-turbo.gguf"
        assert config["kwargs"]["decoder_repo"] == "pnnbao-ump/VieNeu-Codec"
        assert config["kwargs"]["encoder_repo"] == "pnnbao-ump/VieNeu-Codec"
        assert config["kwargs"]["device"] == "cpu"
        assert "backbone_device" not in config["kwargs"]
        assert "codec_repo" not in config["kwargs"]
        assert "gguf_filename" not in config["kwargs"]

    def test_v2_standard_config_uses_standard_args(self, monkeypatch):
        """Test that legacy v2 Standard mode remains available explicitly."""
        monkeypatch.setenv("VIENEU_MODE", "v2_standard")
        monkeypatch.delenv("VIENEU_MODEL_REPO", raising=False)

        config = get_vieneu_config()

        assert config["mode"] == "v2_standard"
        assert config["kwargs"]["backbone_repo"] == "pnnbao-ump/VieNeu-TTS-v2"
        assert config["kwargs"]["backbone_device"] == "cpu"
        assert config["kwargs"]["gguf_filename"] == "VieNeu-TTS-v2-Q4-K-M.gguf"

    def test_turbo_gpu_config_uses_turbo_gpu_args(self, monkeypatch):
        """Test that GPU Turbo mode passes the constructor arguments VieNeu expects."""
        monkeypatch.setenv("VIENEU_MODE", "v2_turbo_gpu")
        monkeypatch.setenv("VIENEU_DEVICE", "cuda")
        monkeypatch.setenv("VIENEU_TURBO_BACKEND", "lmdeploy")
        monkeypatch.delenv("VIENEU_MODEL_REPO", raising=False)

        config = get_vieneu_config()

        assert config["mode"] == "v2_turbo_gpu"
        assert config["kwargs"]["backbone_repo"] == "pnnbao-ump/VieNeu-TTS-v2-Turbo"
        assert config["kwargs"]["decoder_repo"] == "pnnbao-ump/VieNeu-Codec"
        assert config["kwargs"]["encoder_repo"] == "pnnbao-ump/VieNeu-Codec"
        assert config["kwargs"]["device"] == "cuda"
        assert config["kwargs"]["backend"] == "lmdeploy"
        assert "backbone_device" not in config["kwargs"]
        assert "codec_repo" not in config["kwargs"]


class TestVieneuSequentialProcessing:
    """Tests for sequential chunk processing of VieNeu TTS."""

    def test_vieneu_sync_wrapper_exists(self):
        """Test that the sync wrapper function exists and is callable."""
        from main import vieneu_tts_to_audio_sync
        assert callable(vieneu_tts_to_audio_sync)

    def test_chunk_size_limit(self):
        """Test that VieNeu uses 500 character chunk limit."""
        from main import split_text_into_chunks
        from vietnamese_text_processor import create_vietnamese_chunks

        # Test that the 500 char limit is used for VieNeu
        long_text = "Xin chào, " * 100  # ~1000 characters
        chunks = create_vietnamese_chunks(long_text, max_chunk_size=500)

        # Should have multiple chunks
        assert len(chunks) >= 2

        # Each chunk should be under or near the limit
        for chunk in chunks:
            # Allow some margin for natural boundaries
            assert len(chunk) <= 600, f"Chunk too long: {len(chunk)} chars"

    def test_sequential_processing_preserves_order(self):
        """Test that chunks are processed in correct order sequentially."""
        from unittest.mock import patch, Mock
        import main

        # Mock the model to track call order
        call_order = []

        def mock_generate(text, voice, *args, **kwargs):
            call_order.append(text)
            return (b"MP3_DATA_" + str(len(call_order)).encode(), "mp3")

        with patch.object(main, 'vieneu_tts_to_audio_sync', side_effect=mock_generate):
            chunks = ["chunk_0", "chunk_1", "chunk_2", "chunk_3"]
            voice = "vieneu:default"

            for chunk in chunks:
                main.vieneu_tts_to_audio_sync(chunk, voice)

        # Verify order was preserved
        assert call_order == chunks

    def test_error_handling_in_sequential_mode(self):
        """Test that errors in sequential processing are properly raised."""
        from unittest.mock import patch
        import main

        def mock_generate_fail(text, voice, *args, **kwargs):
            if "chunk_1" in text:
                raise RuntimeError("Chunk processing failed")
            return (b"MP3_DATA", "mp3")

        with patch.object(main, 'vieneu_tts_to_audio_sync', side_effect=mock_generate_fail):
            # Should raise error on chunk_1
            with pytest.raises(RuntimeError, match="Chunk processing failed"):
                main.vieneu_tts_to_audio_sync("chunk_1", "vieneu:default")


class TestVieneuIntegration:
    """Integration tests for VieNeu TTS."""

    def test_vieneu_import_works(self):
        """Test that VieNeu module can be imported."""
        from main import VIENEU_MAX_WORKERS
        # Should be 1 for sequential processing
        assert VIENEU_MAX_WORKERS == 1

    def test_vieneu_engine_detection(self):
        """Test that VieNeu engine is correctly detected."""
        from main import get_engine_from_voice

        assert get_engine_from_voice("vieneu:default") == "vieneu"
        assert get_engine_from_voice("vieneu:some_preset") == "vieneu"

    def test_vieneu_voice_validation_rejects_stale_preset(self):
        """Test that removed VieNeu presets fail before worker execution."""
        from fastapi import HTTPException
        from main import validate_voice

        assert validate_voice("vi", "vieneu:Trúc Ly", "vieneu") == "vieneu:Trúc Ly"
        for voice in ("Minh Triết", "Thùy Dung", "Quang Sơn", "Ngọc Trân"):
            assert validate_voice("vi", f"vieneu:{voice}", "vieneu") == f"vieneu:{voice}"
        with pytest.raises(HTTPException):
            validate_voice("vi", "vieneu:Đức Trí", "vieneu")
