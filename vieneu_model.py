"""
VieNeu Model Pool Manager with Per-Model Locking

Manages a pool of VieNeu TTS model instances with locks to ensure thread safety.
llama.cpp (used by VieNeu) is not thread-safe, so we need:
1. Multiple model instances for parallel processing
2. Per-model locks to prevent concurrent access to the same instance

Key features:
1. Creates one model instance per worker at startup
2. Each model has its own lock for thread-safe access
3. Threads acquire the model's lock before using it
"""

import logging
import os
import threading
import json
from threading import Lock
from typing import Optional, List, Tuple, Dict
from contextlib import contextmanager

logger = logging.getLogger("story2audio")

# Path to local voices.json
_VIENEU_VOICES_PATH = os.path.join(
    os.path.dirname(__file__),
    "models",
    "vieneu",
    "assets",
    "voices.json"
)

# Global pool state
_model_pool: List[Tuple["Vieneu", Lock]] = []  # (model, lock) tuples
_pool_lock = Lock()
_warmed_up = False

# Warmup configuration
WARMUP_ITERATIONS = 5  # Number of inferences to stabilize model quality


def get_pool_size() -> int:
    """Get the number of model instances in the pool.

    VieNeu uses sequential processing (no parallel) because llama.cpp
    is not thread-safe. Always returns 1 for reliability.
    """
    return 1


def _create_model_pool(size: int) -> List[Tuple["Vieneu", Lock]]:
    """Create a pool of VieNeu model instances, each with its own lock."""
    from vieneu import Vieneu

    pool = []
    for i in range(size):
        model = Vieneu()
        model_lock = Lock()  # Each model has its own lock
        pool.append((model, model_lock))
        print(f"[STARTUP] VieNeu model instance {i + 1}/{size} created")
        logger.info(f"VieNeu model instance {i + 1}/{size} created")
    return pool


def initialize_model_pool() -> None:
    """
    Initialize the model pool and warm up all instances.

    Should be called once at application startup.
    Thread-safe - can be called multiple times safely.
    """
    global _model_pool, _warmed_up

    if _warmed_up:
        return

    with _pool_lock:
        # Double-check lock
        if _warmed_up:
            return

        try:
            pool_size = get_pool_size()
            logger.info(f"Initializing VieNeu model pool with {pool_size} instances...")

            # Create the pool with locks
            _model_pool = _create_model_pool(pool_size)

            # Warm up each model (using the model's lock)
            logger.info("Warming up VieNeu models...")
            print(f"[STARTUP] Warming up {pool_size} VieNeu models ({WARMUP_ITERATIONS} iterations each)...")
            for i, (model, lock) in enumerate(_model_pool):
                try:
                    with lock:  # Acquire lock for warmup
                        for iteration in range(WARMUP_ITERATIONS):
                            model.infer(f"warmup {iteration}")
                    print(f"[STARTUP] Model {i + 1}/{pool_size} warmed up ({WARMUP_ITERATIONS} iterations)")
                    logger.info(f"Model {i + 1}/{pool_size} warmed up ({WARMUP_ITERATIONS} iterations)")
                except Exception as e:
                    print(f"[STARTUP] Model {i + 1} warmup failed: {e}")
                    logger.warning(f"Model {i + 1} warmup failed: {e}")

            _warmed_up = True
            logger.info(f"VieNeu model pool initialized and warmed up ({pool_size} instances)")

        except Exception as e:
            logger.error(f"Failed to initialize VieNeu model pool: {e}")
            raise


@contextmanager
def get_vieneu_model():
    """
    Get a VieNeu model instance with automatic lock management.

    This function:
    1. Selects a model from the pool (round-robin based on thread ID)
    2. Acquires the model's lock
    3. Yields the model for use
    4. Releases the lock when done

    Usage:
        with get_vieneu_model() as model:
            result = model.infer(text)

    Falls back to creating a new instance if the pool is not available.

    Yields:
        Vieneu: A model instance
    """
    # Try to use the pool first
    if _warmed_up and _model_pool:
        # Select model based on thread ID (consistent assignment per thread)
        thread_id = threading.get_ident()
        pool_size = len(_model_pool)
        model_index = thread_id % pool_size
        model, lock = _model_pool[model_index]

        # Acquire the model's lock and yield the model
        with lock:
            yield model
        return

    # Fallback: create a new instance if pool is not available
    logger.warning("Model pool not available, creating new instance")
    from vieneu import Vieneu
    model = Vieneu()
    # Warm up with at least 1 inference to avoid cold start delay
    try:
        model.infer("warmup")
        logger.info("Fallback model warmed up successfully")
    except Exception as e:
        logger.warning(f"Fallback model warmup failed: {e}")
    yield model


def get_vieneu_model_sync() -> "Vieneu":
    """
    Get a VieNeu model instance WITHOUT lock management.

    This is a compatibility function for code that can't use context manager.
    The caller must manually manage thread safety.

    Returns:
        Vieneu: A model instance (use with caution in threaded contexts)
    """
    if _warmed_up and _model_pool:
        thread_id = threading.get_ident()
        pool_size = len(_model_pool)
        model_index = thread_id % pool_size
        model, _lock = _model_pool[model_index]
        return model

    # Fallback
    from vieneu import Vieneu
    return Vieneu()


def is_warmed_up() -> bool:
    """Check if the model pool has been initialized and warmed up."""
    return _warmed_up and len(_model_pool) > 0


def get_pool_info() -> dict:
    """Get information about the current model pool state."""
    return {
        "pool_size": len(_model_pool),
        "warmed_up": _warmed_up,
        "max_workers": get_pool_size(),
    }


def reset_model_pool() -> None:
    """
    Reset the model pool.

    Primarily for testing purposes. Allows re-creating
    the model pool from scratch.
    """
    global _model_pool, _warmed_up
    with _pool_lock:
        _model_pool = []
        _warmed_up = False
        logger.info("VieNeu model pool reset")


def get_preset_voices_from_file() -> List[Tuple[str, str]]:
    """
    Load preset voices from local voices.json file.

    Returns list of (description, voice_id) tuples for all 6 preset voices:
    - Binh (nam miền Bắc) - default
    - Tuyen (nam miền Bắc)
    - Vinh (nam miền Nam)
    - Doan (nữ miền Nam)
    - Ly (nữ miền Bắc)
    - Ngoc (nữ miền Bắc)

    Falls back to package's list_preset_voices() if local file not found.
    """
    # Try to load from local voices.json first
    if os.path.exists(_VIENEU_VOICES_PATH):
        try:
            with open(_VIENEU_VOICES_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)

            presets = data.get('presets', {})
            voices = []

            for voice_id, voice_data in presets.items():
                description = voice_data.get('description', voice_id)
                voices.append((description, voice_id))

            logger.info(f"Loaded {len(voices)} preset voices from local file")
            return voices

        except Exception as e:
            logger.warning(f"Failed to load local voices.json: {e}")

    # Fallback to package's preset voices
    try:
        from vieneu import Vieneu
        tts = Vieneu()
        voices = tts.list_preset_voices()
        logger.info(f"Loaded {len(voices)} preset voices from package")
        return voices
    except Exception as e:
        logger.error(f"Failed to load preset voices: {e}")
        return []


def get_voice_description(voice_id: str) -> str:
    """
    Get human-readable description for a voice ID.

    Args:
        voice_id: Voice ID (e.g., "Binh", "Tuyen", etc.)

    Returns:
        Human-readable description or the voice_id if not found.
    """
    if os.path.exists(_VIENEU_VOICES_PATH):
        try:
            with open(_VIENEU_VOICES_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)

            presets = data.get('presets', {})
            if voice_id in presets:
                return presets[voice_id].get('description', voice_id)
        except Exception:
            pass

    return voice_id
