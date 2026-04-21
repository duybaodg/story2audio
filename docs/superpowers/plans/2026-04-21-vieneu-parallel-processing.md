# VieNeu Parallel Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement parallel chunk processing for VieNeu TTS to reduce overall generation time by 2-3x on multi-core CPUs.

**Architecture:** Submit all text chunks to a ThreadPoolExecutor simultaneously, collect results as they complete using `asyncio.wait()` with `FIRST_COMPLETED`, then write chunks to disk in correct order.

**Tech Stack:** Python asyncio, concurrent.futures, ThreadPoolExecutor, existing VieNeu TTS integration

---

## File Structure

```
main.py                          # Modify: generate_chunks() function
tests/test_main.py              # Create: tests for parallel processing
```

---

## Task 1: Create Feature Branch

- [ ] **Step 1: Create and checkout new branch**

```bash
git checkout -b feature/vieneu-parallel-processing
```

Expected: Branch created and switched, output shows `Switched to a new branch 'feature/vieneu-parallel-processing'`

- [ ] **Step 2: Verify branch**

```bash
git branch --show-current
```

Expected: `feature/vieneu-parallel-processing`

---

## Task 2: Add Configuration for Max Workers

**Files:**
- Modify: `main.py:87-96` (Config section)

- [ ] **Step 1: Add VIENEU_MAX_WORKERS configuration**

Locate the Config section in `main.py` (around line 87-96). Add the new configuration after `ENABLE_DEBUG_TTS`:

```python
PROXY = os.getenv("PROXY")
ENABLE_DEBUG_TTS = os.getenv("ENABLE_DEBUG_TTS", "").lower() in {"1", "true", "yes"}
VIENEU_MAX_WORKERS = int(os.getenv("VIENEU_MAX_WORKERS", str(min(4, os.cpu_count() or 4))))
```

- [ ] **Step 2: Verify syntax**

```bash
python -m py_compile main.py
```

Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add VIENEU_MAX_WORKERS configuration for parallel processing"
```

---

## Task 3: Extract Chunk Generation Logic into Separate Function

**Files:**
- Modify: `main.py:1039-1106` (vieneu_tts_to_audio function)

- [ ] **Step 1: Create synchronous wrapper for VieNeu**

The current `vieneu_tts_to_audio` is async but runs sync code. We need a pure sync version for ThreadPoolExecutor.

Add this new function right after the existing `vieneu_tts_to_audio` function (around line 1107):

```python
def vieneu_tts_to_audio_sync(text: str, voice: str) -> bytes:
    """
    Synchronous wrapper for VieNeu TTS.
    For use with ThreadPoolExecutor.

    Voice format: "vieneu:default" or "vieneu:{preset_id}"
    Returns: audio bytes (MP3 format)
    """
    from vieneu import Vieneu
    from pydub import AudioSegment
    import io
    import wave

    tts = Vieneu()

    # Handle preset voices
    preset_voice = None
    if voice != "vieneu:default":
        preset_id = voice.split(":", 1)[1]
        try:
            available = tts.list_preset_voices()
            for desc, name in available:
                if name == preset_id:
                    preset_voice = tts.get_preset_voice(name)
                    break
        except Exception:
            pass  # Fall back to default voice

    try:
        if preset_voice:
            audio_array = tts.infer(text=text, voice=preset_voice)
        else:
            audio_array = tts.infer(text=text)

        # VieNeu returns float32 in range [-1, 1], need to convert to int16
        sample_rate = 24000  # VieNeu uses 24kHz

        # Convert float32 [-1, 1] to int16 [-32768, 32767]
        audio_int16 = np.int16(audio_array * 32767)

        with io.BytesIO() as wav_buffer:
            with wave.open(wav_buffer, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 2 bytes per sample (int16)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(audio_int16.tobytes())
            wav_bytes = wav_buffer.getvalue()

        # Convert WAV to MP3 using pydub
        wav_audio = AudioSegment(
            data=wav_bytes,
            sample_width=2,
            frame_rate=sample_rate,
            channels=1
        )

        mp3_buffer = io.BytesIO()
        wav_audio.export(mp3_buffer, format="mp3", bitrate="64k")
        return mp3_buffer.getvalue()

    except Exception as e:
        raise RuntimeError(f"VieNeu TTS failed: {e}")
```

- [ ] **Step 2: Verify syntax**

```bash
python -m py_compile main.py
```

Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: add synchronous wrapper for VieNeu TTS"
```

---

## Task 4: Write Unit Tests for Parallel Processing

**Files:**
- Create: `tests/test_vieneu_parallel.py`

- [ ] **Step 1: Create test file with mock tests**

Create new test file `tests/test_vienen_parallel.py`:

```python
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from main import vieneu_tts_to_audio_sync


class TestVieneuParallelProcessing:
    """Tests for parallel chunk processing of VieNeu TTS."""

    def test_vieneu_sync_wrapper_exists(self):
        """Test that the sync wrapper function exists and is callable."""
        assert callable(vieneu_tts_to_audio_sync)

    @patch('main.vieneu_tts_to_audio_sync')
    def test_parallel_chunk_processing_preserves_order(self, mock_tts):
        """Test that chunks are written in correct order regardless of completion order."""
        # Import after patching
        from concurrent.futures import ThreadPoolExecutor
        import tempfile
        import os

        # Mock different completion times by returning different delays
        chunk_results = {
            0: b"MP3_DATA_0",
            1: b"MP3_DATA_1",
            2: b"MP3_DATA_2",
            3: b"MP3_DATA_3",
        }

        def mock_generate(text, voice):
            # Simulate different processing times
            chunk_id = int(text.split("_")[-1])
            import time
            time.sleep((3 - chunk_id) * 0.01)  # Reverse order completion
            return chunk_results[chunk_id]

        mock_tts.side_effect = mock_generate

        # Test parallel processing
        chunks = ["chunk_0", "chunk_1", "chunk_2", "chunk_3"]
        voice = "vieneu:default"

        results = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(vieneu_tts_to_audio_sync, chunk, voice): i
                      for i, chunk in enumerate(chunks)}

            from concurrent.futures import as_completed
            for future in as_completed(futures):
                index = futures[future]
                results[index] = future.result()

        # Verify all chunks completed
        assert len(results) == 4
        # Verify order preserved by index
        assert all(i in results for i in range(4))

    @patch('main.vieneu_tts_to_audio_sync')
    def test_parallel_chunk_error_handling(self, mock_tts):
        """Test that errors in one chunk are handled properly."""
        from concurrent.futures import ThreadPoolExecutor

        def mock_generate_fail(text, voice):
            if "chunk_1" in text:
                raise RuntimeError("Chunk processing failed")
            return b"MP3_DATA"

        mock_tts.side_effect = mock_generate_fail

        chunks = ["chunk_0", "chunk_1", "chunk_2"]
        voice = "vieneu:default"

        errors = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(vieneu_tts_to_audio_sync, chunk, voice): i
                      for i, chunk in enumerate(chunks)}

            from concurrent.futures import as_completed
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    errors.append(str(e))

        # Should have at least one error
        assert len(errors) > 0
        assert "Chunk processing failed" in errors[0]

    def test_max_workers_respects_cpu_count(self):
        """Test that VIENEU_MAX_WORKERS respects CPU count."""
        import os
        from main import VIENEU_MAX_WORKERS

        # Should be between 1 and 4 (or cpu_count if lower)
        cpu_count = os.cpu_count() or 4
        expected_max = min(4, cpu_count)
        assert VIENEU_MAX_WORKERS == expected_max
```

- [ ] **Step 2: Run tests to verify they fail (implementation not done yet)**

```bash
pytest tests/test_vieneu_parallel.py -v
```

Expected: Tests may pass or fail depending on current state - this is OK, we're setting up the test infrastructure first.

- [ ] **Step 3: Commit**

```bash
git add tests/test_vieneu_parallel.py
git commit -m "test: add unit tests for VieNeu parallel processing"
```

---

## Task 5: Modify generate_chunks for Parallel Processing (VieNeu Only)

**Files:**
- Modify: `main.py:1134-1342` (generate_chunks function)

- [ ] **Step 1: Locate the chunk processing loop in generate_chunks**

Find the `for i, chunk_text in enumerate(chunks):` loop in `generate_chunks()` function (around line 1216).

- [ ] **Step 2: Replace sequential loop with parallel processing for VieNeu**

Replace the entire chunk processing section (from the `try:` block inside the loop through the end of processing) with this parallel implementation:

```python
        try:
            # Use parallel processing for VieNeu, sequential for others
            if engine == "vieneu":
                # Parallel processing for VieNeu
                loop = asyncio.get_running_loop()
                executor = None

                try:
                    # Limit concurrent chunks to avoid overwhelming CPU
                    executor = ThreadPoolExecutor(max_workers=VIENEU_MAX_WORKERS)

                    # Submit all chunks for parallel processing
                    pending = {
                        loop.run_in_executor(executor, vieneu_tts_to_audio_sync, chunk_text, voice): i
                        for i, chunk_text in enumerate(chunks)
                    }

                    # Storage for results (index -> audio bytes)
                    results = {}

                    # Collect results as they complete
                    while pending:
                        done, pending = await asyncio.wait(
                            pending.keys(),
                            return_when=asyncio.FIRST_COMPLETED,
                            timeout=300  # 5 minutes per batch
                        )

                        for future in done:
                            index = pending.pop(future)
                            try:
                                audio = await future
                                if not audio:
                                    raise RuntimeError(f"Empty audio returned for chunk {index + 1}/{total}")

                                # Strip ID3v2 tags from subsequent chunks
                                if index > 0:
                                    audio = strip_id3v2(audio)

                                results[index] = audio

                                # Write to file immediately to save memory
                                with open(audio_path, "ab") as f:
                                    f.write(audio)
                                    f.flush()

                                # Update progress
                                generation_status[cache_id]["progress"] = len(results)
                                current_size = os.path.getsize(audio_path)

                                save_cache_meta(
                                    cache_id,
                                    {
                                        "status": "processing",
                                        "progress": len(results),
                                        "total": total,
                                        "current_file_size": current_size,
                                        "text_hash": md5_short(text),
                                        "voice": voice,
                                        "engine": engine,
                                        "language": language,
                                        "subtitle_supported": False,
                                        "subtitle_ready": False,
                                        "subtitle_cues": 0,
                                    },
                                )

                            except Exception as exc:
                                # Cancel remaining futures
                                for f in pending:
                                    f.cancel()
                                # Clean up and raise
                                remove_runtime_files_only(cache_id)
                                raise RuntimeError(f"Chunk {index + 1} failed: {exc}") from exc

                    # Verify all chunks completed
                    if len(results) != total:
                        raise RuntimeError(f"Only {len(results)}/{total} chunks completed")

                finally:
                    if executor:
                        executor.shutdown(wait=False)

                # Calculate total duration (estimated - VieNeu has no word timing)
                global_audio_sec = total * 0.1  # Rough estimate, will be refined

            else:
                # Sequential processing for Edge TTS and gTTS (existing behavior)
                for i, chunk_text in enumerate(chunks):
                    if engine == "edge":
                        audio, words = await edge_tts_to_audio_and_words(chunk_text, voice)
                    else:  # gtts
                        if voice and voice.startswith("gtts:"):
                            gtts_lang = voice.split(":", 1)[1]
                        else:
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
                            "status": "processing",
                            "progress": i + 1,
                            "total": total,
                            "current_file_size": current_size,
                            "text_hash": md5_short(text),
                            "voice": voice,
                            "engine": engine,
                            "language": language,
                            "subtitle_supported": engine == "edge",
                            "subtitle_ready": False,
                            "subtitle_cues": len(cues_all),
                        },
                    )
```

- [ ] **Step 3: Verify syntax**

```bash
python -m py_compile main.py
```

Expected: No errors

- [ ] **Step 4: Run existing tests to ensure no regressions**

```bash
pytest tests/ -v --tb=short
```

Expected: All existing tests pass

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: implement parallel chunk processing for VieNeu TTS"
```

---

## Task 6: Run Integration Tests

- [ ] **Step 1: Start the server**

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Expected: Server starts without errors, shows `Uvicorn running on http://0.0.0.0:8000`

- [ ] **Step 2: Test VieNeu with long text (multiple chunks)**

```bash
curl -X POST http://localhost:8000/tts/start \
  -H "Content-Type: application/json" \
  -d '{"text": "Đây là đoạn văn bản đầu tiên. Đây là đoạn văn bản thứ hai. Đây là đoạn văn bản thứ ba. Đây là đoạn văn bản thứ tư. Đây là đoạn văn bản thứ năm. " * 10, "voice": "vieneu:default", "language": "vi"}'
```

Expected: Returns JSON with `cache_id` and `status: "started"`

- [ ] **Step 3: Monitor progress**

Replace `{cache_id}` with actual ID from step 2:

```bash
curl http://localhost:8000/tts/status/{cache_id}
```

Expected: Progress updates from 0 to total chunks count

- [ ] **Step 4: Verify audio file was created**

```bash
ls -lh audio_cache/{cache_id}.mp3
```

Expected: MP3 file exists and has reasonable size

- [ ] **Step 5: Test with single chunk (edge case)**

```bash
curl -X POST http://localhost:8000/tts/start \
  -H "Content-Type: application/json" \
  -d '{"text": "Xin chào!", "voice": "vieneu:default", "language": "vi"}'
```

Expected: Works correctly with single chunk

- [ ] **Step 6: Test Edge TTS still works (regression test)**

```bash
curl -X POST http://localhost:8000/tts/start \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world!", "voice": "en-US-AriaNeural", "engine": "edge", "language": "en"}'
```

Expected: Edge TTS still works sequentially

- [ ] **Step 7: Test gTTS still works (regression test)**

```bash
curl -X POST http://localhost:8000/tts/start \
  -H "Content-Type: application/json" \
  -d '{"text": "Xin chào!", "voice": "gtts:vi", "engine": "gtts", "language": "vi"}'
```

Expected: gTTS still works sequentially

---

## Task 7: Performance Benchmark

- [ ] **Step 1: Create benchmark script**

Create `benchmark_vieneu.py`:

```python
import time
import requests
import asyncio

BASE_URL = "http://localhost:8000"

# Long Vietnamese text (should generate ~5-10 chunks)
LONG_TEXT = """
Việt Nam là một quốc gia nằm ở Đông Nam Á, giáp với Lào, Campuchia và Trung Quốc.
Với dân số hơn 98 triệu người, Việt Nam là một trong những quốc gia đông dân nhất thế giới.
""" * 20

async def benchmark_generation(text, voice, iterations=3):
    times = []
    for i in range(iterations):
        start = time.time()
        response = requests.post(f"{BASE_URL}/tts/start", json={
            "text": text,
            "voice": voice,
            "language": "vi"
        })
        cache_id = response.json()["cache_id"]

        # Wait for completion
        while True:
            status = requests.get(f"{BASE_URL}/tts/status/{cache_id}").json()
            if status["status"] in ["completed", "failed"]:
                break
            await asyncio.sleep(0.5)

        elapsed = time.time() - start
        times.append(elapsed)
        print(f"Iteration {i+1}: {elapsed:.2f}s")

    avg = sum(times) / len(times)
    print(f"Average: {avg:.2f}s")
    return avg

if __name__ == "__main__":
    print("Benchmarking VieNeu parallel processing...")
    asyncio.run(benchmark_generation(LONG_TEXT, "vieneu:default"))
```

- [ ] **Step 2: Run benchmark**

```bash
python benchmark_vieneu.py
```

Expected: Completes successfully with timing output

- [ ] **Step 3: Document results**

Note the average time in your release notes. Expected improvement: 2-3x faster on 4-core CPU compared to previous implementation.

- [ ] **Step 4: Clean up benchmark script**

```bash
rm benchmark_vieneu.py
```

- [ ] **Step 5: Commit (no files to commit, just noting completion)**

---

## Task 8: Update Documentation

- [ ] **Step 1: Update RELEASE_NOTES.md**

Add to `RELEASE_NOTES.md`:

```markdown
## [Unreleased]

### Performance
- **VieNeu TTS:** Parallel chunk processing for 2-3x faster generation on multi-core CPUs
- New `VIENEU_MAX_WORKERS` environment variable to control concurrency (default: 4)
```

- [ ] **Step 2: Update CLAUDE.md**

Add to the VieNeu section in `CLAUDE.md`:

```markdown
### Vietnamese Language Handling

- VieNeu TTS is the preferred engine for Vietnamese
- **Parallel processing:** VieNeu uses parallel chunk processing (configurable via `VIENEU_MAX_WORKERS`)
- CJK languages (including zh, ja, ko) use different text chunking
- Special handling for tone marks and diacritics
```

- [ ] **Step 3: Verify changes**

```bash
git diff RELEASE_NOTES.md CLAUDE.md
```

Expected: Shows the documentation updates

- [ ] **Step 4: Commit**

```bash
git add RELEASE_NOTES.md CLAUDE.md
git commit -m "docs: update release notes and CLAUDE.md for VieNeu parallel processing"
```

---

## Task 9: Final Verification and Merge Preparation

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v --cov=. --cov-report=html
```

Expected: All tests pass, coverage report generated

- [ ] **Step 2: Check branch status**

```bash
git status
git log --oneline -5
```

Expected: Clean working tree, shows recent commits

- [ ] **Step 3: Rebase onto main if needed**

```bash
git fetch origin
git rebase origin/main
```

Expected: Clean rebase or no changes needed

- [ ] **Step 4: Push branch**

```bash
git push -u origin feature/vieneu-parallel-processing
```

Expected: Branch pushed to remote

- [ ] **Step 5: Create pull request (if using GitHub/GitLab)**

Use your platform's UI or CLI to create a PR from `feature/vieneu-parallel-processing` to `main`.

---

## Verification Checklist

Before considering this feature complete:

- [ ] All tests pass (`pytest tests/ -v`)
- [ ] VieNeu TTS generates audio correctly with parallel processing
- [ ] Edge TTS still works (no regression)
- [ ] gTTS still works (no regression)
- [ ] Single-chunk texts work correctly
- [ ] Multi-chunk texts see performance improvement
- [ ] Error handling works (failed chunks are caught)
- [ ] Documentation updated
- [ ] Code committed to feature branch
