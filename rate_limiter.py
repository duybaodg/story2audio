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

        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=window)

        pipe = self._client.pipeline()

        # Remove old entries outside the window
        pipe.zremrangebyscore(key, 0, window_start.timestamp())

        # Count current requests
        pipe.zcard(key)

        # Add current request
        pipe.zadd(key, {str(now.timestamp()): now.timestamp()})

        # Set expiry
        pipe.expire(key, window + 1)

        results = await pipe.execute()
        current_count = results[1]

        if current_count >= limit:
            # Get oldest request to calculate retry_after
            oldest = await self._client.zrange(key, 0, 0, withscores=True)
            if oldest:
                oldest_time = oldest[0][1]
                retry_after = int(window - (now.timestamp() - oldest_time)) + 1
                return False, f"Rate limit exceeded. Try again in {retry_after}s"
            return False, "Rate limit exceeded"

        return True, None

    async def check_tts_limits(self, ip: str) -> Tuple[bool, Optional[str]]:
        for limit, window in ((5, 60), (20, 3600)):
            allowed, error = await self.check_limit(f"tts:{window}:{ip}", limit, window)
            if not allowed:
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
        # Check per-minute limit (5 uploads/min)
        allowed, error = await self.check_limit(
            f"upload:1m:{ip}",
            limit=5,
            window=60
        )
        if not allowed:
            return False, f"5 uploads per minute allowed. {error}"

        # Check per-hour limit (20 uploads/hour)
        allowed, error = await self.check_limit(
            f"upload:1h:{ip}",
            limit=20,
            window=3600
        )
        if not allowed:
            return False, f"20 uploads per hour allowed. {error}"

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
