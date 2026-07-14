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
_model_pool: List[Tuple["Vieneu", Lock]] = []  # (model, lock) tuples
_pool_lock = Lock()
_warmed_up = False
_active_model_variant: Optional[str] = None

# VieNeu defaults. v3 Turbo is the default SDK path; v2 modes remain available
# for compatibility by setting VIENEU_MODE=v2_standard, v2_turbo, or v2_turbo_gpu.
VIENEU_V3_TURBO_REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"
VIENEU_V2_REPO = "pnnbao-ump/VieNeu-TTS-v2"
VIENEU_V2_GGUF = "VieNeu-TTS-v2-Q4-K-M.gguf"
VIENEU_TURBO_REPO = "pnnbao-ump/VieNeu-TTS-v2-Turbo-GGUF"
VIENEU_TURBO_GGUF = "vieneu-tts-v2-turbo.gguf"
VIENEU_TURBO_GPU_REPO = "pnnbao-ump/VieNeu-TTS-v2-Turbo"
VIENEU_CODEC_REPO = "pnnbao-ump/VieNeu-Codec"
VIENEU_DECODER_FILENAME = "vieneu_decoder.onnx"
VIENEU_ENCODER_FILENAME = "vieneu_encoder.onnx"
DEFAULT_CODEC_REPO = "neuphonic/neucodec-onnx-decoder-int8"
VIENEU_MODEL_PRECISIONS = {
    "v3_turbo": "fp32",
    "v3_turbo_int8": "int8",
}

# Warmup configuration
WARMUP_ITERATIONS = int(os.getenv("VIENEU_WARMUP_ITERATIONS", "5"))
WARMUP_TEXT = os.getenv("VIENEU_WARMUP_TEXT", "Xin chào")


def get_vieneu_config() -> Dict[str, object]:
    """Return the active VieNeu SDK configuration.

    Defaults use VieNeu-TTS v3 Turbo. The current SDK's v3 Turbo path is
    selected with Vieneu() and auto-selects CPU ONNX or GPU PyTorch.
    Set VIENEU_MODE=v2_standard, v2_turbo, or v2_turbo_gpu for legacy v2.
    Set VIENEU_MODE=remote and VIENEU_REMOTE_API_BASE to use a remote v2 API.
    """
    mode = os.getenv("VIENEU_MODE", "v3_turbo").strip().lower() or "v3_turbo"
    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")

    if mode in {"v3", "v3_turbo", "turbo"}:
        mode = "v3_turbo"
        kwargs = {}
    elif mode in {"remote", "api"}:
        kwargs = {
            "api_base": os.getenv("VIENEU_REMOTE_API_BASE", "http://localhost:23333/v1"),
            "model_name": os.getenv("VIENEU_MODEL_REPO", VIENEU_V2_REPO),
            "codec_repo": os.getenv("VIENEU_CODEC_REPO", "neuphonic/distill-neucodec"),
            "codec_device": os.getenv("VIENEU_CODEC_DEVICE", "cpu"),
        }
    elif mode == "v2_turbo":
        kwargs = {
            "backbone_repo": os.getenv("VIENEU_MODEL_REPO", VIENEU_TURBO_REPO),
            "backbone_filename": os.getenv("VIENEU_BACKBONE_FILENAME", VIENEU_TURBO_GGUF),
            "decoder_repo": os.getenv("VIENEU_DECODER_REPO", VIENEU_CODEC_REPO),
            "decoder_filename": os.getenv("VIENEU_DECODER_FILENAME", VIENEU_DECODER_FILENAME),
            "encoder_repo": os.getenv("VIENEU_ENCODER_REPO", VIENEU_CODEC_REPO),
            "encoder_filename": os.getenv("VIENEU_ENCODER_FILENAME", VIENEU_ENCODER_FILENAME),
            "device": os.getenv("VIENEU_DEVICE", "cpu"),
        }
    elif mode == "v2_turbo_gpu":
        kwargs = {
            "backbone_repo": os.getenv("VIENEU_MODEL_REPO", VIENEU_TURBO_GPU_REPO),
            "decoder_repo": os.getenv("VIENEU_DECODER_REPO", VIENEU_CODEC_REPO),
            "decoder_filename": os.getenv("VIENEU_DECODER_FILENAME", VIENEU_DECODER_FILENAME),
            "encoder_repo": os.getenv("VIENEU_ENCODER_REPO", VIENEU_CODEC_REPO),
            "encoder_filename": os.getenv("VIENEU_ENCODER_FILENAME", VIENEU_ENCODER_FILENAME),
            "device": os.getenv("VIENEU_DEVICE", "cuda"),
            "backend": os.getenv("VIENEU_TURBO_BACKEND", "standard"),
        }
    elif mode in {"standard", "v2_standard"}:
        mode = "v2_standard"
        kwargs = {
            "backbone_repo": os.getenv("VIENEU_MODEL_REPO", VIENEU_V2_REPO),
            "backbone_device": os.getenv("VIENEU_BACKBONE_DEVICE", "cpu"),
            "codec_repo": os.getenv("VIENEU_CODEC_REPO", DEFAULT_CODEC_REPO),
            "codec_device": os.getenv("VIENEU_CODEC_DEVICE", "cpu"),
            "gguf_filename": os.getenv("VIENEU_GGUF_FILENAME", VIENEU_V2_GGUF),
        }
    else:
        raise ValueError(f"Unsupported VIENEU_MODE: {mode}")

    if hf_token:
        kwargs["hf_token"] = hf_token

    return {"mode": mode, "kwargs": kwargs}


def get_public_vieneu_config() -> Dict[str, object]:
    """Return VieNeu config safe for logs and health responses."""
    config = get_vieneu_config()
    kwargs = dict(config["kwargs"])
    if "hf_token" in kwargs:
        kwargs["hf_token"] = "***"
    return {"mode": config["mode"], "kwargs": kwargs}


def create_vieneu_instance(model_variant: Optional[str] = None):
    """Create a VieNeu SDK instance using the configured model."""
    from vieneu import Vieneu

    if model_variant:
        try:
            precision = VIENEU_MODEL_PRECISIONS[model_variant]
        except KeyError as exc:
            raise ValueError(f"Unsupported VieNeu model: {model_variant}") from exc
        return Vieneu(mode="v3turbo", precision=precision)

    config = get_vieneu_config()
    if config["mode"] == "v3_turbo":
        return Vieneu(**config["kwargs"])
    sdk_mode = {
        "v2_standard": "standard",
        "v2_turbo": "turbo",
        "v2_turbo_gpu": "turbo_gpu",
    }.get(config["mode"], config["mode"])
    return Vieneu(mode=sdk_mode, **config["kwargs"])


def get_pool_size() -> int:
    """Get the number of model instances in the pool.

    VieNeu uses sequential processing (no parallel) because llama.cpp
    is not thread-safe. Always returns 1 for reliability.
    """
    return 1


def _create_model_pool(size: int, model_variant: Optional[str] = None) -> List[Tuple["Vieneu", Lock]]:
    """Create a pool of VieNeu model instances, each with its own lock."""
    pool = []
    config = get_vieneu_config()
    for i in range(size):
        model = create_vieneu_instance(model_variant)
        model_lock = Lock()  # Each model has its own lock
        pool.append((model, model_lock))
        print(f"[STARTUP] VieNeu model instance {i + 1}/{size} created ({config['mode']})")
        logger.info(f"VieNeu model instance {i + 1}/{size} created ({config['mode']})")
    return pool


def initialize_model_pool(model_variant: Optional[str] = None) -> None:
    """
    Initialize the model pool and warm up all instances.

    Should be called once at application startup.
    Thread-safe - can be called multiple times safely.
    """
    global _model_pool, _warmed_up, _active_model_variant

    if _warmed_up and _active_model_variant == model_variant:
        return

    with _pool_lock:
        # Double-check lock
        if _warmed_up and _active_model_variant == model_variant:
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
            _model_pool = _create_model_pool(pool_size, model_variant)

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
            _active_model_variant = model_variant
            logger.info(f"VieNeu model pool initialized and warmed up ({pool_size} instances)")

        except Exception as e:
            logger.error(f"Failed to initialize VieNeu model pool: {e}")
            raise


@contextmanager
def get_vieneu_model(model_variant: Optional[str] = None):
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
    if not _warmed_up or _active_model_variant != model_variant:
        initialize_model_pool(model_variant)

    # Try to use the pool first
    if _model_pool:
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
    model = create_vieneu_instance(model_variant)
    # Warm up with at least 1 inference to avoid cold start delay
    try:
        model.infer(WARMUP_TEXT)
        logger.info("Fallback model warmed up successfully")
    except Exception as e:
        logger.warning(f"Fallback model warmup failed: {e}")
    yield model


def get_vieneu_model_sync(model_variant: Optional[str] = None) -> "Vieneu":
    """
    Get a VieNeu model instance WITHOUT lock management.

    This is a compatibility function for code that can't use context manager.
    The caller must manually manage thread safety.

    Returns:
        Vieneu: A model instance (use with caution in threaded contexts)
    """
    if not _warmed_up or _active_model_variant != model_variant:
        initialize_model_pool(model_variant)
    if _model_pool:
        thread_id = threading.get_ident()
        pool_size = len(_model_pool)
        model_index = thread_id % pool_size
        model, _lock = _model_pool[model_index]
        return model

    # Fallback
    return create_vieneu_instance(model_variant)


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
        "active_model": _active_model_variant,
    }


def reset_model_pool() -> None:
    """
    Reset the model pool.

    Primarily for testing purposes. Allows re-creating
    the model pool from scratch.
    """
    global _model_pool, _warmed_up, _active_model_variant
    with _pool_lock:
        _model_pool = []
        _warmed_up = False
        _active_model_variant = None
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
