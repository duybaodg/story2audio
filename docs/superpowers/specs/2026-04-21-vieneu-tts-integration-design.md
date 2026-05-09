# VieNeu-TTS Integration Design

## Context
Story2Audio currently uses Edge TTS and gTTS for text-to-speech. This design adds VieNeu-TTS as a new engine option, providing superior Vietnamese pronunciation, voice cloning, and code-switching capabilities.

## Recommended Approach: Direct Local Integration

Add VieNeu-TTS as a third TTS engine (`"vieneu"`) alongside Edge TTS and gTTS.

## Files to Modify
- `main.py` - Add VieNeu engine, voices registry, TTS function
- `pyproject.toml` - Add vieneu dependency
- `templates/index.html` - Update UI for new voices

## Design Decisions
- **Model:** 0.5B (Apache 2.0 - commercial use)
- **Voices:** Use VieNeu preset voices from the library
- **Engine:** Auto-detected from voice selection (Edge TTS remains default)
- **Subtitles:** Not supported by VieNeu (no word-level timing like Edge TTS)
- **Safety:** Edge TTS and gTTS completely untouched
- **UI:** Same dropdown, voices labeled with engine source

## Dependencies

```toml
dependencies = [
  # ... existing dependencies ...
  "vieneu>=0.1.0",  # VieNeu-TTS Python SDK
]
```

**Note:** First run will download the 0.5B model (~1-2GB) which is cached locally.

## Implementation Details

### Voice Registry Structure
```python
ALL_VOICES = {
    "vi": [
        # Edge TTS (existing)
        {"value": "vi-VN-HoaiMyNeural", "label": "Hoài Mỹ (Nữ) [Edge TTS]", "engine": "edge"},
        {"value": "vi-VN-NamMinhNeural", "label": "Nam Minh (Nam) [Edge TTS]", "engine": "edge"},
        # VieNeu (new) - fetched from library at startup
        {"value": "vieneu:default", "label": "Mặc định [VieNeu]", "engine": "vieneu"},
        # More presets from vieneu.list_preset_voices()
    ],
    # Other languages remain Edge TTS only
}
```

### Engine Auto-Detection
```python
def get_engine_from_voice(voice: str) -> str:
    """Auto-detect engine from selected voice."""
    if voice.startswith("vieneu:"):
        return "vieneu"
    return "edge"  # default
```

### VieNeu TTS Function
```python
async def vieneu_tts_to_audio(text: str, voice: str) -> bytes:
    """Generate audio using VieNeu-TTS. Returns WAV bytes."""
    from vieneu import Vieneu

    tts = Vieneu()

    # Handle preset voices
    preset_voice = None
    if voice != "vieneu:default":
        preset_id = voice.split(":", 1)[1]
        available = tts.list_preset_voices()
        for desc, name in available:
            if name == preset_id:
                preset_voice = tts.get_preset_voice(name)
                break

    # Run in thread pool (VieNeu is synchronous)
    loop = asyncio.get_running_loop()

    def _generate():
        if preset_voice:
            return tts.infer(text=text, voice=preset_voice)
        return tts.infer(text=text)

    audio_spec = await loop.run_in_executor(None, _generate)
    return tts.to_bytes(audio_spec)
```

### Subtitle Behavior

| Engine | Subtitles | Notes |
|--------|-----------|-------|
| Edge TTS | ✅ SRT, VTT, JSON cues | WordBoundary events |
| gTTS | ❌ | No timing data |
| VieNeu | ❌ | No word-level timing API |

## Testing Strategy

1. **Unit tests:**
   - Test `vieneu_tts_to_audio()` function
   - Test engine auto-detection from voice ID
   - Test cache ID generation includes engine

2. **Integration tests:**
   - Test `/tts/start` with `vieneu:default` voice
   - Test audio file generation and playback
   - Verify Edge TTS still works (regression test)

3. **Manual tests:**
   - Play generated audio files for quality
   - Test with long texts (chunking behavior)
   - Test cache hit/miss scenarios

## Verification

Run these commands to verify the implementation:

```bash
# Install dependencies
uv sync

# Run tests
pytest tests/ -v

# Test manually
curl -X POST http://localhost:8000/tts/start \
  -H "Content-Type: application/json" \
  -d '{"text": "Xin chào, đây là bài kiểm tra VieNeu TTS.", "voice": "vieneu:default"}'

# Play the generated audio
# Check /tts/status/{cache_id} for progress
```
