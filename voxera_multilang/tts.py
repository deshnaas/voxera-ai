"""Language-aware speech synthesis: English, Hindi and Marathi with the SAME Priya voice.

Uses the Kokoro model already loaded by voxera_core (kokoro_hindi_final.pth) - no new TTS engine.
  * en : goonj English G2P
  * hi : misaki/espeak 'hi'
  * mr : misaki/espeak 'mr'  (the Hindi-trained model reading Marathi phonemes; quality is checked by a
         synthesize -> transcribe round trip in the tests, and needs a native-speaker listen before wide use)
Mixed text (a medicine name in Latin script inside a Devanagari sentence) is split by script and each run is
spoken with its own G2P, then joined with a short pause.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from typing import Optional

import numpy as np

SR = 24000
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".model_cache", "tts")
_DEV = re.compile(r"[ऀ-ॿ]")
_lock = threading.Lock()
_g2p: dict = {}


def split_scripts(text: str) -> list:
    """[(lang_hint, chunk)] where lang_hint is 'dev' (Devanagari) or 'lat' (Latin/digits)."""
    out: list = []
    for tok in re.findall(r"\S+|\s+", text):
        kind = "dev" if _DEV.search(tok) else ("lat" if re.search(r"[A-Za-z0-9]", tok) else None)
        if kind is None:                                   # spaces / punctuation join the current run
            if out:
                out[-1] = (out[-1][0], out[-1][1] + tok)
            continue
        if out and out[-1][0] == kind:
            out[-1] = (kind, out[-1][1] + tok)
        else:
            out.append((kind, tok))
    return [(k, c.strip()) for k, c in out if c.strip()]


def _espeak(lang: str):
    with _lock:
        if lang not in _g2p:
            from misaki import espeak
            _g2p[lang] = espeak.EspeakG2P(language=lang)
        return _g2p[lang]


def phonemize(text: str, lang: str, goonj=None) -> str:
    if lang == "en":
        return goonj.phonemize(text, "en") if goonj else _espeak("en-us")(text)[0]
    ph, _ = _espeak("hi" if lang == "hi" else "mr")(text)
    return ph


class MultiTTS:
    """Wraps voxera_core's loaded Kokoro model. Call after vx.load_tts()."""

    def __init__(self, core):
        self.core = core
        self._voices: dict = {}
        self._synth_lock = threading.RLock()

    def _ref(self, n_phonemes: int, voice: str = "priya"):
        if voice == "priya":
            return self.core._priya_embedding(n_phonemes).to("cpu")
        v = self._voices.get(voice)
        if v is None:
            path = self.core._goonj.resolve_voice(voice)
            v = self.core._torch.load(path, map_location="cpu", weights_only=True)
            self._voices[voice] = v
        idx = max(0, min(n_phonemes - 1, v.shape[0] - 1))
        r = v[idx]
        return (r if r.ndim == 2 else r.unsqueeze(0)).to("cpu")

    def _run(self, text: str, lang: str, voice: str, speed: float) -> Optional[np.ndarray]:
        ph = phonemize(text, lang, self.core._goonj)
        if not ph:
            return None
        ph = ph[:509]
        with self.core._torch.inference_mode():
            audio = self.core._tts_model(ph, self._ref(len(ph), voice), speed=speed)
        a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
        a = np.asarray(a, dtype=np.float32).reshape(-1)
        return a if len(a) and np.isfinite(a).all() else None

    # ---- disk cache: fixed phrases are rendered ONCE (build_tts_cache.py) and loaded instantly at run time ----------
    def _path(self, text: str, lang: str, voice: str, speed: float) -> str:
        key = hashlib.sha1(f"{lang}|{voice}|{speed:.2f}|{text}".encode("utf-8")).hexdigest()
        return os.path.join(CACHE_DIR, f"{key}.npy")

    def load_cached(self, text: str, lang: str, voice: str = "priya", speed: Optional[float] = None) -> Optional[np.ndarray]:
        speed = speed if speed is not None else self.core.TTS_SPEED
        try:
            return np.load(self._path(text, lang, voice, speed))
        except Exception:                                   # noqa: BLE001
            return None

    def synth(self, text: str, lang: str, voice: str = "priya", speed: Optional[float] = None) -> Optional[np.ndarray]:
        """text -> float32 mono @ 24 kHz. Devanagari runs use `lang` (hi/mr); Latin runs use English.
        Serialised (the phonemizer and model are not meant to be called from two threads at once)."""
        speed = speed if speed is not None else self.core.TTS_SPEED
        cached = self.load_cached(text, lang, voice, speed)
        if cached is not None:
            return cached
        with self._synth_lock:
            if lang == "en":
                out = self._run(text, "en", voice, speed)
            else:
                pieces = []
                for kind, chunk in split_scripts(text):
                    a = self._run(chunk, lang if kind == "dev" else "en", voice, speed)
                    if a is not None:
                        pieces.append(a)
                        pieces.append(np.zeros(int(0.06 * SR), dtype=np.float32))
                out = np.concatenate(pieces[:-1]) if pieces else None
        return out

    def render_to_cache(self, text: str, lang: str, voice: str = "priya") -> bool:
        speed = self.core.TTS_SPEED
        if self.load_cached(text, lang, voice, speed) is not None:
            return True
        a = self.synth(text, lang, voice, speed)
        if a is None:
            return False
        os.makedirs(CACHE_DIR, exist_ok=True)
        np.save(self._path(text, lang, voice, speed), a.astype(np.float32))
        return True
