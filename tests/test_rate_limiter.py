import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rate_limiter import RateLimiter


class FakeRedis:
    def __init__(self):
        self.counts = {}

    async def eval(self, script, *args):
        key_count = int(args[0])
        keys = args[1:1 + key_count]
        values = args[1 + key_count:]
        now = float(values[0])
        assert script.index("current >=") < script.index("ZADD")
        for index, key in enumerate(keys):
            limit = int(values[3 + index * 3])
            if self.counts.get(key, 0) >= limit:
                return [0, index + 1, str(now - 1)]
        for key in keys:
            self.counts[key] = self.counts.get(key, 0) + 1
        return [1, 0, "0"]


@pytest.mark.asyncio
async def test_rejected_requests_do_not_allocate_more_state():
    limiter = object.__new__(RateLimiter)
    limiter._client = FakeRedis()

    allowed = [await limiter.check_limit("tts:test", 2, 60) for _ in range(5)]

    assert [result[0] for result in allowed] == [True, True, False, False, False]
    assert limiter._client.counts["tts:test"] == 2


@pytest.mark.asyncio
async def test_second_window_rejection_does_not_charge_first_window():
    limiter = object.__new__(RateLimiter)
    limiter._client = FakeRedis()
    limiter._client.counts["tts:3600:client"] = 20

    allowed, _error = await limiter.check_tts_limits("client")

    assert allowed is False
    assert limiter._client.counts.get("tts:60:client", 0) == 0
    assert limiter._client.counts["tts:3600:client"] == 20
