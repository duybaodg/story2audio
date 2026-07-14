import pytest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import tts_queue


class FakeRedis:
    def __init__(self, queue_size):
        self.queue_size = queue_size
        self.pushed = []

    async def llen(self, key):
        return self.queue_size

    async def lpush(self, key, value):
        self.pushed.append(value)

    async def get(self, key):
        return "1" if self.queue_size else None

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_vieneu_queue_cap(monkeypatch):
    full = FakeRedis(tts_queue.TTS_MAX_QUEUE_SIZE)

    async def get_full_client():
        return full

    monkeypatch.setattr(tts_queue, "get_async_client", get_full_client)
    with pytest.raises(tts_queue.TTSQueueError, match="queue is full"):
        await tts_queue.enqueue_vieneu_tts_job({"cache_id": "a" * 32})
    assert full.pushed == []

    available = FakeRedis(tts_queue.TTS_MAX_QUEUE_SIZE - 1)

    async def get_available_client():
        return available

    monkeypatch.setattr(tts_queue, "get_async_client", get_available_client)
    await tts_queue.enqueue_vieneu_tts_job({"cache_id": "b" * 32})
    assert len(available.pushed) == 1


@pytest.mark.asyncio
async def test_worker_readiness_requires_heartbeat(monkeypatch):
    client = FakeRedis(1)

    async def get_client():
        return client

    monkeypatch.setattr(tts_queue, "get_async_client", get_client)
    assert await tts_queue.vieneu_worker_is_ready() is True

    client.queue_size = 0
    assert await tts_queue.vieneu_worker_is_ready() is False
