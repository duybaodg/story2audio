# rate_limiter.py
import os
from datetime import datetime, timedelta, UTC
from typing import Optional, Tuple

try:
    import redis.asyncio as redis
except ImportError:
    redis = None


class RateLimiter:
    """Redis-based rate limiting with sliding window."""

    _SLIDING_WINDOW_SCRIPT = """
    for i, key in ipairs(KEYS) do
        local offset = 3 + ((i - 1) * 3)
        redis.call('ZREMRANGEBYSCORE', key, '-inf', ARGV[offset])
        local current = redis.call('ZCARD', key)
        if current >= tonumber(ARGV[offset + 1]) then
            local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
            return {0, i, oldest[2] or '0'}
        end
    end
    for i, key in ipairs(KEYS) do
        local offset = 3 + ((i - 1) * 3)
        redis.call('ZADD', key, ARGV[1], ARGV[2])
        redis.call('EXPIRE', key, ARGV[offset + 2])
    end
    return {1, 0, '0'}
    """

    def __init__(self, redis_url: Optional[str] = None):
        if redis is None:
            raise ImportError(
                "redis package is required for rate limiting. "
                "Install with: pip install redis>=5.0.0"
            )
        self.redis_url = redis_url or os.getenv(
            "REDIS_URL",
            "redis://localhost:6379"
        )
        self._client: Optional[redis.Redis] = None

    async def initialize(self):
        """Initialize Redis connection."""
        self._client = await redis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True
        )

    async def close(self):
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()

    async def clear(self):
        """Clear all rate limit data (for testing)."""
        if self._client:
            await self._client.flushdb()

    async def check_limit(
        self,
        key: str,
        limit: int,
        window: int,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if request is within rate limit using sliding window.

        Args:
            key: Unique key for this limit (e.g., "upload:127.0.0.1")
            limit: Maximum number of requests allowed
            window: Time window in seconds

        Returns:
            (allowed: bool, error_message: str | None)
        """
        if not self._client:
            await self.initialize()

        allowed, rejected_index, retry_after = await self._check_limits(
            [(key, limit, window)]
        )
        if not allowed:
            if retry_after:
                return False, f"Rate limit exceeded. Try again in {retry_after}s"
            return False, "Rate limit exceeded"
        return True, None

    async def _check_limits(
        self,
        limits: list[tuple[str, int, int]],
    ) -> tuple[bool, Optional[int], Optional[int]]:
        if not self._client:
            await self.initialize()

        now = datetime.now(UTC)
        member = f"{now.timestamp()}:{os.urandom(4).hex()}"
        arguments = [now.timestamp(), member]
        for _key, limit, window in limits:
            window_start = now - timedelta(seconds=window)
            arguments.extend((window_start.timestamp(), limit, window + 1))

        allowed, rejected_index, oldest_timestamp = await self._client.eval(
            self._SLIDING_WINDOW_SCRIPT,
            len(limits),
            *(key for key, _limit, _window in limits),
            *arguments,
        )

        if not int(allowed):
            oldest_time = float(oldest_timestamp or 0)
            if oldest_time:
                window = limits[int(rejected_index) - 1][2]
                retry_after = max(1, int(window - (now.timestamp() - oldest_time)) + 1)
                return False, int(rejected_index) - 1, retry_after
            return False, int(rejected_index) - 1, None
        return True, None, None

    async def check_tts_limits(self, ip: str) -> Tuple[bool, Optional[str]]:
        limits = [(f"tts:{window}:{ip}", limit, window) for limit, window in ((5, 60), (20, 3600))]
        allowed, rejected_index, retry_after = await self._check_limits(limits)
        if not allowed:
            _key, limit, window = limits[rejected_index]
            error = f"Rate limit exceeded. Try again in {retry_after}s" if retry_after else "Rate limit exceeded"
            return False, f"{limit} TTS requests per {window // 60} minute(s) allowed. {error}"
        return True, None

    async def check_upload_limits(
        self,
        ip: str,
        session_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check all upload-related rate limits.

        Args:
            ip: Client IP address
            session_id: Optional upload session ID for chunk limit

        Returns:
            (allowed: bool, error_message: str | None)
        """
        limits = [
            (f"upload:1m:{ip}", 5, 60),
            (f"upload:1h:{ip}", 20, 3600),
        ]
        allowed, rejected_index, retry_after = await self._check_limits(limits)
        if not allowed:
            _key, limit, window = limits[rejected_index]
            unit = "minute" if window == 60 else "hour"
            error = f"Rate limit exceeded. Try again in {retry_after}s" if retry_after else "Rate limit exceeded"
            return False, f"{limit} uploads per {unit} allowed. {error}"

        # Check concurrent chunks if session provided
        if session_id:
            # Track active chunk uploads per session
            # This is a simpler check - max 5 chunks in flight
            chunk_key = f"chunks:{session_id}"
            current = await self._client.incr(chunk_key)
            await self._client.expire(chunk_key, 300)  # 5 min expiry

            if current > 5:
                await self._client.decr(chunk_key)
                return False, "Maximum 5 concurrent chunks per session"

        return True, None
