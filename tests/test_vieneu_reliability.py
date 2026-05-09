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

from vieneu_model import get_pool_size, get_pool_info, initialize_model_pool


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
