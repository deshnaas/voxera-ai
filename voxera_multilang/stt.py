"""Multilingual speech-to-text: language ID + Hindi/Marathi transcription + English translation.

Uses the SAME Whisper 'small' weights already on disk, converted to int8 CTranslate2 by build_fast_stt.py
(~1.6 s per Hindi turn on this CPU vs 4-8 s for openai-whisper). English turns keep using the existing base.en path
in voxera_core (see integration.py), so English behaviour and latency are unchanged.

Three jobs, all on one loaded model:
  detect(audio)            -> {lang: probability}   (cheap: one encoder pass + one decoder step)
  decode(audio, lang)      -> text in that language's script
  translate(audio, lang)   -> English text (Whisper's translate task) - lets the English-only safety / triage /
                              care logic understand Hindi and Marathi
"""

from __future__ import annotations

import os
import re
import threading
from typing import Optional

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(HERE, ".model_cache", "whisper-small-int8")
SR = 16000


class MultiSTT:
    def __init__(self, model_dir: Optional[str] = None, workers: int = 2, threads: int = 4):
        self.model_dir = model_dir or os.getenv("VOXERA_MULTI_STT_DIR") or DEFAULT_DIR
        self.workers, self.threads = workers, threads
        self._model = None
        self._lock = threading.Lock()
        self.error = ""

    @property
    def available(self) -> bool:
        if not os.path.exists(os.path.join(self.model_dir, "model.bin")):
            self.error = "model not built (run: python -m voxera_multilang.build_fast_stt)"
            return False
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            self.error = "faster-whisper not installed"
            return False
        return True

    def load(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(self.model_dir, device="cpu", compute_type="int8",
                                           cpu_threads=self.threads, num_workers=self.workers)
                self._model.transcribe(np.zeros(SR, dtype=np.float32), language="en", beam_size=1)   # warm
        return self._model

    # ---- language ID -----------------------------------------------------------------------------
    def detect(self, audio) -> dict:
        m = self.load()
        try:
            _, _, probs = m.detect_language(np.asarray(audio, dtype=np.float32), language_detection_segments=1)
        except Exception:                                   # noqa: BLE001
            return {}
        return {code: float(p) for code, p in probs}

    # ---- text ------------------------------------------------------------------------------------
    def _run(self, audio, *, language: str, task: str) -> str:
        m = self.load()
        segs, _ = m.transcribe(
            np.asarray(audio, dtype=np.float32), language=language, task=task, beam_size=1, temperature=0,
            condition_on_previous_text=False, without_timestamps=True, no_speech_threshold=0.5,
            log_prob_threshold=-1.0, compression_ratio_threshold=2.3, vad_filter=False)
        text = " ".join(s.text.strip() for s in segs).strip()
        return re.sub(r"\s+", " ", text)

    def decode(self, audio, lang: str = "hi") -> str:
        """Decode with the GIVEN language's own token. Forcing the Hindi token on Marathi speech biases the
        decoder's word choices toward Hindi spellings (measured: Marathi "aahe" comes out as Hindi "hai") -
        exactly what then fools word-based Hindi/Marathi detection. Pass the caller's actual (or candidate)
        language; integration.py tries both when it isn't sure yet."""
        return self._run(audio, language=lang if lang in ("en", "hi", "mr") else "hi", task="transcribe")

    def translate(self, audio, lang: str = "hi") -> str:
        return self._run(audio, language=lang if lang in ("en", "hi", "mr") else "hi", task="translate")
