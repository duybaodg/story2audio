# VieNeu Parallel Processing Design

## Context

VieNeu TTS is currently slower than Edge TTS and gTTS because it runs a local ML model (0.5B parameters) on CPU. The current implementation processes chunks sequentially, which doesn't utilize multi-core CPUs effectively.

**Goal:** Reduce overall completion time for VieNeu TTS generation through parallel chunk processing.

**Constraints:**
- CPU-only infrastructure (no GPU acceleration)
- Storage and bandwidth are not major concerns
- Must maintain compatibility with existing cache system

## Recommended Approach: Parallel Chunk Processing

Process multiple text chunks simultaneously using ThreadPoolExecutor, collecting results as they complete and writing them in the correct order.

### Current Flow (Sequential)

```
for chunk in chunks:
    audio = await vieneu_tts_to_audio(chunk)  # blocks until complete
    append_to_file(audio)
```

### New Flow (Parallel)

```
# Submit all chunks to ThreadPoolExecutor at once
futures = [executor.submit(vieneu_tts_to_audio, chunk) for chunk in chunks]

# Collect results as they complete, maintaining order
for future in as_completed(futures):
    audio = future.result()
    append_to_file(audio)
```

## Files to Modify

- `main.py` - `generate_chunks()` function

## Implementation Details

### Concurrency Strategy

```python
MAX_CONCURRENT_CHUNKS = 4  # Configurable, limits CPU/memory usage

async def generate_chunks(...):
    chunks = split_text_into_chunks(text, language=language)
    total = len(chunks)

    # Use ThreadPoolExecutor for parallel processing
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CHUNKS)

    # Submit all chunks for parallel processing
    pending = {
        loop.run_in_executor(executor, vieneu_tts_to_audio, chunk, voice): i
        for i, chunk in enumerate(chunks)
    }

    # Storage for results (index -> audio bytes)
    results = {}

    # Collect as they complete, update progress
    while pending:
        done, _ = await asyncio.wait(pending.keys(), return_when=FIRST_COMPLETED)
        for future in done:
            index = pending.pop(future)
            try:
                audio = await future
                results[index] = audio
                generation_status[cache_id]["progress"] = len(results)
            except Exception as e:
                cleanup_incomplete_cache(cache_id)
                raise
```

### Writing to File (Order Preservation)

```python
    # Write chunks in correct order
    for i in range(total):
        if i not in results:
            raise RuntimeError(f"Chunk {i} did not complete")
        audio = results[i]
        if i > 0:
            audio = strip_id3v2(audio)
        with open(audio_path, "ab") as f:
            f.write(audio)
```

### Progress Updates

- Progress updates as chunks complete (not in sequential order)
- Total progress = `len(results) / total`

## Error Handling

### Failure Scenarios

1. **Single chunk fails:**
   - Cancel all pending futures
   - Clean up incomplete cache files
   - Mark cache_id as failed with error details

2. **Executor timeout:**
   - Set timeout per chunk (5 minutes per chunk)
   - Use `asyncio.wait_for()` with timeout

3. **Memory limits:**
   - Limit max concurrent chunks to avoid OOM
   - Write completed chunks to disk immediately

### Implementation

```python
try:
    while pending:
        done, pending = await asyncio.wait(
            pending.keys(),
            return_when=FIRST_COMPLETED,
            timeout=300
        )
        for future in done:
            index = pending.pop(future)
            audio = await future
            results[index] = audio

except asyncio.TimeoutError:
    for f in pending:
        f.cancel()
    cleanup_incomplete_cache(cache_id)
    raise HTTPException(503, "Generation timed out")

except Exception as e:
    for f in pending:
        f.cancel()
    cleanup_incomplete_cache(cache_id)
    save_cache_meta(cache_id, {"status": "failed", "error": str(e)})
    raise
```

## Testing Strategy

### Unit Tests

- Test parallel processing with mock `vieneu_tts_to_audio` function
- Verify chunks are written in correct order regardless of completion order
- Test progress reporting updates correctly
- Test error handling when one chunk fails

### Integration Tests

- Generate real audio with 3-5 chunks using VieNeu
- Verify output MP3 is playable and matches sequential output
- Compare generation time (should be faster with parallel)

### Manual Testing

```bash
# Test with long text (multiple chunks)
curl -X POST http://localhost:8000/tts/start \
  -H "Content-Type: application/json" \
  -d '{"text": "<long vietnamese text>", "voice": "vieneu:default"}'

# Monitor progress
watch curl http://localhost:8000/tts/status/{cache_id}
```

### Performance Benchmark

- Compare sequential vs parallel timing for same text
- Expected: 2-3x speedup on 4-core CPU

## Configuration

```python
# Environment variables (optional)
VIENEU_MAX_WORKERS = int(os.getenv("VIENEU_MAX_WORKERS", "4"))
```

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Single chunk | Falls back to sequential (no overhead) |
| CPU with 1-2 cores | Auto-adjust `max_workers` to `cpu_count()` |
| Very long texts (100+ chunks) | Process in batches to avoid memory issues |

## Expected Performance Improvement

| Cores | Expected Speedup |
|-------|------------------|
| 2 | 1.5x - 1.8x |
| 4 | 2.5x - 3.5x |
| 8 | 4x - 6x |

Actual performance depends on CPU specs and chunk sizes.

## Future Enhancements (Out of Scope)

- GPU acceleration for ML inference
- Faster MP3 encoder (lameenc) to replace pydub
- WAV streaming with background MP3 conversion
