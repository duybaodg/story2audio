import logging
import os
import signal
import threading
import time
from pathlib import Path

from tts_queue import (
    TTSQueueError,
    acknowledge_job,
    clear_vieneu_cancel_sync,
    deserialize_job,
    get_sync_client,
    is_vieneu_cancelled_sync,
    recover_processing_jobs,
    reserve_job,
    TTS_WORKER_HEARTBEAT_KEY,
    TTS_WORKER_HEARTBEAT_TTL_SECONDS,
)

logger = logging.getLogger("ebook2audio.worker")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_shutdown = False
_READY_FILE = Path("/tmp/vieneu-worker-ready")


def _heartbeat_loop() -> None:
    client = get_sync_client()
    try:
        while not _shutdown:
            client.setex(TTS_WORKER_HEARTBEAT_KEY, TTS_WORKER_HEARTBEAT_TTL_SECONDS, "1")
            time.sleep(max(1, TTS_WORKER_HEARTBEAT_TTL_SECONDS // 3))
    finally:
        client.delete(TTS_WORKER_HEARTBEAT_KEY)
        client.close()


def _handle_shutdown(signum, frame):
    global _shutdown
    _shutdown = True
    logger.info("Received signal %s; stopping after current job", signum)


def _mark_stopped(cache_id: str, message: str = "Cancellation requested") -> None:
    import main

    meta = main.load_cache_meta(cache_id) or {}
    main.save_cache_meta(
        cache_id,
        {
            **meta,
            "status": "stopped",
            "error": message,
        },
    )


def _process_job(raw_job: str, client) -> None:
    import main

    job = deserialize_job(raw_job)
    cache_id = main.validate_cache_id(job["cache_id"])

    if is_vieneu_cancelled_sync(cache_id, client):
        logger.info("Skipping cancelled VieNeu job %s", cache_id)
        _mark_stopped(cache_id)
        return

    logger.info("Starting VieNeu job %s", cache_id)
    main.generate_chunks_sync(
        text=job["text"],
        voice=job["voice"],
        engine="vieneu",
        cache_id=cache_id,
        language=job.get("language", "vi"),
        chunks=job.get("chunks"),
        audio_quality=job.get("audio_quality", "standard"),
        model=job.get("model"),
    )
    clear_vieneu_cancel_sync(cache_id, client)
    logger.info("Finished VieNeu job %s", cache_id)


def main_loop() -> int:
    _READY_FILE.unlink(missing_ok=True)
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    import main
    from vieneu_model import initialize_model_pool

    logger.info("Initializing VieNeu model in worker process")
    initialize_model_pool()

    client = get_sync_client()
    recovered = recover_processing_jobs(client)
    if recovered:
        logger.info("Recovered %d in-flight VieNeu job(s)", recovered)

    logger.info("VieNeu worker ready")
    _READY_FILE.touch()
    heartbeat = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat.start()
    try:
        while not _shutdown:
            try:
                raw_job = reserve_job(client, timeout=5)
                if raw_job is None:
                    continue
                try:
                    _process_job(raw_job, client)
                except Exception as exc:
                    logger.exception("VieNeu job failed: %s", exc)
                    try:
                        job = deserialize_job(raw_job)
                        cache_id = main.validate_cache_id(job["cache_id"])
                        meta = main.load_cache_meta(cache_id) or {}
                        main.save_cache_meta(
                            cache_id,
                            {
                                **meta,
                                "status": "failed",
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                        )
                    except Exception:
                        logger.exception("Unable to persist failed job metadata")
                finally:
                    acknowledge_job(client, raw_job)
            except TTSQueueError:
                raise
            except Exception as exc:
                logger.exception("Worker loop error: %s", exc)
                time.sleep(2)
    finally:
        _READY_FILE.unlink(missing_ok=True)
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main_loop())
