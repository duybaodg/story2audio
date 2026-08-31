import json
import os
from typing import Any, Dict, Optional

try:
    import redis
    import redis.asyncio as async_redis
except ImportError:  # pragma: no cover - app dependency should provide redis
    redis = None
    async_redis = None


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
TTS_QUEUE_KEY = os.getenv("TTS_QUEUE_KEY", "ebook2audio:tts:vieneu:queue")
TTS_PROCESSING_KEY = os.getenv("TTS_PROCESSING_KEY", "ebook2audio:tts:vieneu:processing")
TTS_CANCEL_PREFIX = os.getenv("TTS_CANCEL_PREFIX", "ebook2audio:tts:cancel:")
TTS_CANCEL_TTL_SECONDS = int(os.getenv("TTS_CANCEL_TTL_SECONDS", "86400"))
TTS_MAX_QUEUE_SIZE = int(os.getenv("TTS_MAX_QUEUE_SIZE", "20"))
TTS_WORKER_HEARTBEAT_KEY = os.getenv("TTS_WORKER_HEARTBEAT_KEY", "ebook2audio:tts:vieneu:worker")
TTS_WORKER_HEARTBEAT_TTL_SECONDS = int(os.getenv("TTS_WORKER_HEARTBEAT_TTL_SECONDS", "30"))
_async_client = None


class TTSQueueError(RuntimeError):
    pass


def _serialize_job(job: Dict[str, Any]) -> str:
    return json.dumps(job, ensure_ascii=False, separators=(",", ":"))


def deserialize_job(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TTSQueueError("Invalid TTS job payload")
    return data


def _require_redis():
    if redis is None or async_redis is None:
        raise TTSQueueError("redis package is required for TTS queue")


def get_sync_client():
    _require_redis()
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


async def get_async_client():
    global _async_client
    _require_redis()
    if _async_client is None:
        _async_client = async_redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    return _async_client


async def close_async_client() -> None:
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None


async def enqueue_vieneu_tts_job(job: Dict[str, Any]) -> None:
    client = await get_async_client()
    try:
        # ponytail: single-web-process queue cap; use an atomic Lua check if web replicas are added.
        if await client.llen(TTS_QUEUE_KEY) >= TTS_MAX_QUEUE_SIZE:
            raise TTSQueueError("VieNeu queue is full; try again later")
        await client.lpush(TTS_QUEUE_KEY, _serialize_job(job))
    except Exception as exc:
        raise TTSQueueError(f"Unable to enqueue VieNeu TTS job: {exc}") from exc


async def redis_is_ready() -> bool:
    client = await get_async_client()
    try:
        return bool(await client.ping())
    except Exception:
        return False


async def vieneu_worker_is_ready() -> bool:
    client = await get_async_client()
    try:
        return bool(await client.get(TTS_WORKER_HEARTBEAT_KEY))
    except Exception:
        return False


async def request_vieneu_cancel(cache_id: str) -> None:
    client = await get_async_client()
    try:
        await client.setex(f"{TTS_CANCEL_PREFIX}{cache_id}", TTS_CANCEL_TTL_SECONDS, "1")
    except Exception as exc:
        raise TTSQueueError(f"Unable to request VieNeu cancellation: {exc}") from exc


def request_vieneu_cancel_sync(cache_id: str) -> None:
    client = get_sync_client()
    try:
        client.setex(f"{TTS_CANCEL_PREFIX}{cache_id}", TTS_CANCEL_TTL_SECONDS, "1")
    except Exception as exc:
        raise TTSQueueError(f"Unable to request VieNeu cancellation: {exc}") from exc
    finally:
        client.close()


def is_vieneu_cancelled_sync(cache_id: str, client: Optional[Any] = None) -> bool:
    owns_client = client is None
    client = client or get_sync_client()
    try:
        return bool(client.get(f"{TTS_CANCEL_PREFIX}{cache_id}"))
    except Exception:
        return False
    finally:
        if owns_client:
            client.close()


def clear_vieneu_cancel_sync(cache_id: str, client: Optional[Any] = None) -> None:
    owns_client = client is None
    client = client or get_sync_client()
    try:
        client.delete(f"{TTS_CANCEL_PREFIX}{cache_id}")
    finally:
        if owns_client:
            client.close()


def recover_processing_jobs(client: Any) -> int:
    recovered = 0
    while True:
        raw = client.rpop(TTS_PROCESSING_KEY)
        if raw is None:
            break
        client.lpush(TTS_QUEUE_KEY, raw)
        recovered += 1
    return recovered


def reserve_job(client: Any, timeout: int = 5) -> Optional[str]:
    return client.brpoplpush(TTS_QUEUE_KEY, TTS_PROCESSING_KEY, timeout=timeout)


def acknowledge_job(client: Any, raw_job: str) -> None:
    client.lrem(TTS_PROCESSING_KEY, 1, raw_job)
