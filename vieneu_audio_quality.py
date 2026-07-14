"""
VieNeu Audio Quality Enhancement Module

Provides audio post-processing functions for improving TTS output quality:
- Fade effects at chunk boundaries
- Loudness normalization (EBU R128)
- Configurable bitrate MP3 conversion
- Lossless WAV export
"""

import os
import io
import wave
import logging
from typing import Optional, Literal
import numpy as np
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

logger = logging.getLogger("ebook2audio")


# Configuration constants
VIENEU_SAMPLE_RATE = int(os.getenv("VIENEU_SAMPLE_RATE", "48000"))  # VieNeu v3 Turbo outputs 48kHz
VIENEU_CHANNELS = 1  # Mono
VIENEU_SAMPLE_WIDTH = 2  # int16

# Bitrate options
AudioQuality = Literal["standard", "high", "lossless"]

BITRATE_MAP = {
    "standard": "128k",
    "high": "192k",
    "lossless": None,  # WAV format
}

# Default settings
DEFAULT_FADE_DURATION_MS = 10
DEFAULT_TARGET_LOUDNESS = -16.0  # EBU R128 target (dB)
DEFAULT_NORMALIZATION_THRESHOLD_DB = 3.0  # Skip normalization if within this range


def calculate_rms_loudness(audio: np.ndarray) -> float:
    """
    Calculate RMS loudness in dBFS (decibels relative to full scale).

    Args:
        audio: Input audio (int16 numpy array)

    Returns:
        Loudness in dBFS, or -100.0 for empty/silent audio
    """
    if len(audio) == 0:
        return -100.0

    rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
    if rms < 1e-8:
        return -100.0

    # dBFS = 20 * log10(rms / max_amplitude)
    # max_amplitude for int16 is 32768
    return 20 * np.log10(rms / 32768.0)


def smart_normalize(
    audio: np.ndarray,
    target_loudness: float = DEFAULT_TARGET_LOUDNESS,
    threshold_db: float = DEFAULT_NORMALIZATION_THRESHOLD_DB,
) -> np.ndarray:
    """
    Normalize audio only if loudness differs from target by threshold.

    This is a fast alternative to EBU R128 normalization that skips processing
    when audio is already close to the target loudness.

    Args:
        audio: Input audio (int16 numpy array)
        target_loudness: Target loudness in dBFS
        threshold_db: Skip normalization if within this range of target

    Returns:
        Normalized audio or original if within threshold
    """
    if len(audio) == 0:
        return audio

    current_loudness = calculate_rms_loudness(audio)

    # Skip normalization if already close to target
    if abs(current_loudness - target_loudness) < threshold_db:
        return audio

    # Calculate required gain
    gain_db = target_loudness - current_loudness
    gain_linear = 10 ** (gain_db / 20)

    # Apply gain with clipping protection
    result = audio.astype(np.float32) * gain_linear
    result = np.clip(result, -32768, 32767)

    return result.astype(np.int16)


def cosine_crossfade(
    segment1: np.ndarray,
    segment2: np.ndarray,
    overlap_samples: int = int(VIENEU_SAMPLE_RATE * 0.1),  # 100ms
) -> np.ndarray:
    """
    Crossfade two audio segments using cosine curve for smooth transition.

    Args:
        segment1: First audio segment (float32 numpy array)
        segment2: Second audio segment (float32 numpy array)
        overlap_samples: Number of samples to overlap (default 100ms)

    Returns:
        Crossfaded audio segment
    """
    if len(segment1) < overlap_samples or len(segment2) < overlap_samples:
        # Segments too short for overlap, just concatenate
        return np.concatenate([segment1, segment2])

    # Calculate crossfade curve using cosine
    # Formula: 0.5 * (1 - cos(π * t / duration))
    t = np.arange(overlap_samples)
    fade_out = 0.5 * (1 + np.cos(np.pi * t / overlap_samples))
    fade_in = 0.5 * (1 - np.cos(np.pi * t / overlap_samples))

    # Apply fades
    segment1_copy = segment1.copy()
    segment2_copy = segment2.copy()

    segment1_copy[-overlap_samples:] *= fade_out
    segment2_copy[:overlap_samples] *= fade_in

    # Combine: take non-overlap part of segment1 + overlapped region + non-overlap part of segment2
    result = np.concatenate([
        segment1_copy[:-overlap_samples],
        segment1_copy[-overlap_samples:] + segment2_copy[:overlap_samples],
        segment2_copy[overlap_samples:]
    ])

    return result


def apply_fade_effects(
    audio_array: np.ndarray,
    sample_rate: int = VIENEU_SAMPLE_RATE,
    fade_in_ms: int = DEFAULT_FADE_DURATION_MS,
    fade_out_ms: int = DEFAULT_FADE_DURATION_MS,
) -> np.ndarray:
    """
    Apply fade in/out to audio to prevent clicks at chunk boundaries.

    Args:
        audio_array: int16 numpy array of audio samples
        sample_rate: Sample rate in Hz
        fade_in_ms: Fade in duration in milliseconds
        fade_out_ms: Fade out duration in milliseconds

    Returns:
        Audio array with fade effects applied (in-place modified)
    """
    if len(audio_array) == 0:
        return audio_array

    fade_in_samples = int(sample_rate * fade_in_ms / 1000)
    fade_out_samples = int(sample_rate * fade_out_ms / 1000)

    # Ensure we don't exceed array bounds
    fade_in_samples = min(fade_in_samples, len(audio_array))
    fade_out_samples = min(fade_out_samples, len(audio_array))

    # Apply fade in (linear crossfade)
    if fade_in_samples > 0:
        fade_curve = np.linspace(0, 1, fade_in_samples)
        audio_array[:fade_in_samples] = (audio_array[:fade_in_samples] * fade_curve).astype(np.int16)

    # Apply fade out
    if fade_out_samples > 0:
        fade_curve = np.linspace(1, 0, fade_out_samples)
        audio_array[-fade_out_samples:] = (audio_array[-fade_out_samples:] * fade_curve).astype(np.int16)

    return audio_array


def normalize_audio_loudness(
    audio_data: bytes,
    target_loudness: float = DEFAULT_TARGET_LOUDNESS,
    sample_rate: int = VIENEU_SAMPLE_RATE,
    channels: int = VIENEU_CHANNELS,
    sample_width: int = VIENEU_SAMPLE_WIDTH,
) -> bytes:
    """
    Normalize audio to target loudness using EBU R128 standard.

    This uses pydub's built-in normalization which approximates EBU R128.

    Args:
        audio_data: Raw audio bytes (WAV format)
        target_loudness: Target loudness in dB (typically -16 for EBU R128)
        sample_rate: Sample rate in Hz
        channels: Number of audio channels
        sample_width: Bytes per sample

    Returns:
        Normalized audio bytes in WAV format
    """
    try:
        audio = AudioSegment(
            data=audio_data,
            sample_width=sample_width,
            frame_rate=sample_rate,
            channels=channels
        )

        # Calculate current loudness and adjust
        # pydub's normalize_to_headroom and headroom can be used
        # For EBU R128, we target -16 LUFS
        change_in_dBFS = target_loudness - audio.dBFS

        if abs(change_in_dBFS) > 0.5:  # Only adjust if difference is significant
            normalized = audio.apply_gain(change_in_dBFS)
        else:
            normalized = audio

        # Export back to bytes
        output = io.BytesIO()
        normalized.export(output, format="wav")
        return output.getvalue()

    except Exception as e:
        logger.warning(f"Audio normalization failed: {e}. Returning original audio.")
        return audio_data


def float32_to_int16(audio_array: np.ndarray) -> np.ndarray:
    """
    Convert float32 audio in range [-1, 1] to int16 range [-32768, 32767].

    Args:
        audio_array: float32 numpy array

    Returns:
        int16 numpy array
    """
    # Clip to prevent overflow
    clipped = np.clip(audio_array, -1.0, 1.0)
    # Convert to int16
    return (clipped * 32767).astype(np.int16)


def int16_to_wav_bytes(
    audio_int16: np.ndarray,
    sample_rate: int = VIENEU_SAMPLE_RATE,
    channels: int = VIENEU_CHANNELS,
) -> bytes:
    """
    Convert int16 numpy array to WAV format bytes.

    Args:
        audio_int16: int16 numpy array of audio samples
        sample_rate: Sample rate in Hz
        channels: Number of audio channels

    Returns:
        WAV format bytes
    """
    with io.BytesIO() as wav_buffer:
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)  # 2 bytes per sample (int16)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_int16.tobytes())
        return wav_buffer.getvalue()


def convert_to_mp3(
    wav_data: bytes,
    bitrate: str = "128k",
    sample_rate: int = VIENEU_SAMPLE_RATE,
) -> bytes:
    """
    Convert WAV audio to MP3 with specified bitrate.

    Args:
        wav_data: WAV format audio bytes
        bitrate: MP3 bitrate (e.g., "64k", "128k", "192k")
        sample_rate: Sample rate in Hz

    Returns:
        MP3 format bytes
    """
    wav_audio = AudioSegment(
        data=wav_data,
        sample_width=2,
        frame_rate=sample_rate,
        channels=1
    )

    mp3_buffer = io.BytesIO()
    wav_audio.export(mp3_buffer, format="mp3", bitrate=bitrate)
    return mp3_buffer.getvalue()


def convert_to_wav(
    audio_array: np.ndarray,
    sample_rate: int = VIENEU_SAMPLE_RATE,
    channels: int = VIENEU_CHANNELS,
) -> bytes:
    """
    Convert float32 audio array directly to WAV bytes (lossless).

    Args:
        audio_array: float32 numpy array in range [-1, 1]
        sample_rate: Sample rate in Hz
        channels: Number of audio channels

    Returns:
        WAV format bytes (lossless)
    """
    audio_int16 = float32_to_int16(audio_array)
    return int16_to_wav_bytes(audio_int16, sample_rate, channels)


def add_silence(
    audio_data: bytes,
    silence_duration_ms: int,
    sample_rate: int = VIENEU_SAMPLE_RATE,
    sample_width: int = VIENEU_SAMPLE_WIDTH,
    channels: int = VIENEU_CHANNELS,
) -> bytes:
    """
    Add silence to the end of audio.

    Args:
        audio_data: WAV format audio bytes
        silence_duration_ms: Duration of silence in milliseconds
        sample_rate: Sample rate in Hz
        sample_width: Bytes per sample
        channels: Number of audio channels

    Returns:
        Audio bytes with silence appended
    """
    if silence_duration_ms <= 0:
        return audio_data

    # Create silence using pydub
    silence = AudioSegment.silent(
        duration=silence_duration_ms,
        frame_rate=sample_rate
    )

    audio = AudioSegment(
        data=audio_data,
        sample_width=sample_width,
        frame_rate=sample_rate,
        channels=channels
    )

    combined = audio + silence

    output = io.BytesIO()
    combined.export(output, format="wav")
    return output.getvalue()


def process_vienneu_audio(
    audio_array: np.ndarray,
    audio_quality: AudioQuality = "standard",
    apply_normalization: bool = True,
    apply_fade: bool = True,
    fade_ms: int = DEFAULT_FADE_DURATION_MS,
    silence_ms: int = 0,
) -> tuple[bytes, str]:
    """
    Process VieNeu audio with quality enhancements.

    Now uses:
    - Smart normalization (skip if not needed)
    - VBR MP3 encoding for better quality/size ratio
    - Cosine crossfade for chunk boundaries (handled externally)

    Args:
        audio_array: Raw audio from VieNeu model (float32)
        audio_quality: "standard", "high", or "lossless"
        apply_normalization: Whether to normalize loudness
        apply_fade: Whether to apply fade effects
        fade_ms: Fade duration in milliseconds
        silence_ms: Silence to append in milliseconds

    Returns:
        Tuple of (audio_bytes, file_extension)
    """
    # Convert to int16
    audio_int16 = float32_to_int16(audio_array)

    # Apply smart normalization if requested
    if apply_normalization:
        audio_int16 = smart_normalize(audio_int16, target_loudness=-16.0, threshold_db=3.0)

    # Apply fade effects
    if apply_fade and fade_ms > 0:
        audio_int16 = apply_fade_effects(audio_int16, fade_in_ms=fade_ms, fade_out_ms=fade_ms)

    # Add silence if requested
    if silence_ms > 0:
        silence_samples = int(VIENEU_SAMPLE_RATE * silence_ms / 1000)
        silence = np.zeros(silence_samples, dtype=np.int16)
        audio_int16 = np.concatenate([audio_int16, silence])

    # Encode based on quality
    if audio_quality == "lossless":
        audio_bytes = int16_to_wav_bytes(audio_int16)
        file_extension = "wav"
    else:
        # Use CBR encoding to avoid MP3 concatenation issues
        bitrate = "128k" if audio_quality == "standard" else "192k"
        audio_bytes = encode_mp3_cbr(audio_int16, bitrate=bitrate)
        file_extension = "mp3"

    return audio_bytes, file_extension


# Keep old name as alias for backward compatibility
process_vieneu_audio = process_vienneu_audio


def get_file_extension(audio_quality: AudioQuality) -> str:
    """Get file extension for given audio quality setting."""
    return "wav" if audio_quality == "lossless" else "mp3"


def get_bitrate_for_quality(audio_quality: AudioQuality) -> Optional[str]:
    """Get bitrate string for audio quality setting."""
    return BITRATE_MAP.get(audio_quality)


def encode_mp3_cbr(
    audio: np.ndarray,
    bitrate: str = "128k",
    sample_rate: int = VIENEU_SAMPLE_RATE,
) -> bytes:
    """
    Encode audio to MP3 using CBR (Constant Bitrate).

    CBR encoding avoids VBR/Xing header issues that cause
    some MP3 players to stop playback after the first chunk
    when concatenating multiple MP3 files.

    Args:
        audio: Input audio (int16 numpy array)
        bitrate: CBR bitrate (e.g., "128k", "192k")
        sample_rate: Sample rate in Hz

    Returns:
        MP3 encoded bytes
    """
    from pydub import AudioSegment
    import io

    # Convert numpy to AudioSegment
    audio_segment = AudioSegment(
        data=audio.tobytes(),
        sample_width=audio.dtype.itemsize,
        frame_rate=sample_rate,
        channels=1  # Mono
    )

    # Export with CBR encoding (no VBR parameters)
    output = io.BytesIO()
    audio_segment.export(
        output,
        format='mp3',
        bitrate=bitrate
    )

    return output.getvalue()


# Keep old name for compatibility but redirect to CBR
encode_mp3_vbr = encode_mp3_cbr
