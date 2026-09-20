"""Voice signal analysis — a CONVERSATIONAL hint, never a diagnosis.

    captured turn audio (already in memory) ─┬─> Whisper            (unchanged)
                                             └─> VoiceSignalAnalyzer (this file, off the hot path)

Two layers, both local:
  1. Acoustic features in-process with numpy (cheap, ~ms): RMS, speech rate,
     pitch variability, signal quality.
  2. Emotion posteriors from the pretrained SpeechBrain wav2vec2-IEMOCAP model,
     running in a separate worker process (voice_worker.py).

Hard rules (enforced + tested)
  * Output labels are limited to  neutral | calm | sad | high_arousal | uncertain.
    It never says panic / anxiety / fear / angry / unstable / any diagnosis.
  * It NEVER triggers or suppresses an emergency. voxera_emergency.py stays the
    only authority; this signal only nudges how gently Voxera phrases follow-ups.
  * The IEMOCAP model has only 4 classes (neutral, angry, happy, sad), trained on
    acted US-English speech. It cannot detect "fear", and accuracy on Indian
    English/Hindi is unvalidated. So the arousal level blends model posteriors
    with acoustics, confidence is deliberately conservative, and anything weak
    degrades to ``unavailable`` / ``uncertain``.
  * If the worker is missing, slow or crashes, analysis falls back to
    acoustics-only or ``unavailable``. The call never waits on it.
  * Raw audio is not stored or logged.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import numpy as np

from .models import VoiceSignal

SR = 16000
MIN_SECONDS = 1.0
MIN_RMS = 0.002
FORBIDDEN_LABELS = {"panic", "anxiety", "anxious", "fearful", "fear", "angry", "unstable", "distressed",
                    "psychotic", "depressed", "panic attack"}
ALLOWED_EMOTION = {"neutral", "calm", "sad", "high_arousal", "uncertain"}

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_WORKER_PY = os.path.join(_HERE, ".venv_voice", "Scripts", "python.exe")
if not os.path.exists(DEFAULT_WORKER_PY):
    DEFAULT_WORKER_PY = os.path.join(_HERE, ".venv_voice", "bin", "python")


def _log(msg: str) -> None:
    print(f"[VOICE_SIGNAL] {msg}")


# ------------------------------------------------------------------
# Layer 1: acoustic features (numpy only)
# ------------------------------------------------------------------

def _frames(x: np.ndarray, size: int, hop: int) -> np.ndarray:
    if len(x) < size:
        return np.zeros((0, size), dtype=np.float32)
    n = 1 + (len(x) - size) // hop
    idx = np.arange(size)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def acoustic_features(audio: np.ndarray, sr: int = SR) -> dict:
    x = np.asarray(audio, dtype=np.float32).flatten()
    dur = len(x) / sr
    feats = {"duration": round(dur, 2), "rms": None, "speech_rate": None, "pitch_variability": None,
             "clipping": 0.0, "snr_db": None, "voiced_ratio": 0.0}
    if len(x) < int(0.3 * sr):
        return feats
    feats["rms"] = float(np.sqrt(np.mean(x ** 2)))
    feats["clipping"] = float(np.mean(np.abs(x) > 0.985))

    # energy envelope on 20 ms frames
    fr = _frames(x, int(0.025 * sr), int(0.010 * sr))
    if len(fr) < 10:
        return feats
    env = np.sqrt(np.mean(fr ** 2, axis=1))
    noise = float(np.percentile(env, 10)) + 1e-8
    sig = float(np.percentile(env, 90)) + 1e-8
    feats["snr_db"] = round(20 * np.log10(sig / noise), 1)

    # speech rate: syllable-like energy peaks per second of speech
    thr = noise + 0.25 * (sig - noise)
    speech = env > thr
    speech_time = float(np.sum(speech)) * 0.010
    from scipy.ndimage import uniform_filter1d
    from scipy.signal import find_peaks
    smooth = uniform_filter1d(env, 11)                       # ~110 ms: one bump per syllable nucleus
    peaks, _ = find_peaks(smooth, height=thr, distance=14, prominence=0.12 * (sig - noise))   # >= 140 ms apart
    peaks = len(peaks)
    if speech_time > 0.5:
        feats["speech_rate"] = round(peaks / speech_time, 2)

    # pitch variability: autocorrelation F0 on voiced frames, coefficient of variation
    f0s = []
    pf = _frames(x, int(0.04 * sr), int(0.02 * sr))
    lo, hi = int(sr / 400), int(sr / 70)
    for i, f in enumerate(pf):
        if np.sqrt(np.mean(f ** 2)) < thr:
            continue
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[len(f) - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:hi]
        if len(seg) == 0:
            continue
        k = int(np.argmax(seg)) + lo
        if ac[k] / ac[0] > 0.45:
            f0s.append(sr / k)
    feats["voiced_ratio"] = round(len(f0s) / max(1, len(pf)), 2)
    if len(f0s) >= 8:
        f0 = np.array(f0s)
        f0 = f0[(f0 > np.median(f0) * 0.6) & (f0 < np.median(f0) * 1.6)]       # drop octave errors
        if len(f0) >= 8 and f0.mean() > 0:
            feats["pitch_variability"] = round(float(np.std(f0) / np.mean(f0)), 3)
    return feats


def signal_quality(f: dict) -> str:
    if f["rms"] is None or f["duration"] < MIN_SECONDS:
        return "poor"
    if f["clipping"] > 0.02 or f["rms"] < MIN_RMS:
        return "poor"
    snr = f["snr_db"] or 0
    if snr < 8:
        return "poor"
    return "good" if snr >= 18 and f["voiced_ratio"] >= 0.15 else "fair"


def acoustic_arousal(f: dict) -> Optional[float]:
    """0..1 from speech rate + pitch variability (+ loudness weakly). None if too little data."""
    parts, w = [], []
    if f["speech_rate"] is not None:
        parts.append(min(1.0, max(0.0, (f["speech_rate"] - 2.5) / 4.0)))    # ~2.5/s calm .. ~6.5/s very fast
        w.append(0.4)
    if f["pitch_variability"] is not None:
        parts.append(min(1.0, max(0.0, (f["pitch_variability"] - 0.08) / 0.30)))
        w.append(0.4)
    if f["rms"] is not None:
        parts.append(min(1.0, max(0.0, (f["rms"] - 0.02) / 0.12)))
        w.append(0.2)
    if len(parts) < 2:
        return None
    return float(np.average(parts, weights=w))


# ------------------------------------------------------------------
# Layer 2: model worker client
# ------------------------------------------------------------------

class WorkerClient:
    """Lazy, thread-safe client for voice_worker.py. Never raises to callers."""

    def __init__(self, python: Optional[str] = None, startup_timeout: float = 240.0, call_timeout: float = 6.0):
        self.python = python or os.getenv("VOXERA_VOICE_PY") or DEFAULT_WORKER_PY
        self.startup_timeout = startup_timeout
        self.call_timeout = call_timeout
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._ready = False
        self._failed = ""
        self._starting = False

    @property
    def available(self) -> bool:
        return os.path.exists(self.python) and not self._failed

    @property
    def ready(self) -> bool:
        return self._ready and self._proc is not None and self._proc.poll() is None

    def start_async(self) -> None:
        """Warm the model in the background (call once at app boot)."""
        if self._starting or self.ready or not self.available:
            return
        self._starting = True
        threading.Thread(target=self._start, daemon=True, name="voice-worker-start").start()

    def _start(self) -> None:
        try:
            root = os.path.dirname(_HERE)
            cache = os.path.join(_HERE, ".model_cache")          # git-ignored; SpeechBrain writes its weights relative to cwd
            os.makedirs(cache, exist_ok=True)
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", HF_HUB_DISABLE_SYMLINKS="1",
                       PYTHONPATH=root + os.pathsep + os.environ.get("PYTHONPATH", ""))
            # BELOW_NORMAL priority on Windows: the emotion model must never steal CPU from
            # Whisper / Ollama / TTS while a call is running.
            flags = 0x00004000 if sys.platform == "win32" else 0
            self._proc = subprocess.Popen(
                [self.python, "-m", "voxera_patientfetch.voice_worker"], cwd=cache,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                creationflags=flags)
            line = self._readline(self.startup_timeout)
            info = json.loads(line) if line else {}
            if info.get("ready"):
                self._ready = True
                _log("emotion model ready (local, isolated process)")
            else:
                self._failed = info.get("error", "worker failed to start")
                _log(f"emotion model unavailable ({self._failed[:80]})")
        except Exception as e:                                  # noqa: BLE001
            self._failed = f"{type(e).__name__}"
            _log(f"emotion model unavailable ({self._failed})")
        finally:
            self._starting = False

    def _readline(self, timeout: float) -> str:
        box: list = []
        t = threading.Thread(target=lambda: box.append(self._proc.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        if not box:
            try:
                self._proc.kill()
            except Exception:                                   # noqa: BLE001
                pass
            self._ready = False
            raise TimeoutError("worker timeout")
        return box[0].decode("utf-8", "ignore").strip()

    def posteriors(self, audio: np.ndarray) -> Optional[dict]:
        if not self.ready:
            return None
        pcm = np.asarray(audio, dtype=np.float32).tobytes()
        with self._lock:
            try:
                self._proc.stdin.write(struct.pack(">I", len(pcm)) + pcm)
                self._proc.stdin.flush()
                resp = json.loads(self._readline(self.call_timeout))
            except Exception:                                   # noqa: BLE001
                self._ready = False
                self._failed = "worker crashed or timed out"
                return None
        return resp.get("probs") if resp.get("ok") else None

    def close(self) -> None:
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.stdin.write(struct.pack(">I", 0))
                self._proc.stdin.flush()
                self._proc.wait(timeout=3)
        except Exception:                                       # noqa: BLE001
            try:
                self._proc.kill()
            except Exception:                                   # noqa: BLE001
                pass


# ------------------------------------------------------------------
# Analyzer
# ------------------------------------------------------------------

# arousal contribution of each IEMOCAP class (a coarse, documented mapping)
_CLASS_AROUSAL = {"ang": 1.0, "hap": 0.75, "neu": 0.25, "sad": 0.10}


class VoiceSignalAnalyzer:
    def __init__(self, client: Optional[WorkerClient] = None, use_model: Optional[bool] = None):
        env = os.getenv("VOXERA_VOICE_SIGNAL", "1").strip().lower()
        self.enabled = env not in ("0", "off", "false", "no")
        self.client = client if client is not None else WorkerClient()
        self.use_model = (self.client.available if use_model is None else use_model)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voxsig")
        self._latest: Optional[VoiceSignal] = None
        self._lock = threading.Lock()

    def warm(self) -> None:
        if self.enabled and self.use_model:
            self.client.start_async()

    # ---- pure/sync ------------------------------------------------
    def analyze(self, audio, sr: int = SR) -> VoiceSignal:
        t0 = time.time()
        if not self.enabled:
            return VoiceSignal(reason="disabled")
        try:
            x = np.asarray(audio, dtype=np.float32).flatten()
            if sr != SR and len(x):
                x = np.interp(np.linspace(0, len(x) - 1, int(len(x) * SR / sr)), np.arange(len(x)), x).astype(np.float32)
            if not np.isfinite(x).all():
                return VoiceSignal(reason="invalid_audio")
            f = acoustic_features(x)
            if f["duration"] < MIN_SECONDS:
                return VoiceSignal(reason="audio_too_short", rms=f["rms"])
            q = signal_quality(f)
            if f["rms"] is not None and f["rms"] < MIN_RMS:
                return VoiceSignal(reason="too_quiet", rms=round(f["rms"], 4), signal_quality="poor")

            probs = self.client.posteriors(_model_window(x)) if (self.use_model and self.client.ready) else None
            ac = acoustic_arousal(f)
            sig = self._combine(f, q, ac, probs)
            sig.latency_ms = int((time.time() - t0) * 1000)
            return sig
        except Exception as e:                                  # noqa: BLE001
            return VoiceSignal(reason=f"error:{type(e).__name__}")

    @staticmethod
    def _combine(f: dict, quality: str, ac: Optional[float], probs: Optional[dict]) -> VoiceSignal:
        model_ar = None
        top, conf_model = None, 0.0
        if probs:
            tot = sum(probs.values()) or 1.0
            p = {k: v / tot for k, v in probs.items()}
            model_ar = sum(_CLASS_AROUSAL.get(k, 0.25) * v for k, v in p.items())
            top = max(p, key=p.get)
            srt = sorted(p.values(), reverse=True)
            conf_model = float(srt[0] - (srt[1] if len(srt) > 1 else 0))        # margin, not raw prob

        if model_ar is None and ac is None:
            return VoiceSignal(reason="insufficient_features", rms=_r(f["rms"]), signal_quality=quality)

        if model_ar is not None and ac is not None:
            score = 0.6 * model_ar + 0.4 * ac
            conf = 0.5 * conf_model + 0.5 * 0.6
        elif model_ar is not None:
            score, conf = model_ar, 0.6 * conf_model
        else:
            score, conf = ac, 0.35                          # acoustics alone are weak evidence
        qf = {"good": 1.0, "fair": 0.75, "poor": 0.4}[quality]
        conf = round(min(0.9, conf * qf), 2)

        level = "low" if score < 0.40 else "moderate" if score < 0.65 else "high"
        if model_ar is None and level == "high":
            level = "moderate"                     # acoustics alone are too weak to claim high arousal
        if quality == "poor" and conf < 0.3:
            return VoiceSignal(reason="poor_signal_quality", rms=_r(f["rms"]), signal_quality="poor",
                               speech_rate=f["speech_rate"], pitch_variability=f["pitch_variability"])

        if level == "high":
            emo = "high_arousal"
        elif top == "sad" and level == "low":
            emo = "sad"
        elif level == "low":
            emo = "calm" if (top in (None, "neu")) else "neutral"
        else:
            emo = "neutral" if top in (None, "neu", "sad") else "uncertain"
        if conf < 0.25 and level != "low":
            emo = "uncertain"

        return VoiceSignal(available=True, arousal_level=level, emotion_signal=emo, confidence=conf,
                           speech_rate=f["speech_rate"], rms=_r(f["rms"]),
                           pitch_variability=f["pitch_variability"], signal_quality=quality)

    # ---- async (production path) -------------------------------------
    def submit(self, audio, sr: int = SR, on_done: Optional[Callable[[VoiceSignal], None]] = None) -> None:
        """Fire-and-forget. The call loop never waits; read ``latest()`` next turn."""
        if not self.enabled:
            return
        a = np.array(audio, dtype=np.float32, copy=True)         # own the buffer; mic buffers get reused

        def run():
            sig = self.analyze(a, sr)
            with self._lock:
                self._latest = sig
            if sig.available:
                _log(f"arousal={sig.arousal_level} confidence={sig.confidence}")
            else:
                _log(f"unavailable ({sig.reason})")
            if on_done:
                try:
                    on_done(sig)
                except Exception:                               # noqa: BLE001
                    pass
        self._pool.submit(run)

    def latest(self) -> Optional[VoiceSignal]:
        with self._lock:
            return self._latest

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.client.close()


MODEL_WINDOW_S = float(os.getenv("VOXERA_VOICE_WINDOW", "4.0"))


def _model_window(x: np.ndarray, seconds: float = MODEL_WINDOW_S) -> np.ndarray:
    """wav2vec2 cost grows with length: send the most energetic `seconds` only."""
    n = int(seconds * SR)
    if len(x) <= n:
        return x
    hop = SR // 4
    e = np.array([float(np.sum(x[i:i + n] ** 2)) for i in range(0, len(x) - n + 1, hop)])
    i = int(np.argmax(e)) * hop
    return x[i:i + n]


def _r(v):
    return None if v is None else round(float(v), 4)


def display_label(sig: Optional[VoiceSignal]) -> str:
    """Dashboard wording. Never a diagnosis."""
    if not sig or not sig.available:
        return "Uncertain"
    return {"low": "Normal", "moderate": "Elevated", "high": "High arousal"}.get(sig.arousal_level, "Uncertain")
