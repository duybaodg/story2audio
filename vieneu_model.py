"""Own the single VieNeu v3 Turbo INT8 model and serialize inference."""

import logging
import os
import json
from threading import Lock
from typing import Any, List, Tuple, Dict
from contextlib import contextmanager

logger = logging.getLogger("ebook2audio")

# Path to local voices.json
_VIENEU_VOICES_PATH = os.path.join(
    os.path.dirname(__file__),
    "models",
    "vieneu",
    "assets",
    "voices.json"
)

# Global pool state
_model_pool: List[Tuple[Any, Lock]] = []  # (model, lock) tuples
_pool_lock = Lock()
_warmed_up = False
# The application intentionally supports one model to avoid duplicate downloads,
# memory use, and expensive runtime model reloads.
VIENEU_MODEL = "v3_turbo_int8"
VIENEU_V3_TURBO_REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"

# Warmup configuration
WARMUP_ITERATIONS = int(os.getenv("VIENEU_WARMUP_ITERATIONS", "1"))
WARMUP_TEXT = os.getenv("VIENEU_WARMUP_TEXT", "Xin chào")


def get_vieneu_config() -> Dict[str, object]:
    """Return the fixed VieNeu v3 Turbo INT8 SDK configuration."""
    return {"mode": VIENEU_MODEL, "kwargs": {"mode": "v3turbo", "precision": "int8"}}


def get_public_vieneu_config() -> Dict[str, object]:
    """Return VieNeu config safe for logs and health responses."""
    config = get_vieneu_config()
    kwargs = dict(config["kwargs"])
    if "hf_token" in kwargs:
        kwargs["hf_token"] = "***"
    return {"mode": config["mode"], "kwargs": kwargs}


def create_vieneu_instance():
    """Create the fixed VieNeu v3 Turbo INT8 model."""
    from vieneu import Vieneu
    return Vieneu(**get_vieneu_config()["kwargs"])


def get_pool_size() -> int:
    """Get the number of model instances in the pool.

    VieNeu uses sequential processing (no parallel) because llama.cpp
    is not thread-safe. Always returns 1 for reliability.
    """
    return 1


def _create_model_pool(size: int) -> List[Tuple[Any, Lock]]:
    """Create a pool of VieNeu model instances, each with its own lock."""
    pool = []
    config = get_vieneu_config()
    for i in range(size):
        model = create_vieneu_instance()
        model_lock = Lock()  # Each model has its own lock
        pool.append((model, model_lock))
        print(f"[STARTUP] VieNeu model instance {i + 1}/{size} created ({config['mode']})")
        logger.info(f"VieNeu model instance {i + 1}/{size} created ({config['mode']})")
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
            config = get_public_vieneu_config()
            logger.info(
                "Initializing VieNeu model pool with %s instance(s): mode=%s, config=%s",
                pool_size,
                config["mode"],
                config["kwargs"],
            )

            # Create the pool with locks
            _model_pool = _create_model_pool(pool_size)

            # Warm up each model (using the model's lock)
            logger.info("Warming up VieNeu models...")
            print(f"[STARTUP] Warming up {pool_size} VieNeu models ({WARMUP_ITERATIONS} iterations each)...")
            for i, (model, lock) in enumerate(_model_pool):
                try:
                    with lock:  # Acquire lock for warmup
                        for iteration in range(WARMUP_ITERATIONS):
                            model.infer(f"{WARMUP_TEXT} {iteration}")
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

    This function acquires the model lock, yields the model, and releases it.

    Usage:
        with get_vieneu_model() as model:
            result = model.infer(text)

    Falls back to creating a new instance if the pool is not available.

    Yields:
        Vieneu: A model instance
    """
    if not _warmed_up:
        initialize_model_pool()

    # Try to use the pool first
    if _model_pool:
        model, lock = _model_pool[0]

        # Acquire the model's lock and yield the model
        with lock:
            yield model
        return

    # Fallback: create a new instance if pool is not available
    logger.warning("Model pool not available, creating new instance")
    model = create_vieneu_instance()
    # Warm up with at least 1 inference to avoid cold start delay
    try:
        model.infer(WARMUP_TEXT)
        logger.info("Fallback model warmed up successfully")
    except Exception as e:
        logger.warning(f"Fallback model warmup failed: {e}")
    yield model


def is_warmed_up() -> bool:
    """Check if the model pool has been initialized and warmed up."""
    return _warmed_up and len(_model_pool) > 0


def get_pool_info() -> dict:
    """Get information about the current model pool state."""
    return {
        "pool_size": len(_model_pool),
        "warmed_up": _warmed_up,
        "max_workers": get_pool_size(),
        "model_version": get_public_vieneu_config()["mode"],
        "config": get_public_vieneu_config(),
        "active_model": VIENEU_MODEL,
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

    Returns list of (description, voice_id) tuples for local or package voices.

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
        tts = create_vieneu_instance()
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
