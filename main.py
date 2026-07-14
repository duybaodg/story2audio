import os
import io
import re
import sys
import json
import math
import time
import logging
import hashlib
import asyncio
import unicodedata
import threading
import traceback
import secrets
from datetime import timedelta
from typing import List, Dict, AsyncGenerator, Literal, Optional, Tuple, Union

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, status
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Rate limiter for upload endpoints
from rate_limiter import RateLimiter

import edge_tts
from gtts import gTTS
from dotenv import load_dotenv
from document_api import router as document_router

# Audio quality imports
from vieneu_audio_quality import (
    process_vienneu_audio,
    get_file_extension,
    AudioQuality,
)
from vieneu_model import get_pool_size
from tts_queue import (
    TTSQueueError,
    enqueue_vieneu_tts_job,
    is_vieneu_cancelled_sync,
    redis_is_ready,
    request_vieneu_cancel,
    vieneu_worker_is_ready,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Windows asyncio patch (suppress benign socket shutdown errors)
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    import asyncio.proactor_events

    _original_call_connection_lost = (
        asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost
    )

    def _patched_call_connection_lost(self, exc):
        try:
            _original_call_connection_lost(self, exc)
        except OSError:
            pass

    asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost = (
        _patched_call_connection_lost
    )

# ---------------------------------------------------------------------------
# Directories & Setup
# ---------------------------------------------------------------------------
VERSION = os.environ.get("APP_VERSION", "v4.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
CACHE_DIR = os.path.join(BASE_DIR, "audio_cache")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Story to Audio + Live Subtitles API")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(document_router)

logger = logging.getLogger("ebook2audio")

# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------
rate_limiter = RateLimiter()


def get_client_ip(request: Request) -> str:
    if TRUST_PROXY_HEADERS:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def browser_session_middleware(request: Request, call_next):
    session_id = request.cookies.get("story2audio_session")
    if not session_id or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", session_id):
        session_id = secrets.token_urlsafe(32)
    request.state.session_id = session_id
    response = await call_next(request)
    if request.cookies.get("story2audio_session") != session_id:
        response.set_cookie(
            "story2audio_session",
            session_id,
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=SESSION_COOKIE_SECURE,
            samesite="lax",
        )
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply rate limiting to upload initiate endpoint only.

    Note: We do NOT rate limit chunk uploads because:
    - Large files may have many chunks
    - Rate limiting chunk uploads would break legitimate file uploads
    - Retries after transient failures should not be blocked
    """
    # Only rate limit the upload initiate endpoint, NOT chunk uploads
    if request.url.path == "/document/upload/initiate" or request.url.path.startswith("/document/upload/initiate?"):
        limiter = app.state.rate_limiter if hasattr(app.state, 'rate_limiter') else None

        if limiter:
            ip = get_client_ip(request)

            # Check limits (no session_id for initiate endpoint)
            allowed, error = await limiter.check_upload_limits(ip, session_id=None)
            if not allowed:
                # Return JSONResponse directly instead of raising HTTPException
                # This ensures proper error response from middleware
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": error}
                )
    elif request.url.path == "/tts/start":
        limiter = app.state.rate_limiter if hasattr(app.state, "rate_limiter") else None
        if limiter:
            allowed, error = await limiter.check_tts_limits(get_client_ip(request))
            if not allowed:
                return JSONResponse(status_code=429, content={"detail": error})

    return await call_next(request)


# ---------------------------------------------------------------------------
# Cache-ID validation (chặn path traversal)
# ---------------------------------------------------------------------------
_CACHE_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def validate_cache_id(cache_id: str) -> str:
    cache_id = (cache_id or "").strip()
    if not _CACHE_ID_RE.fullmatch(cache_id):
        raise HTTPException(status_code=400, detail="Invalid cache id")
    return cache_id


# ---------------------------------------------------------------------------
# Session Verification Endpoint
# ---------------------------------------------------------------------------
@app.get("/tts/session/{cache_id}")
async def verify_session(cache_id: str):
    """
    Verify if a cached TTS result still exists.
    Returns cache metadata if found, 404 if not.
    Used by frontend for session restoration after page refresh.
    """
    # Validate cache_id format to prevent path traversal
    cache_id = validate_cache_id(cache_id)

    # Check if audio file exists (try .mp3 first, then .wav for lossless)
    audio_path = get_audio_path(cache_id, "mp3")
    if not os.path.exists(audio_path):
        audio_path = get_audio_path(cache_id, "wav")

    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="Cache not found")

    # Load metadata if available
    metadata = load_cache_meta(cache_id) or {}

    return {
        "exists": True,
        "cache_id": cache_id,
        "duration": metadata.get("duration"),
        "text_length": metadata.get("text_length"),
        "voice": metadata.get("voice"),
        "created_at": metadata.get("created_at")
    }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROXY = os.getenv("PROXY")
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "").lower() in {"1", "true", "yes"}
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
ENABLE_DEBUG_TTS = os.getenv("ENABLE_DEBUG_TTS", "").lower() in {"1", "true", "yes"}
VIENEU_MAX_WORKERS = get_pool_size()
TTS_STREAM_FIRST_BYTE_TIMEOUT_SECONDS = int(os.getenv("TTS_STREAM_FIRST_BYTE_TIMEOUT_SECONDS", "300"))
VIENEU_INIT_IN_WEB = os.getenv("VIENEU_INIT_IN_WEB", "").lower() in {"1", "true", "yes"}
ENABLE_GLOBAL_CACHE_CLEAR = os.getenv("ENABLE_GLOBAL_CACHE_CLEAR", "").lower() in {"1", "true", "yes"}
AUDIO_CACHE_RETENTION_HOURS = int(os.getenv("AUDIO_CACHE_RETENTION_HOURS", "12"))
TTS_MAX_TEXT_LENGTH = int(os.getenv("TTS_MAX_TEXT_LENGTH", "100000"))

if PROXY:
    os.environ["HTTP_PROXY"] = PROXY
    os.environ["HTTPS_PROXY"] = PROXY

generation_status: Dict[str, dict] = {}
_generation_locks: Dict[str, asyncio.Lock] = {}
_cancellation_requests: Dict[str, bool] = {}


def request_cancellation(cache_id: str) -> bool:
    """Mark a generation for cancellation. Returns True if active generation found."""
    if cache_id not in generation_status:
        return False
    _cancellation_requests[cache_id] = True
    return True


def is_cancelled(cache_id: str) -> bool:
    """Check and consume cancellation flag."""
    if _cancellation_requests.pop(cache_id, False):
        return True
    return is_vieneu_cancelled_sync(cache_id)

# ---------------------------------------------------------------------------
# Language & Voice Registry
# ---------------------------------------------------------------------------

def _load_vieneu_voices() -> List[Dict[str, str]]:
    """Load VieNeu preset voices from local voices.json file."""
    try:
        from vieneu_model import get_preset_voices_from_file

        available = get_preset_voices_from_file()
        voices = [
            {"value": "vieneu:default", "label": "Mặc định [VieNeu v3 Turbo]", "engine": "vieneu"}
        ]

        for desc, name in available:
            voices.append({
                "value": f"vieneu:{name}",
                "label": f"{desc} [VieNeu v3 Turbo]",
                "engine": "vieneu"
            })

        logger.info(f"Loaded {len(voices)} VieNeu voices")
        return voices
    except Exception as e:
        logger.warning(f"Failed to load VieNeu voices: {e}")
        return []


# Load VieNeu voices at startup (lazy load)
_VIENEU_VOICES: List[Dict[str, str]] = []

EDGE_VOICES: Dict[str, List[Dict[str, str]]] = {
    "vi": [
        {"value": "vi-VN-HoaiMyNeural", "label": "Hoài Mỹ (Nữ) [Edge TTS]", "engine": "edge"},
        {"value": "vi-VN-NamMinhNeural", "label": "Nam Minh (Nam) [Edge TTS]", "engine": "edge"},
    ],
    "en": [
        {"value": "en-US-AriaNeural", "label": "Aria (Female, US) [Edge TTS]", "engine": "edge"},
        {"value": "en-US-GuyNeural", "label": "Guy (Male, US) [Edge TTS]", "engine": "edge"},
        {"value": "en-US-JennyNeural", "label": "Jenny (Female, US) [Edge TTS]", "engine": "edge"},
        {"value": "en-GB-SoniaNeural", "label": "Sonia (Female, UK) [Edge TTS]", "engine": "edge"},
        {"value": "en-GB-RyanNeural", "label": "Ryan (Male, UK) [Edge TTS]", "engine": "edge"},
        {"value": "en-AU-NatashaNeural", "label": "Natasha (Female, AU) [Edge TTS]", "engine": "edge"},
    ],
    "ja": [
        {"value": "ja-JP-NanamiNeural", "label": "Nanami (女性) [Edge TTS]", "engine": "edge"},
        {"value": "ja-JP-KeitaNeural", "label": "Keita (男性) [Edge TTS]", "engine": "edge"},
    ],
    "zh": [
        {"value": "zh-CN-XiaoxiaoNeural", "label": "晓晓 (女, 普通话) [Edge TTS]", "engine": "edge"},
        {"value": "zh-CN-YunxiNeural", "label": "云希 (男, 普通话) [Edge TTS]", "engine": "edge"},
        {"value": "zh-TW-HsiaoChenNeural", "label": "曉臻 (女, 台灣) [Edge TTS]", "engine": "edge"},
    ],
    "ko": [
        {"value": "ko-KR-SunHiNeural", "label": "선히 (여성) [Edge TTS]", "engine": "edge"},
        {"value": "ko-KR-InJoonNeural", "label": "인준 (남성) [Edge TTS]", "engine": "edge"},
    ],
    "fr": [
        {"value": "fr-FR-DeniseNeural", "label": "Denise (Femme, FR) [Edge TTS]", "engine": "edge"},
        {"value": "fr-FR-HenriNeural", "label": "Henri (Homme, FR) [Edge TTS]", "engine": "edge"},
        {"value": "fr-CA-SylvieNeural", "label": "Sylvie (Femme, CA) [Edge TTS]", "engine": "edge"},
    ],
    "de": [
        {"value": "de-DE-KatjaNeural", "label": "Katja (Weiblich) [Edge TTS]", "engine": "edge"},
        {"value": "de-DE-ConradNeural", "label": "Conrad (Männlich) [Edge TTS]", "engine": "edge"},
    ],
}


def get_all_voices() -> Dict[str, List[Dict[str, str]]]:
    """Get all voices including VieNeu voices (loaded once)."""
    global _VIENEU_VOICES
    if not _VIENEU_VOICES:
        _VIENEU_VOICES = _load_vieneu_voices()
        # Add VieNeu voices to Vietnamese
        if _VIENEU_VOICES:
            if "vi" not in EDGE_VOICES:
                EDGE_VOICES["vi"] = []
            EDGE_VOICES["vi"].extend(_VIENEU_VOICES)
    return EDGE_VOICES

GTTS_LANG_MAP: Dict[str, str] = {
    "vi": "vi",
    "en": "en",
    "ja": "ja",
    "zh": "zh-CN",
    "ko": "ko",
    "fr": "fr",
    "de": "de",
}

SUPPORTED_LANGUAGES = list(EDGE_VOICES.keys())
CJK_LANGUAGES = {"ja", "zh", "ko"}

# ---------------------------------------------------------------------------
# Chunking profiles
# ---------------------------------------------------------------------------
DEFAULT_CHUNK_SIZES = [120, 300, 600, 1500, 3500]
DEFAULT_HARD_MAX_CHUNK = 3500
DEFAULT_MAX_SENTENCES_PER_CHUNK = [2, 4, 8, 15, 50]

CJK_CHUNK_SIZES = [60, 150, 300, 800, 2000]
CJK_HARD_MAX_CHUNK = 2000
CJK_MAX_SENTENCES_PER_CHUNK = [2, 4, 8, 12, 30]

MULTILANG_SENTENCE_RE = re.compile(
    r".+?(?:[.!?…。！？]+(?:[\"'”’»」』）】]*)|$)",
    re.S,
)
PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+", re.M)
SOFT_SPLIT_RE = re.compile(r"(?<=[,;:，、；：])")
SENTENCE_END_RE = re.compile(r"[.!?…。！？][\"'”’»」』）】]*$")

# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------
class TTSRequest(BaseModel):
    text: str = ""  # Make optional when using source_chapters
    source_chapters: Optional[List[Dict[str, str]]] = None  # NEW
    voice: str = "vi-VN-HoaiMyNeural"
    engine: str = "edge"   # edge | gtts
    language: str = "vi"
    audio_quality: AudioQuality = "standard"
    model: Optional[Literal["v3_turbo", "v3_turbo_int8"]] = None


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def md5_short(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def get_cache_id(text: str, voice: str, engine: str, language: str, variant: str = "") -> str:
    raw = f"{text}_{voice}_{engine}_{language}"
    if variant:
        raw = f"{raw}_{variant}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def estimate_conversion_seconds(
    text: str,
    engine: str,
    model: Optional[str] = None,
    chunk_count: int = 1,
) -> int:
    """Return a conservative conversion estimate for an average CPU server."""
    chars_per_second = {
        "edge": 45.0,
        "gtts": 35.0,
        "v3_turbo": 2.5,
        "v3_turbo_int8": 7.5,
    }
    profile = model if engine == "vieneu" else engine
    rate = chars_per_second.get(profile or "v3_turbo", 2.5)
    startup_seconds = 8 if engine == "vieneu" else max(2, chunk_count)
    return max(3, math.ceil(len(text) / rate + startup_seconds))


def get_audio_path(cache_id: str, extension: str = "mp3") -> str:
    return os.path.join(CACHE_DIR, f"{cache_id}.{extension}")


def get_meta_path(cache_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{cache_id}.json")


def get_srt_path(cache_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{cache_id}.srt")


def get_vtt_path(cache_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{cache_id}.vtt")


def get_cues_json_path(cache_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{cache_id}.cues.json")


def get_cues_jsonl_path(cache_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{cache_id}.cues.jsonl")


def save_cache_meta(cache_id: str, data: dict) -> None:
    meta_path = get_meta_path(cache_id)
    tmp_path = f"{meta_path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp_path, meta_path)


def load_cache_meta(cache_id: str) -> Optional[dict]:
    meta_path = get_meta_path(cache_id)
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def remove_if_exists(path: str) -> bool:
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except OSError as exc:
            logger.warning("Không thể xóa file %s: %s", path, exc)
            return False
    return True


def cleanup_incomplete_cache(cache_id: str) -> None:
    """Xóa toàn bộ file cache (audio, meta, subtitle, cues). Best-effort."""
    paths = [
        get_audio_path(cache_id),
        get_audio_path(cache_id, "wav"),
        get_meta_path(cache_id),
        get_srt_path(cache_id),
        get_vtt_path(cache_id),
        get_cues_json_path(cache_id),
        get_cues_jsonl_path(cache_id),
    ]
    failed = [p for p in paths if not remove_if_exists(p)]
    if failed:
        logger.warning(
            "cleanup_incomplete_cache(%s): không thể xóa %d file: %s",
            cache_id, len(failed), failed,
        )


def cleanup_old_audio_cache(retention_hours: int = AUDIO_CACHE_RETENTION_HOURS) -> int:
    """Delete completed/failed audio cache entries older than retention_hours."""
    if not os.path.exists(CACHE_DIR):
        return 0

    cutoff = time.time() - retention_hours * 3600
    cache_ids = set()
    deleted = 0

    for filename in os.listdir(CACHE_DIR):
        match = re.match(r"^([a-f0-9]{32})(?:\.|$)", filename)
        if match:
            cache_ids.add(match.group(1))
            continue

        path = os.path.join(CACHE_DIR, filename)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff and remove_if_exists(path):
                deleted += 1
        except OSError:
            continue

    for cache_id in cache_ids:
        meta = load_cache_meta(cache_id) or {}
        if meta.get("status") in {"queued", "processing", "generating"}:
            continue

        paths = [
            get_audio_path(cache_id),
            get_audio_path(cache_id, "wav"),
            get_meta_path(cache_id),
            get_srt_path(cache_id),
            get_vtt_path(cache_id),
            get_cues_json_path(cache_id),
            get_cues_jsonl_path(cache_id),
        ]
        existing = [path for path in paths if os.path.exists(path)]
        if not existing:
            continue

        try:
            newest_mtime = max(os.path.getmtime(path) for path in existing)
        except OSError:
            continue
        if newest_mtime >= cutoff:
            continue

        before = sum(1 for path in existing if os.path.exists(path))
        cleanup_incomplete_cache(cache_id)
        after = sum(1 for path in existing if os.path.exists(path))
        deleted += before - after

    return deleted


def remove_runtime_files_only(cache_id: str) -> None:
    """Xóa file runtime (audio, subtitle, cues) nhưng giữ meta lại. Best-effort."""
    paths = [
        get_audio_path(cache_id),
        get_srt_path(cache_id),
        get_vtt_path(cache_id),
        get_cues_json_path(cache_id),
        get_cues_jsonl_path(cache_id),
    ]
    failed = [p for p in paths if not remove_if_exists(p)]
    if failed:
        logger.warning(
            "remove_runtime_files_only(%s): không thể xóa %d file: %s",
            cache_id, len(failed), failed,
        )


def is_cache_valid(cache_id: str, require_subtitles: bool = False) -> bool:
    audio_path = get_audio_path(cache_id)
    if not os.path.exists(audio_path):
        return False

    meta = load_cache_meta(cache_id)
    if not meta or meta.get("status") != "completed":
        return False

    expected_size = meta.get("file_size")
    if expected_size is None:
        return False

    try:
        actual_size = os.path.getsize(audio_path)
    except OSError:
        return False

    if actual_size <= 0 or actual_size != expected_size:
        return False

    if require_subtitles and meta.get("engine") == "edge":
        if not (
            os.path.exists(get_srt_path(cache_id))
            and os.path.exists(get_vtt_path(cache_id))
            and os.path.exists(get_cues_json_path(cache_id))
        ):
            return False

    return True


def add_remaining_time(status: dict) -> dict:
    estimate = status.get("estimated_seconds")
    if estimate is None:
        return status

    if status.get("status") in {"completed", "failed", "stopped"}:
        status["remaining_seconds"] = 0
        return status

    started_at = status.get("started_at")
    if not started_at:
        status["remaining_seconds"] = estimate
        return status

    elapsed = max(0.0, time.time() - float(started_at))
    progress = int(status.get("progress") or 0)
    total = int(status.get("total") or 0)
    if 0 < progress < total:
        remaining = elapsed / progress * (total - progress)
    else:
        remaining = float(estimate) - elapsed
    status["remaining_seconds"] = max(1, math.ceil(remaining))
    return status


def get_effective_status(cache_id: str) -> Optional[dict]:
    audio_path = get_audio_path(cache_id)
    file_size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0

    in_mem = generation_status.get(cache_id)
    if in_mem and in_mem.get("status") in {"queued", "processing", "generating", "stopped"}:
        status = dict(in_mem)
        status["file_size"] = file_size
        return add_remaining_time(status)

    meta = load_cache_meta(cache_id)
    if meta:
        status = dict(meta)
        status["file_size"] = file_size
        return add_remaining_time(status)

    if is_cache_valid(cache_id):
        return add_remaining_time({
            "status": "completed",
            "progress": 1,
            "total": 1,
            "file_size": file_size,
        })

    return None


def strip_id3v2(data: bytes) -> bytes:
    if len(data) >= 10 and data[:3] == b"ID3":
        size = (
            ((data[6] & 0x7F) << 21)
            | ((data[7] & 0x7F) << 14)
            | ((data[8] & 0x7F) << 7)
            | (data[9] & 0x7F)
        )
        return data[size + 10:]
    return data


def normalize_text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def is_cjk_script_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3040 <= code <= 0x309F
        or 0x30A0 <= code <= 0x30FF
        or 0xFF66 <= code <= 0xFF9D
        or 0xAC00 <= code <= 0xD7AF
        or 0x1100 <= code <= 0x11FF
    )


def char_weight(ch: str) -> int:
    if ch in "\r\n":
        return 0
    if ch.isspace():
        return 1

    east = unicodedata.east_asian_width(ch)
    if east in {"W", "F"} or is_cjk_script_char(ch):
        return 2
    return 1


def effective_len(text: str) -> int:
    return sum(char_weight(ch) for ch in (text or ""))


def is_cjk_language(language: str) -> bool:
    return (language or "").lower().strip() in CJK_LANGUAGES


def is_mostly_cjk(text: str, threshold: float = 0.25) -> bool:
    chars = [ch for ch in (text or "") if not ch.isspace()]
    if not chars:
        return False
    cjk_count = sum(1 for ch in chars if is_cjk_script_char(ch))
    return (cjk_count / len(chars)) >= threshold


def get_chunk_profile(language: str, text: str) -> Tuple[List[int], int, List[int]]:
    if is_cjk_language(language) or is_mostly_cjk(text):
        return CJK_CHUNK_SIZES, CJK_HARD_MAX_CHUNK, CJK_MAX_SENTENCES_PER_CHUNK
    return DEFAULT_CHUNK_SIZES, DEFAULT_HARD_MAX_CHUNK, DEFAULT_MAX_SENTENCES_PER_CHUNK


def smart_join(left: str, right: str) -> str:
    left = (left or "").strip()
    right = (right or "").strip()
    if not left:
        return right
    if not right:
        return left
    if left[-1].isspace() or right[0].isspace():
        return left + right
    if is_cjk_script_char(left[-1]) or is_cjk_script_char(right[0]):
        return left + right
    return f"{left} {right}"


def split_paragraphs(text: str) -> List[str]:
    text = normalize_text(text)
    if not text:
        return []
    return [p.strip() for p in PARAGRAPH_SPLIT_RE.split(text) if p.strip()]


def split_sentences_multilang(text: str) -> List[str]:
    text = normalize_text(text)
    if not text:
        return []
    matches = [m.group().strip() for m in MULTILANG_SENTENCE_RE.finditer(text)]
    sentences = [s for s in matches if s]
    return sentences or [text]


def hard_cut_text(text: str, limit: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []

    pieces: List[str] = []
    current_chars: List[str] = []
    current_weight = 0

    for ch in text:
        w = char_weight(ch)
        if current_chars and current_weight + w > limit:
            piece = "".join(current_chars).strip()
            if piece:
                pieces.append(piece)
            current_chars = [ch]
            current_weight = w
        else:
            current_chars.append(ch)
            current_weight += w

    if current_chars:
        piece = "".join(current_chars).strip()
        if piece:
            pieces.append(piece)

    return pieces


def pack_units_by_limit(units: List[str], limit: int) -> List[str]:
    chunks: List[str] = []
    current = ""

    for unit in units:
        unit = (unit or "").strip()
        if not unit:
            continue

        if not current:
            if effective_len(unit) <= limit:
                current = unit
            else:
                chunks.extend(hard_cut_text(unit, limit))
            continue

        candidate = smart_join(current, unit)
        if effective_len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current.strip())
            if effective_len(unit) <= limit:
                current = unit
            else:
                sub = hard_cut_text(unit, limit)
                if sub:
                    chunks.extend(sub[:-1])
                    current = sub[-1]
                else:
                    current = ""

    if current.strip():
        chunks.append(current.strip())
    return chunks


def split_long_text_gently_by_space(text: str, limit: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if effective_len(text) <= limit:
        return [text]

    tokens = re.findall(r"\S+\s*", text)
    if not tokens:
        return hard_cut_text(text, limit)

    chunks: List[str] = []
    current = ""

    for token in tokens:
        token = token.strip()
        if not token:
            continue

        if not current:
            if effective_len(token) <= limit:
                current = token
            else:
                chunks.extend(hard_cut_text(token, limit))
            continue

        candidate = smart_join(current, token)
        if effective_len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current.strip())
            if effective_len(token) <= limit:
                current = token
            else:
                sub = hard_cut_text(token, limit)
                if sub:
                    chunks.extend(sub[:-1])
                    current = sub[-1]
                else:
                    current = ""

    if current.strip():
        chunks.append(current.strip())
    return chunks


def split_long_text_gently(text: str, limit: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if effective_len(text) <= limit:
        return [text]

    parts = [p.strip() for p in SOFT_SPLIT_RE.split(text) if p.strip()]
    if len(parts) > 1:
        packed = pack_units_by_limit(parts, limit)
        final_chunks: List[str] = []
        for ch in packed:
            if effective_len(ch) <= limit:
                final_chunks.append(ch)
            else:
                final_chunks.extend(split_long_text_gently_by_space(ch, limit))
        return [c for c in final_chunks if c]

    by_space = split_long_text_gently_by_space(text, limit)
    final_chunks: List[str] = []
    for ch in by_space:
        if effective_len(ch) <= limit:
            final_chunks.append(ch)
        else:
            final_chunks.extend(hard_cut_text(ch, limit))
    return [c for c in final_chunks if c]


def split_text_into_chunks(text: str, language: str = "vi") -> List[str]:
    text = normalize_text(text)
    if not text:
        return []

    chunk_sizes, hard_max, max_sentences_profile = get_chunk_profile(language, text)
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: List[str] = []
    size_idx = 0
    current = ""
    sentence_count = 0

    for para in paragraphs:
        sentences = split_sentences_multilang(para)
        if not sentences:
            continue

        normalized_units: List[str] = []
        for sentence in sentences:
            if effective_len(sentence) > hard_max:
                normalized_units.extend(split_long_text_gently(sentence, hard_max))
            else:
                normalized_units.append(sentence)

        for unit_idx, unit in enumerate(normalized_units):
            max_len = chunk_sizes[min(size_idx, len(chunk_sizes) - 1)]
            max_sentences = max_sentences_profile[min(size_idx, len(max_sentences_profile) - 1)]

            if not current:
                if effective_len(unit) <= max_len:
                    current = unit
                    sentence_count = 1
                else:
                    sub = split_long_text_gently(unit, max_len)
                    if sub:
                        current = sub[0]
                        sentence_count = 1
                        for rest in sub[1:]:
                            chunks.append(current.strip())
                            size_idx += 1
                            current = rest
                            sentence_count = 1
                continue

            if unit_idx == 0:
                candidate = current + "\n\n" + unit
            else:
                candidate = smart_join(current, unit)

            candidate_sentences = sentence_count + 1
            if effective_len(candidate) <= max_len and candidate_sentences <= max_sentences:
                current = candidate
                sentence_count = candidate_sentences
            else:
                chunks.append(current.strip())
                size_idx += 1
                max_len = chunk_sizes[min(size_idx, len(chunk_sizes) - 1)]
                if effective_len(unit) <= max_len:
                    current = unit
                    sentence_count = 1
                else:
                    sub = split_long_text_gently(unit, max_len)
                    if sub:
                        current = sub[0]
                        sentence_count = 1
                        for rest in sub[1:]:
                            chunks.append(current.strip())
                            size_idx += 1
                            current = rest
                            sentence_count = 1

    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c.strip()]


def split_text_for_engine(text: str, engine: str, language: str = "vi") -> List[str]:
    """Return app-level chunks; VieNeu handles its own TTS chunking internally."""
    text = normalize_text(text)
    if not text:
        return []
    if engine == "vieneu":
        return [text]
    return split_text_into_chunks(text, language=language)


def validate_language(language: str) -> str:
    language = (language or "vi").lower().strip()
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {language}")
    return language


def validate_engine(engine: str) -> str:
    engine = (engine or "edge").lower().strip()
    if engine not in {"edge", "gtts", "vieneu"}:
        raise HTTPException(status_code=400, detail="Unsupported engine")
    return engine


def get_engine_from_voice(voice: str, default_engine: str = "edge") -> str:
    """Auto-detect engine from selected voice ID."""
    if voice.startswith("vieneu:"):
        return "vieneu"
    return default_engine


def validate_voice(language: str, voice: str, engine: str) -> str:
    if engine == "gtts":
        return ""
    if engine == "vieneu":
        # VieNeu voices are validated differently (preset lookup)
        voice = (voice or "vieneu:default").strip()
        if not voice.startswith("vieneu:"):
            raise HTTPException(
                status_code=400,
                detail=f"VieNeu voice must start with 'vieneu:'",
            )
        preset_id = voice.split(":", 1)[1]
        if preset_id != "default":
            from vieneu_model import get_preset_voices_from_file

            valid = {name for _desc, name in get_preset_voices_from_file()}
            if preset_id not in valid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported VieNeu voice: {preset_id}",
                )
        return voice

    all_voices = get_all_voices()
    available = all_voices.get(language, [])
    if not available:
        raise HTTPException(
            status_code=400,
            detail=f"No voices available for selected language: {language}",
        )

    voice = (voice or "").strip()
    valid_voices = {v["value"] for v in available}
    if voice not in valid_voices:
        raise HTTPException(
            status_code=400,
            detail=f"Voice does not belong to selected language: {language}",
        )
    return voice


# ---------------------------------------------------------------------------
# Subtitle helpers
# ---------------------------------------------------------------------------
def ticks_to_seconds(ticks: int) -> float:
    return max(0.0, float(ticks) / 10_000_000.0)


def smart_join_tokens(parts: List[str]) -> str:
    out = ""
    for part in parts:
        token = (part or "").strip()
        if not token:
            continue
        out = smart_join(out, token) if out else token
    return out.strip()


def group_word_boundaries_to_cues(
    word_boundaries: List[dict],
    base_offset_sec: float,
    language: str,
) -> List[dict]:
    """
    Gom WordBoundary thành cue dễ đọc hơn:
    - Latin: tối đa 8 từ / cue
    - CJK: tối đa 12 token / cue
    - ngắt khi gặp dấu kết câu hoặc quá dài
    """
    if not word_boundaries:
        return []

    is_cjk = is_cjk_language(language)
    max_words = 12 if is_cjk else 8
    max_duration = 3.8 if is_cjk else 4.2

    cues: List[dict] = []
    current_words: List[dict] = []

    def flush_current() -> None:
        nonlocal current_words, cues
        if not current_words:
            return

        start = base_offset_sec + current_words[0]["start"]
        end = base_offset_sec + current_words[-1]["end"]
        text = smart_join_tokens([w["text"] for w in current_words])

        if text:
            end = max(end, start + 0.08)
            cues.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
            })
        current_words = []

    for word in word_boundaries:
        current_words.append(word)

        cue_start = current_words[0]["start"]
        cue_end = current_words[-1]["end"]
        duration = cue_end - cue_start
        token = (word.get("text") or "").strip()
        sentence_end = bool(token and SENTENCE_END_RE.search(token))

        if len(current_words) >= max_words or duration >= max_duration or sentence_end:
            flush_current()

    flush_current()
    return cues


def format_srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_vtt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def cues_to_srt(cues: List[dict]) -> str:
    lines: List[str] = []
    for idx, cue in enumerate(cues, start=1):
        lines.append(str(idx))
        lines.append(f"{format_srt_time(cue['start'])} --> {format_srt_time(cue['end'])}")
        lines.append(cue["text"])
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def cues_to_vtt(cues: List[dict]) -> str:
    lines: List[str] = ["WEBVTT", ""]
    for cue in cues:
        lines.append(f"{format_vtt_time(cue['start'])} --> {format_vtt_time(cue['end'])}")
        lines.append(cue["text"])
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_json_atomic(path: str, data: object) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def write_text_atomic(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def append_cues_jsonl(cache_id: str, cues: List[dict]) -> None:
    if not cues:
        return
    path = get_cues_jsonl_path(cache_id)
    with open(path, "a", encoding="utf-8") as f:
        for cue in cues:
            f.write(json.dumps(cue, ensure_ascii=False) + "\n")


def load_cues_json(cache_id: str) -> List[dict]:
    path = get_cues_json_path(cache_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


# ---------------------------------------------------------------------------
# MP3 duration helper
# Dùng để cộng global time offset giữa các chunk cho subtitle.
# ---------------------------------------------------------------------------
BITRATES = {
    (3, 1): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    (3, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0],
    (3, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    (2, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    (0, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0],
    (0, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    (0, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
}
SAMPLE_RATES = {
    3: [44100, 48000, 32000, 0],
    2: [22050, 24000, 16000, 0],
    0: [11025, 12000, 8000, 0],
}


def skip_id3v2_len(data: bytes) -> int:
    if len(data) >= 10 and data[:3] == b"ID3":
        size = (
            ((data[6] & 0x7F) << 21)
            | ((data[7] & 0x7F) << 14)
            | ((data[8] & 0x7F) << 7)
            | (data[9] & 0x7F)
        )
        return size + 10
    return 0


def mp3_duration_seconds(data: bytes) -> float:
    if not data:
        return 0.0

    pos = skip_id3v2_len(data)
    total = 0.0
    data_len = len(data)

    while pos + 4 <= data_len:
        b1, b2, b3, b4 = data[pos], data[pos + 1], data[pos + 2], data[pos + 3]
        if b1 != 0xFF or (b2 & 0xE0) != 0xE0:
            pos += 1
            continue

        version_bits = (b2 >> 3) & 0x03
        layer_bits = (b2 >> 1) & 0x03
        bitrate_idx = (b3 >> 4) & 0x0F
        sample_rate_idx = (b3 >> 2) & 0x03
        padding = (b3 >> 1) & 0x01

        if version_bits == 1 or layer_bits == 0 or bitrate_idx in {0, 15} or sample_rate_idx == 3:
            pos += 1
            continue

        version_map = {0: 0, 2: 2, 3: 3}
        version = version_map.get(version_bits)
        layer = 4 - layer_bits
        if version is None:
            pos += 1
            continue

        bitrate_table = BITRATES.get((version, layer))
        sample_rates = SAMPLE_RATES.get(version)
        if not bitrate_table or not sample_rates:
            pos += 1
            continue

        bitrate_kbps = bitrate_table[bitrate_idx]
        sample_rate = sample_rates[sample_rate_idx]
        if bitrate_kbps <= 0 or sample_rate <= 0:
            pos += 1
            continue

        if layer == 1:
            samples_per_frame = 384
            frame_length = int((12 * bitrate_kbps * 1000 / sample_rate + padding) * 4)
        elif layer == 2:
            samples_per_frame = 1152
            frame_length = int(144 * bitrate_kbps * 1000 / sample_rate + padding)
        else:
            if version == 3:
                samples_per_frame = 1152
                frame_length = int(144 * bitrate_kbps * 1000 / sample_rate + padding)
            else:
                samples_per_frame = 576
                frame_length = int(72 * bitrate_kbps * 1000 / sample_rate + padding)

        if frame_length <= 0:
            pos += 1
            continue

        total += samples_per_frame / sample_rate
        pos += frame_length

    return round(total, 6)


# ---------------------------------------------------------------------------
# Audio engine helpers
# ---------------------------------------------------------------------------
async def edge_tts_to_audio_and_words(text: str, voice: str) -> Tuple[bytes, List[dict]]:
    """
    Trả về:
    - audio bytes
    - danh sách WordBoundary / SentenceBoundary đã chuẩn hóa thành start/end/text
    """
    communicate = edge_tts.Communicate(text, voice, proxy=PROXY)
    audio_parts: List[bytes] = []
    words: List[dict] = []

    async for chunk in communicate.stream():
        chunk_type = chunk.get("type")
        if chunk_type == "audio":
            audio_parts.append(chunk["data"])
        elif chunk_type in {"WordBoundary", "SentenceBoundary"}:
            try:
                start_sec = ticks_to_seconds(int(chunk.get("offset", 0)))
                duration_sec = ticks_to_seconds(int(chunk.get("duration", 0)))
                text_part = (chunk.get("text") or "").strip()
                if text_part:
                    words.append({
                        "type": chunk_type,
                        "text": text_part,
                        "start": start_sec,
                        "end": start_sec + max(0.01, duration_sec),
                    })
            except Exception:
                continue

    return b"".join(audio_parts), words


def resolve_vieneu_voice_arg(tts, voice: str):
    """Return an SDK voice argument for VieNeu v3/v2, or None for default."""
    if not voice or voice == "vieneu:default":
        return None

    preset_id = voice.split(":", 1)[1] if voice.startswith("vieneu:") else voice
    if not preset_id:
        return None

    try:
        available = tts.list_preset_voices()
        for desc, name in available:
            if preset_id in {desc, name}:
                try:
                    return tts.get_preset_voice(name)
                except Exception:
                    return name
    except Exception:
        pass

    # VieNeu v3 Turbo accepts built-in voice names directly, e.g. "Ngọc Lan".
    return preset_id


async def vieneu_tts_to_audio(
    text: str,
    voice: str,
    audio_quality: AudioQuality = "standard",
    add_natural_pauses: bool = True,
    pause_duration_ms: int = 300,
    model: Optional[str] = None,
) -> tuple[bytes, str]:
    """
    Generate audio using VieNeu-TTS with quality enhancements.

    Args:
        text: Text to synthesize
        voice: Voice ID (e.g., "vieneu:default" or "vieneu:{preset_id}")
        audio_quality: "standard" (128k), "high" (192k), or "lossless" (WAV)
        add_natural_pauses: Whether to add fade effects for smoother audio
        pause_duration_ms: Silence to append (for internal chunk processing)

    Returns:
        Tuple of (audio_bytes, file_extension)
    """
    from vieneu_model import get_vieneu_model

    # Run in thread pool since VieNeu is synchronous
    loop = asyncio.get_running_loop()

    def _generate():
        # Acquire model lock for inference
        with get_vieneu_model(model) as tts:
            try:
                voice_arg = resolve_vieneu_voice_arg(tts, voice)
                if voice_arg:
                    audio_array = tts.infer(text=text, voice=voice_arg)
                else:
                    audio_array = tts.infer(text=text)

                # Process audio with quality enhancements (includes MP3 conversion)
                return process_vienneu_audio(
                    audio_array=audio_array,
                    audio_quality=audio_quality,
                    apply_normalization=True,
                    apply_fade=add_natural_pauses,
                    fade_ms=10,
                    silence_ms=pause_duration_ms if add_natural_pauses else 0,
                )
            except Exception as e:
                raise RuntimeError(f"VieNeu TTS failed: {e}")

    return await loop.run_in_executor(None, _generate)


def vieneu_tts_to_audio_sync(
    text: str,
    voice: str,
    audio_quality: AudioQuality = "standard",
    add_natural_pauses: bool = True,
    pause_duration_ms: int = 300,
    model: Optional[str] = None,
) -> tuple[bytes, str]:
    """
    Synchronous wrapper for VieNeu TTS with quality enhancements.
    For use with ThreadPoolExecutor.

    Args:
        text: Text to synthesize
        voice: Voice ID (e.g., "vieneu:default" or "vieneu:{preset_id}")
        audio_quality: "standard" (128k), "high" (192k), or "lossless" (WAV)
        add_natural_pauses: Whether to add fade effects for smoother audio
        pause_duration_ms: Silence to append (for internal chunk processing)

    Returns:
        Tuple of (audio_bytes, file_extension)
    """
    from vieneu_model import get_vieneu_model

    # Acquire model lock for inference
    with get_vieneu_model(model) as tts:
        try:
            voice_arg = resolve_vieneu_voice_arg(tts, voice)
            if voice_arg:
                audio_array = tts.infer(text=text, voice=voice_arg)
            else:
                audio_array = tts.infer(text=text)

            # Process audio with quality enhancements
            return process_vienneu_audio(
                audio_array=audio_array,
                audio_quality=audio_quality,
                apply_normalization=True,
                apply_fade=add_natural_pauses,
                fade_ms=10,
                silence_ms=pause_duration_ms if add_natural_pauses else 0,
            )
        except Exception as e:
            raise RuntimeError(f"VieNeu TTS failed: {e}")


def gtts_to_bytes(text: str, lang: str = "vi") -> bytes:
    last_exc = None
    for attempt in range(3):
        try:
            tts = gTTS(text=text, lang=lang, timeout=15)
            buf = io.BytesIO()
            tts.write_to_fp(buf)
            return buf.getvalue()
        except Exception as exc:
            last_exc = exc
            error_type = type(exc).__name__
            print(f"[WARN] gTTS attempt {attempt + 1} failed with {error_type}: {exc}")
            if attempt < 2:
                if any(x in error_type.lower() for x in ("socket", "connection", "timeout")):
                    delay = 3 * (attempt + 1)
                else:
                    delay = 2 * (attempt + 1)
                time.sleep(delay)

    raise last_exc


# ---------------------------------------------------------------------------
# Background generation
# ---------------------------------------------------------------------------
def generate_chunks_sync(
    text: str,
    voice: str,
    engine: str,
    cache_id: str,
    language: str = "vi",
    chunks: Optional[List[str]] = None,
    audio_quality: AudioQuality = "standard",
    add_natural_pauses: bool = True,
    pause_duration_ms: int = 300,
    model: Optional[str] = None,
):
    """
    Synchronous wrapper for generate_chunks to use with BackgroundTasks.

    FastAPI BackgroundTasks runs tasks in a thread pool, which doesn't have
    an active event loop. This wrapper creates a new event loop to run the
    async generate_chunks function.
    """
    thread_id = threading.get_ident()
    print(f"[BG-TASK] Starting generate_chunks_sync for {cache_id} in thread {thread_id}")
    logger.info(f"[BG-TASK] Starting generate_chunks_sync for {cache_id} in thread {thread_id}")

    # Create a new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # Run the async function in the new loop
        loop.run_until_complete(
            generate_chunks(
                text=text,
                voice=voice,
                engine=engine,
                cache_id=cache_id,
                language=language,
                chunks=chunks,
                audio_quality=audio_quality,
                add_natural_pauses=add_natural_pauses,
                pause_duration_ms=pause_duration_ms,
                model=model,
            )
        )
        print(f"[BG-TASK] Completed generate_chunks for {cache_id}")
        logger.info(f"[BG-TASK] Completed generate_chunks for {cache_id}")
    except Exception as e:
        print(f"[BG-TASK] Error for {cache_id}: {e}")
        traceback.print_exc()
        logger.error(f"[BG-TASK] Error for {cache_id}: {e}\n{traceback.format_exc()}")
    finally:
        # Clean up the loop
        loop.close()


async def generate_chunks(
    text: str,
    voice: str,
    engine: str,
    cache_id: str,
    language: str = "vi",
    chunks: Optional[List[str]] = None,
    audio_quality: AudioQuality = "standard",
    add_natural_pauses: bool = True,
    pause_duration_ms: int = 300,
    model: Optional[str] = None,
):
    if cache_id not in _generation_locks:
        _generation_locks[cache_id] = asyncio.Lock()

    async with _generation_locks[cache_id]:
        audio_path = get_audio_path(cache_id)
        cues_all: List[dict] = []
        global_audio_sec = 0.0
        cue_index = 0

        require_subtitles = engine == "edge"
        subtitle_supported = engine == "edge"
        if is_cache_valid(cache_id, require_subtitles=require_subtitles):
            generation_status[cache_id] = {
                "status": "completed",
                "progress": 1,
                "total": 1,
                "subtitle_supported": subtitle_supported,
                "subtitle_ready": subtitle_supported,
            }
            return

        if chunks is None:
            chunks = split_text_for_engine(text, engine=engine, language=language)

        if not chunks:
            err = "Text must not be empty"
            save_cache_meta(
                cache_id,
                {
                    "status": "failed",
                    "error": err,
                    "text_hash": md5_short(text),
                    "voice": voice,
                    "engine": engine,
                    "model": model if engine == "vieneu" else None,
                    "language": language,
                    "subtitle_supported": engine == "edge",
                    "subtitle_ready": False,
                },
            )
            generation_status.pop(cache_id, None)
            return

        total = len(chunks)
        timing_meta = {
            "estimated_seconds": estimate_conversion_seconds(text, engine, model, total),
            "started_at": time.time(),
        }

        generation_status[cache_id] = {
            **timing_meta,
            "status": "processing",
            "progress": 0,
            "total": total,
            "subtitle_supported": engine == "edge",
            "subtitle_ready": False,
            "subtitle_cues": 0,
        }
        save_cache_meta(
            cache_id,
            {
                **timing_meta,
                "status": "processing",
                "progress": 0,
                "total": total,
                "text_hash": md5_short(text),
                "voice": voice,
                "engine": engine,
                "model": model if engine == "vieneu" else None,
                "language": language,
                "subtitle_supported": engine == "edge",
                "subtitle_ready": False,
                "subtitle_cues": 0,
            },
        )

        # Xóa file runtime cũ nhưng giữ meta vừa save
        remove_runtime_files_only(cache_id)

        loop = asyncio.get_running_loop()

        try:
            for i, chunk_text in enumerate(chunks):
                # Check for cancellation before processing chunk
                if is_cancelled(cache_id):
                    save_cache_meta(
                        cache_id,
                        {
                            **timing_meta,
                            "status": "stopped",
                            "progress": i,
                            "total": total,
                            "text_hash": md5_short(text),
                            "voice": voice,
                            "engine": engine,
                            "model": model if engine == "vieneu" else None,
                            "language": language,
                            "subtitle_supported": engine == "edge",
                            "subtitle_ready": False,
                            "subtitle_cues": 0,
                        },
                    )
                    generation_status.pop(cache_id, None)
                    return

                # Update progress before generation (better UX feedback)
                generation_status[cache_id]["progress"] = i
                generation_status[cache_id]["status"] = "generating"
                save_cache_meta(
                    cache_id,
                    {
                        **timing_meta,
                        "status": "generating",
                        "progress": i,
                        "total": total,
                        "text_hash": md5_short(text),
                        "voice": voice,
                        "engine": engine,
                        "model": model if engine == "vieneu" else None,
                        "language": language,
                        "subtitle_supported": engine == "edge",
                        "subtitle_ready": False,
                        "subtitle_cues": 0,
                    },
                )

                if engine == "edge":
                    audio, words = await edge_tts_to_audio_and_words(chunk_text, voice)
                elif engine == "vieneu":
                    # Use sync version directly in thread pool for better performance
                    audio, ext = await loop.run_in_executor(
                        None,
                        vieneu_tts_to_audio_sync,
                        chunk_text,
                        voice,
                        audio_quality,
                        add_natural_pauses,
                        pause_duration_ms if i < total - 1 else 0,  # No pause on last chunk
                        model,
                    )
                    words = []  # VieNeu doesn't provide word-level timing
                else:  # gtts
                    gtts_lang = GTTS_LANG_MAP.get(language, "en")
                    audio = await loop.run_in_executor(None, gtts_to_bytes, chunk_text, gtts_lang)
                    words = []

                if not audio:
                    raise RuntimeError(f"Empty audio returned for chunk {i + 1}/{total}")

                raw_for_duration = audio
                if i > 0:
                    audio = strip_id3v2(audio)
                    raw_for_duration = audio

                with open(audio_path, "ab") as f:
                    f.write(audio)
                    f.flush()

                chunk_duration = mp3_duration_seconds(raw_for_duration)
                if chunk_duration <= 0:
                    if words:
                        chunk_duration = max((w["end"] for w in words), default=0.0)
                    if chunk_duration <= 0:
                        chunk_duration = 0.05

                if engine == "edge":
                    new_cues = group_word_boundaries_to_cues(words, global_audio_sec, language)
                    for cue in new_cues:
                        cue_index += 1
                        cue["index"] = cue_index

                    cues_all.extend(new_cues)
                    append_cues_jsonl(cache_id, new_cues)
                    generation_status[cache_id]["subtitle_cues"] = len(cues_all)

                global_audio_sec += chunk_duration

                current_size = os.path.getsize(audio_path)
                generation_status[cache_id]["progress"] = i + 1

                save_cache_meta(
                    cache_id,
                    {
                        **timing_meta,
                        "status": "processing",
                        "progress": i + 1,
                        "total": total,
                        "current_file_size": current_size,
                        "text_hash": md5_short(text),
                        "voice": voice,
                        "engine": engine,
                        "model": model if engine == "vieneu" else None,
                        "language": language,
                        "subtitle_supported": engine == "edge",
                        "subtitle_ready": False,
                        "subtitle_cues": len(cues_all),
                    },
                )

            final_size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0
            if final_size <= 0:
                raise RuntimeError("Generated audio file is empty")

            subtitle_ready = False
            if engine == "edge":
                write_json_atomic(get_cues_json_path(cache_id), cues_all)
                write_text_atomic(get_srt_path(cache_id), cues_to_srt(cues_all))
                write_text_atomic(get_vtt_path(cache_id), cues_to_vtt(cues_all))
                subtitle_ready = True
            # VieNeu and gTTS don't support subtitles

            save_cache_meta(
                cache_id,
                {
                    **timing_meta,
                    "status": "completed",
                    "progress": total,
                    "total": total,
                    "file_size": final_size,
                    "text_hash": md5_short(text),
                    "voice": voice,
                    "engine": engine,
                    "model": model if engine == "vieneu" else None,
                    "language": language,
                    "subtitle_supported": engine == "edge",
                    "subtitle_ready": subtitle_ready,
                    "subtitle_cues": len(cues_all),
                    "duration_seconds": round(global_audio_sec, 3),
                },
            )
            generation_status.pop(cache_id, None)

        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(f"[ERROR] generate_chunks engine={engine}: {err}")

            remove_runtime_files_only(cache_id)
            save_cache_meta(
                cache_id,
                {
                    **timing_meta,
                    "status": "failed",
                    "error": err,
                    "text_hash": md5_short(text),
                    "voice": voice,
                    "engine": engine,
                    "model": model if engine == "vieneu" else None,
                    "language": language,
                    "subtitle_supported": engine == "edge",
                    "subtitle_ready": False,
                    "subtitle_cues": len(cues_all),
                },
            )
            generation_status.pop(cache_id, None)
        finally:
            _generation_locks.pop(cache_id, None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(TEMPLATES_DIR, "index.html")
    if not os.path.exists(path):
        return HTMLResponse(
            content="<h3>Template not found.</h3><p>Please create templates/index.html</p>",
            status_code=200,
        )
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__APP_VERSION__", VERSION)
    return HTMLResponse(content=html)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    path_svg = os.path.join(STATIC_DIR, "favicon.svg")
    if os.path.exists(path_svg):
        return FileResponse(path_svg, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="favicon not found")


@app.get("/tts/voices")
async def get_voices():
    return {
        "voices": get_all_voices(),
        "languages": SUPPORTED_LANGUAGES,
    }


@app.get("/tts/health")
async def health_check():
    ready = await vieneu_worker_is_ready()
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"vieneu_ready": ready, "mode": "redis-worker"},
    )


@app.post("/tts/start")
async def start_tts(background_tasks: BackgroundTasks, request: TTSRequest):
    # If source_chapters provided, concatenate texts
    # NOTE: MVP validation - uses .get("text", "") for safety.
    # Missing "text" keys result in empty strings, caught by validation below.
    # Production use should add explicit schema validation for chapter structure.
    if request.source_chapters and len(request.source_chapters) > 0:
        combined_text = "\n\n".join([
            chapter.get("text", "") for chapter in request.source_chapters
        ])
        text_to_process = normalize_text(combined_text)
    else:
        text_to_process = normalize_text(request.text)

    if not text_to_process:
        raise HTTPException(status_code=400, detail="Text must not be empty")
    if len(text_to_process) > TTS_MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=413,
            detail=f"Text exceeds the {TTS_MAX_TEXT_LENGTH} character limit",
        )

    language = validate_language(request.language)

    # Auto-detect engine from voice (e.g., "vieneu:default" -> "vieneu")
    voice = (request.voice or "").strip()
    detected_engine = get_engine_from_voice(voice)
    engine = validate_engine(detected_engine if detected_engine != "edge" else request.engine)

    voice = validate_voice(language, voice, engine)

    audio_quality: AudioQuality = request.audio_quality if engine == "vieneu" else "standard"
    # The current cache/file endpoints are MP3-only. Keep lossless disabled until
    # the audio path and response media types support WAV end to end.
    if audio_quality == "lossless":
        audio_quality = "high"

    cache_variant = f"{request.model or 'configured'}:{audio_quality}" if engine == "vieneu" else ""
    cache_id = get_cache_id(text_to_process, voice, engine, language, cache_variant)
    require_subtitles = engine == "edge"

    if is_cache_valid(cache_id, require_subtitles=require_subtitles):
        return {
            "cache_id": cache_id,
            "status": "completed",
            "url": f"/tts/file/{cache_id}",
            "subtitle_supported": engine == "edge",
            "subtitle_ready": engine == "edge",
        }

    current = get_effective_status(cache_id)
    if current and current.get("status") in {"queued", "processing", "generating"}:
        return {
            "cache_id": cache_id,
            "status": current.get("status"),
            "subtitle_supported": engine == "edge",
            "subtitle_ready": False,
        }

    if os.path.exists(get_audio_path(cache_id)) or os.path.exists(get_meta_path(cache_id)):
        cleanup_incomplete_cache(cache_id)

    chunk_preview = split_text_for_engine(text_to_process, engine=engine, language=language)
    estimated_seconds = estimate_conversion_seconds(
        text_to_process,
        engine,
        request.model,
        len(chunk_preview),
    )

    queued_meta = {
        "status": "queued",
        "progress": 0,
        "total": len(chunk_preview),
        "text_hash": md5_short(text_to_process),
        "voice": voice,
        "engine": engine,
        "model": request.model if engine == "vieneu" else None,
        "estimated_seconds": estimated_seconds,
        "language": language,
        "subtitle_supported": engine == "edge",
        "subtitle_ready": False,
        "subtitle_cues": 0,
    }

    if engine == "vieneu":
        save_cache_meta(cache_id, queued_meta)
        try:
            await enqueue_vieneu_tts_job(
                {
                    "text": text_to_process,
                    "voice": voice,
                    "engine": engine,
                    "cache_id": cache_id,
                    "language": language,
                    "chunks": chunk_preview,
                    "audio_quality": audio_quality,
                    "model": request.model,
                }
            )
        except TTSQueueError as exc:
            save_cache_meta(
                cache_id,
                {
                    **queued_meta,
                    "status": "failed",
                    "error": str(exc),
                },
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    else:
        generation_status[cache_id] = {
            "status": "queued",
            "progress": 0,
            "total": len(chunk_preview),
            "estimated_seconds": estimated_seconds,
            "subtitle_supported": engine == "edge",
            "subtitle_ready": False,
            "subtitle_cues": 0,
        }
        save_cache_meta(cache_id, queued_meta)

        background_tasks.add_task(
            generate_chunks_sync,
            text_to_process,
            voice,
            engine,
            cache_id,
            language,
            chunk_preview,
            audio_quality,
        )

    return {
        "cache_id": cache_id,
        "status": "started",
        "estimated_chunks": len(chunk_preview),
        "estimated_seconds": estimated_seconds,
        "subtitle_supported": engine == "edge",
        "subtitle_ready": False,
    }


@app.get("/tts/status/{cache_id}")
async def get_status(cache_id: str):
    cache_id = validate_cache_id(cache_id)
    status = get_effective_status(cache_id)
    if not status:
        raise HTTPException(status_code=404, detail="Not found")
    return status


@app.delete("/tts/file/{cache_id}")
async def delete_audio(cache_id: str):
    """Cancel ongoing generation or delete completed audio file."""
    cache_id = validate_cache_id(cache_id)

    # Check if generation is in progress
    in_progress = cache_id in generation_status
    status_info = get_effective_status(cache_id)

    if in_progress:
        # Request cancellation
        if request_cancellation(cache_id):
            return {"status": "cancelling", "message": "Generation cancellation requested"}
        else:
            raise HTTPException(status_code=404, detail="Generation not found")
    elif (
        status_info
        and status_info.get("engine") == "vieneu"
        and status_info.get("status") in {"queued", "processing", "generating"}
    ):
        try:
            await request_vieneu_cancel(cache_id)
        except TTSQueueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        save_cache_meta(
            cache_id,
            {
                **status_info,
                "status": "stopped",
                "error": "Cancellation requested",
            },
        )
        return {"status": "cancelling", "message": "VieNeu generation cancellation requested"}
    else:
        # Delete completed audio file
        cleanup_incomplete_cache(cache_id)
        return {"status": "deleted", "message": "Audio file deleted"}


@app.delete("/tts/cache")
async def clear_all_cache():
    """Clear all audio cache files."""
    if not ENABLE_GLOBAL_CACHE_CLEAR:
        raise HTTPException(status_code=404, detail="Not found")

    if not os.path.exists(CACHE_DIR):
        return {"status": "cleared", "deleted": 0}

    deleted_count = 0
    for filename in os.listdir(CACHE_DIR):
        filepath = os.path.join(CACHE_DIR, filename)
        if remove_if_exists(filepath):
            deleted_count += 1

    logger.info(f"Cleared all audio cache: {deleted_count} files deleted")
    return {"status": "cleared", "deleted": deleted_count}


@app.get("/tts/file/{cache_id}")
async def get_audio_file(cache_id: str, request: Request):
    cache_id = validate_cache_id(cache_id)
    if not is_cache_valid(cache_id):
        status = get_effective_status(cache_id)
        if status and status.get("status") in {"queued", "processing"}:
            raise HTTPException(
                status_code=409,
                detail="Audio is still being generated. Use /tts/stream for live playback.",
            )
        raise HTTPException(status_code=404, detail="Audio not ready")

    audio_path = get_audio_path(cache_id)
    return FileResponse(
        audio_path,
        media_type="audio/mpeg",
        filename=f"{cache_id}.mp3",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@app.get("/tts/subtitle/srt/{cache_id}")
async def get_srt_file(cache_id: str):
    cache_id = validate_cache_id(cache_id)
    meta = load_cache_meta(cache_id)
    if not meta or meta.get("engine") != "edge":
        raise HTTPException(status_code=404, detail="Subtitle not found")

    if not is_cache_valid(cache_id, require_subtitles=True):
        status = get_effective_status(cache_id)
        if status and status.get("status") in {"queued", "processing"}:
            raise HTTPException(status_code=409, detail="Subtitle is still being generated")
        raise HTTPException(status_code=404, detail="Subtitle not ready")

    return FileResponse(
        get_srt_path(cache_id),
        media_type="application/x-subrip",
        filename=f"{cache_id}.srt",
    )


@app.get("/tts/subtitle/vtt/{cache_id}")
async def get_vtt_file(cache_id: str):
    cache_id = validate_cache_id(cache_id)
    meta = load_cache_meta(cache_id)
    if not meta or meta.get("engine") != "edge":
        raise HTTPException(status_code=404, detail="Subtitle not found")

    if not is_cache_valid(cache_id, require_subtitles=True):
        status = get_effective_status(cache_id)
        if status and status.get("status") in {"queued", "processing"}:
            raise HTTPException(status_code=409, detail="Subtitle is still being generated")
        raise HTTPException(status_code=404, detail="Subtitle not ready")

    return FileResponse(
        get_vtt_path(cache_id),
        media_type="text/vtt",
        filename=f"{cache_id}.vtt",
    )


@app.get("/tts/cues/{cache_id}")
async def get_cues(cache_id: str):
    cache_id = validate_cache_id(cache_id)
    meta = load_cache_meta(cache_id)
    if not meta or meta.get("engine") != "edge":
        return JSONResponse({"cues": [], "done": True})

    cues = load_cues_json(cache_id)
    if cues:
        return {"cues": cues, "done": True}

    jsonl_path = get_cues_jsonl_path(cache_id)
    partial: List[dict] = []

    if os.path.exists(jsonl_path):
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        partial.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

    status = get_effective_status(cache_id)
    if status and status.get("status") in {"completed", "failed"}:
        result: dict = {"cues": partial, "done": True}
        if status.get("status") == "failed":
            result["error"] = status.get("error", "generation failed")
        return result
    return {"cues": partial, "done": False}


@app.get("/tts/cues/stream/{cache_id}")
async def stream_cues_live(cache_id: str, request: Request):
    cache_id = validate_cache_id(cache_id)
    meta = load_cache_meta(cache_id)
    if not meta or meta.get("engine") != "edge":
        async def empty():
            payload = json.dumps({"done": True}, ensure_ascii=False)
            yield f"event: complete\ndata: {payload}\n\n"

        return StreamingResponse(
            empty(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Accel-Buffering": "no",
            },
        )

    # Hỗ trợ resume: đọc Last-Event-ID từ header để skip cues đã nhận
    last_event_id = request.headers.get("Last-Event-ID")
    resume_after_index = 0
    if last_event_id:
        try:
            resume_after_index = int(last_event_id)
        except (ValueError, TypeError):
            pass

    async def event_generator() -> AsyncGenerator[str, None]:
        jsonl_path = get_cues_jsonl_path(cache_id)
        sent_offset = 0
        last_heartbeat = time.time()
        stable_completed_checks = 0

        def _emit_cue_line(payload: dict) -> Optional[str]:
            """Tạo SSE event string cho cue, hoặc None nếu cue đã được gửi."""
            cue_idx = payload.get("index", 0)
            if cue_idx <= resume_after_index:
                return None
            event_id = str(cue_idx)
            data_str = json.dumps(payload, ensure_ascii=False)
            return f"id: {event_id}\nevent: cue\ndata: {data_str}\n\n"

        while True:
            if os.path.exists(jsonl_path):
                try:
                    with open(jsonl_path, "r", encoding="utf-8") as f:
                        f.seek(sent_offset)
                        while True:
                            line = f.readline()
                            if not line:
                                break
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                payload = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            evt = _emit_cue_line(payload)
                            if evt:
                                yield evt
                            sent_offset = f.tell()
                except OSError:
                    pass

            st = get_effective_status(cache_id)
            state = st.get("status") if st else None

            if state == "failed":
                payload = json.dumps({"error": st.get("error", "generation failed")}, ensure_ascii=False)
                yield f"event: error\ndata: {payload}\n\n"
                break

            if state == "completed":
                # kiểm tra lần nữa xem còn cue cuối nào chưa flush hết không
                if os.path.exists(jsonl_path):
                    try:
                        with open(jsonl_path, "r", encoding="utf-8") as f:
                            f.seek(sent_offset)
                            has_more = False
                            while True:
                                line = f.readline()
                                if not line:
                                    break
                                has_more = True
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    payload = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                evt = _emit_cue_line(payload)
                                if evt:
                                    yield evt
                                sent_offset = f.tell()

                            if not has_more:
                                stable_completed_checks += 1
                    except OSError:
                        stable_completed_checks += 1
                else:
                    stable_completed_checks += 1

                if stable_completed_checks >= 2:
                    payload = json.dumps({"done": True}, ensure_ascii=False)
                    yield f"event: complete\ndata: {payload}\n\n"
                    break
            else:
                stable_completed_checks = 0

            if time.time() - last_heartbeat >= 10:
                yield ": keep-alive\n\n"
                last_heartbeat = time.time()

            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/tts/stream/{cache_id}")
async def stream_audio_live(cache_id: str):
    cache_id = validate_cache_id(cache_id)
    audio_path = get_audio_path(cache_id)

    st = get_effective_status(cache_id)
    state = st.get("status") if st else None
    if not st:
        raise HTTPException(status_code=404, detail="Audio not ready")
    if state == "failed":
        err = st.get("error", "generation failed")
        logger.warning(f"Stream failed for {cache_id}: {err}")
        raise HTTPException(status_code=503, detail=f"Generation failed: {err}")
    if state == "completed" and (
        not os.path.exists(audio_path) or os.path.getsize(audio_path) <= 0
    ):
        raise HTTPException(status_code=404, detail="Audio not ready")

    async def generate() -> AsyncGenerator[bytes, None]:
        read_size = 64 * 1024
        sent = 0
        stable_completed_checks = 0
        first_byte_started_at = time.time()

        while True:
            if os.path.exists(audio_path):
                cur_size = os.path.getsize(audio_path)
                if cur_size > sent:
                    with open(audio_path, "rb") as f:
                        f.seek(sent)
                        while sent < cur_size:
                            chunk = f.read(min(read_size, cur_size - sent))
                            if not chunk:
                                break
                            sent += len(chunk)
                            yield chunk
                    stable_completed_checks = 0

            st = get_effective_status(cache_id)
            state = st.get("status") if st else None

            if state in {"failed", "stopped"}:
                break
            if state is None:
                break

            if state == "completed":
                expected_size = st.get("file_size") or 0
                if expected_size > 0 and sent >= expected_size:
                    stable_completed_checks += 1
                    if stable_completed_checks >= 2:
                        break
                else:
                    stable_completed_checks = 0

            if sent == 0 and time.time() - first_byte_started_at >= TTS_STREAM_FIRST_BYTE_TIMEOUT_SECONDS:
                logger.warning(
                    "Stream first-byte timeout for %s: status=%s, file_exists=%s",
                    cache_id,
                    state,
                    os.path.exists(audio_path),
                )
                break

            await asyncio.sleep(0.2)

    return StreamingResponse(
        generate(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Optional debug route
# ---------------------------------------------------------------------------
@app.post("/tts/debug/chunks")
async def debug_chunks(request: TTSRequest):
    if not ENABLE_DEBUG_TTS:
        raise HTTPException(status_code=404, detail="Not found")

    text = normalize_text(request.text)
    if not text:
        raise HTTPException(status_code=400, detail="Text must not be empty")

    language = validate_language(request.language)
    chunk_sizes, hard_max, max_sentences_profile = get_chunk_profile(language, text)
    chunks = split_text_into_chunks(text, language=language)

    return {
        "language": language,
        "chunk_profile": {
            "chunk_sizes": chunk_sizes,
            "hard_max": hard_max,
            "max_sentences_per_chunk": max_sentences_profile,
        },
        "total_chunks": len(chunks),
        "chunks": [
            {
                "index": i + 1,
                "effective_len": effective_len(ch),
                "char_len": len(ch),
                "text": ch,
            }
            for i, ch in enumerate(chunks)
        ],
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    ready = await redis_is_ready()
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ok": ready, "version": VERSION, "redis": ready},
    )

@app.on_event("startup")
async def startup_event():
    """Register background cleanup task on startup and recover orphan jobs."""
    from file_processor import start_cleanup_scheduler
    asyncio.create_task(start_cleanup_scheduler())
    logger.info("Document upload cleanup task started")

    # Initialize rate limiter
    await rate_limiter.initialize()
    app.state.rate_limiter = rate_limiter
    logger.info("Rate limiter initialized")

    if VIENEU_INIT_IN_WEB:
        # Usually disabled in production: the separate worker owns the heavy model.
        from vieneu_model import initialize_model_pool
        try:
            initialize_model_pool()
            logger.info("VieNeu model pool initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize VieNeu model pool: {e}")
            # Don't fail startup - other engines (Edge, gTTS) still work
    else:
        logger.info("VieNeu model initialization skipped in web process")

    # Recover orphan jobs from previous run
    from job_queue import recover_orphan_jobs
    recovered = recover_orphan_jobs()
    if recovered > 0:
        logger.info(f"Recovered {recovered} orphan extraction jobs")

    # Start periodic cleanup
    asyncio.create_task(periodic_job_cleanup())


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    await rate_limiter.close()
    logger.info("Rate limiter closed")


async def periodic_job_cleanup():
    """Background task to clean up old job files."""
    while True:
        try:
            from job_queue import cleanup_old_jobs
            deleted = cleanup_old_jobs()
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old job files")
        except Exception as e:
            logger.error(f"Job cleanup error: {e}")

        try:
            deleted = cleanup_old_audio_cache()
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old audio cache files")
        except Exception as e:
            logger.error(f"Audio cache cleanup error: {e}")

        # Run every hour
        await asyncio.sleep(3600)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
