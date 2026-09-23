"""Telephony audio helpers: 16-bit little-endian PCM <-> float, resampling, Exotel-sized chunks."""

from __future__ import annotations

import re
from math import gcd

import numpy as np
from scipy.signal import resample_poly

FRAME_S = 0.02                     # endpointing frame (20 ms)
VOX_SR = 16000                     # Voxera's speech-recognition rate
TTS_SR = 24000                     # Priya's output rate


def pcm16_to_float(data: bytes) -> np.ndarray:
    if len(data) % 2:
        data = data[:-1]
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(a: np.ndarray) -> bytes:
    return (np.clip(np.asarray(a, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def resample(a: np.ndarray, src: int, dst: int) -> np.ndarray:
    a = np.asarray(a, dtype=np.float32)
    if src == dst or len(a) == 0:
        return a
    g = gcd(src, dst)
    return resample_poly(a, dst // g, src // g).astype(np.float32)


def exotel_chunks(pcm: bytes, sample_rate: int, chunk_ms: int = 100) -> list:
    """Split PCM into Exotel-legal chunks: each a multiple of 320 bytes and at least 3,200 bytes.
    The final chunk is padded with silence so no chunk breaks the rule (odd sizes make audible gaps)."""
    # Exotel's minimum is 3,200 BYTES (a byte rule: 200 ms at 8 kHz, 100 ms at 16 kHz), always a multiple of 320
    size = max(3200, int(sample_rate * 2 * chunk_ms / 1000))
    size -= size % 320
    out = []
    for i in range(0, len(pcm), size):
        c = pcm[i:i + size]
        if len(c) % 320:
            c += b"\x00" * (320 - len(c) % 320)
        out.append(c)
    return out


_SENT = re.compile(r"(?<=[.?!।])\s+")


def split_sentences(text: str) -> list:
    """Sentence pieces (English / Devanagari danda). The phone channel and the audio-cache builder must agree on this."""
    return [p.strip() for p in _SENT.split(text or "") if p.strip()]


def phone_level(x: np.ndarray, target_rms: float = 0.12, ceiling: float = 0.85, max_gain: float = 3.0) -> np.ndarray:
    """Bring a spoken sentence to a steady, phone-friendly loudness and soft-limit the peaks (never clips)."""
    x = np.asarray(x, dtype=np.float32)
    act = x[np.abs(x) > 0.01]
    if len(act) < 80:
        return x
    rms = float(np.sqrt(np.mean(act * act)))
    y = x * min(max_gain, target_rms / max(rms, 1e-4))
    return (ceiling * np.tanh(y / ceiling)).astype(np.float32)


def level_for_stt(x: np.ndarray, target_rms: float = 0.08, max_gain: float = 10.0) -> np.ndarray:
    """Phone speech is often quiet, and speech recognition guesses (or hallucinates) on quiet audio: bring the caller's
    sentence to a healthy, steady level first. Loud audio is left alone (gain never below 1)."""
    x = np.asarray(x, dtype=np.float32)
    act = x[np.abs(x) > 0.004]
    if len(act) < 160:
        return x
    rms = float(np.sqrt(np.mean(act * act)))
    gain = min(max_gain, max(1.0, target_rms / max(rms, 1e-4)))
    return np.clip(x * gain, -0.99, 0.99).astype(np.float32)
