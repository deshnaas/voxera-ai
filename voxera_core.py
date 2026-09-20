# ============================================================
# VOXERA — SHARED REAL-TIME VOICE ENGINE
# ============================================================
#
# One place for the proven, reusable pieces so the patient
# (voxera.py) and hospital (voxera_hospital.py) entrypoints
# stay small.
#
#   Microphone  -> energy VAD (hysteresis + pre-roll)
#               -> Whisper (base.en by default, CPU)
#               -> ONE Qwen3-1.7B call (streamed, think=False)
#               -> Priya / Kokoro TTS  (model + phonemizer warmed once)
#               -> Speaker
#
# Design choices (see README):
#   * NO separate LLM "scope" call — that was the 7s tax.
#   * Deterministic emergency layer runs first (voxera_emergency).
#   * Supabase writes are fire-and-forget on a worker thread so
#     the spoken path never waits on the database.
#   * Barge-in defaults to conservative turn-taking. True duplex
#     AEC barge-in is opt-in (VOXERA_BARGEIN=aec) and clearly
#     marked experimental — a false interruption is worse than a
#     slightly late one.
# ============================================================

import os
import re
import sys
import json
import time
import queue
import threading
from collections import deque

import numpy as np
import sounddevice as sd
import soundfile as sf
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ============================================================
# CONFIG
# ============================================================

def _env(name, default):
    v = os.getenv(name)
    return v if v is not None and v != "" else default


SAMPLE_RATE          = 16000
CHUNK_SECONDS        = 0.10
CHUNK_SIZE           = int(SAMPLE_RATE * CHUNK_SECONDS)

MIC_DEVICE           = int(_env("VOXERA_MIC_DEVICE", "1"))
OUTPUT_DEVICE_RAW    = _env("VOXERA_OUTPUT_DEVICE", "")
OUTPUT_DEVICE        = int(OUTPUT_DEVICE_RAW) if str(OUTPUT_DEVICE_RAW).strip() != "" else None

WHISPER_MODEL        = _env("VOXERA_WHISPER_MODEL", "base.en")

OLLAMA_URL           = _env("VOXERA_OLLAMA_URL", "http://localhost:11434/api/chat")
LLM_MODEL            = _env("VOXERA_LLM_MODEL", "qwen3:1.7b")
LLM_TIMEOUT          = float(_env("VOXERA_LLM_TIMEOUT", "30"))
LLM_MAX_TOKENS       = int(_env("VOXERA_LLM_MAX_TOKENS", "64"))
LLM_NUM_CTX          = int(_env("VOXERA_LLM_NUM_CTX", "2048"))
LLM_KEEP_ALIVE       = _env("VOXERA_LLM_KEEP_ALIVE", "30m")

TTS_SPEED            = float(_env("VOXERA_TTS_SPEED", "1.08"))
TTS_SAMPLE_RATE      = 24000

# VAD
CALIBRATION_SECONDS  = 1.5
MIN_SPEECH_SECONDS   = 0.40
# End-of-turn silence window. 0.8 s ~ a natural phone pause: long enough not to
# clip mid-sentence breaths, short enough that Voxera answers promptly.
END_SILENCE_SECONDS  = float(_env("VOXERA_END_SILENCE", "0.80"))
# A frame also counts as silence when it drops below this fraction of the
# patient's own speaking level this turn. This is what lets end-of-turn work in
# a room whose noise floor sits ABOVE the fixed end threshold (otherwise the
# turn never ends until MAX_TURN).
ENDPOINT_DROP_RATIO  = float(_env("VOXERA_ENDPOINT_RATIO", "0.32"))
MAX_TURN_SECONDS     = float(_env("VOXERA_MAX_TURN", "20"))
PRE_ROLL_SECONDS     = 0.50
POST_ROLL_SECONDS    = 0.15
PRE_ROLL_CHUNKS      = max(1, int(PRE_ROLL_SECONDS / CHUNK_SECONDS))

MIN_START_THRESHOLD  = 0.0045
MAX_START_THRESHOLD  = 0.0450     # raised so calibration can track a noisy room
MIN_END_THRESHOLD    = 0.0025
MAX_END_THRESHOLD    = 0.0300     # (the min(MAX, noise*1.6) clamp only bites when noise is high)
# A frame is also "silence" when it falls back to within this multiple of the
# measured idle noise floor (used only once the patient is clearly louder).
ENDPOINT_NOISE_MULT  = float(_env("VOXERA_ENDPOINT_NOISE_MULT", "2.2"))

# Hard overrides (skip calibration math). Set from mic_check.py output if the
# room is noisy or the mic gain is unusual.
FORCE_START_THRESHOLD = float(_env("VOXERA_START_THRESHOLD", "0") or 0)
FORCE_END_THRESHOLD   = float(_env("VOXERA_END_THRESHOLD", "0") or 0)

MIN_AUDIO_SECONDS    = 0.55
MIN_RMS_FOR_STT      = 0.0011
MIN_PEAK_FOR_STT     = 0.010

# Barge-in mode: "aec" (true duplex, default) or "off" (strict turn-taking)
BARGE_MODE           = _env("VOXERA_BARGEIN", "aec").strip().lower()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GOONJ_DIR = os.path.join(BASE_DIR, "models", "goonj")
if GOONJ_DIR not in sys.path:
    sys.path.insert(0, GOONJ_DIR)

TTS_OUTPUT  = os.path.join(BASE_DIR, "voxera_priya_response.wav")
DEBUG_AUDIO = os.path.join(BASE_DIR, "last_patient_audio.wav")


# ============================================================
# SMALL AUDIO HELPERS
# ============================================================

def rms(a):
    if a is None or len(a) == 0:
        return 0.0
    a = np.asarray(a, dtype=np.float32)
    return float(np.sqrt(np.mean(a * a) + 1e-12))


def peak(a):
    if a is None or len(a) == 0:
        return 0.0
    return float(np.max(np.abs(np.asarray(a, dtype=np.float32))))


def _resample(a, src, dst):
    a = np.asarray(a, dtype=np.float32).flatten()
    if src == dst or len(a) == 0:
        return a
    n = max(1, int(round(len(a) * dst / src)))
    return np.interp(
        np.linspace(0, len(a) - 1, n), np.arange(len(a)), a
    ).astype(np.float32)


# ============================================================
# LATENCY TRACKER
# ============================================================

class LatencyTracker:
    """Per-turn timing plus an end-of-call summary."""

    def __init__(self):
        self.turns = []
        self._t = {}

    def start_turn(self):
        self._t = {"t0": time.perf_counter()}

    def mark(self, name):
        self._t[name] = time.perf_counter()

    def _d(self, a, b):
        if a in self._t and b in self._t:
            return self._t[b] - self._t[a]
        return None

    def end_turn(self, extra=None):
        rec = {
            "stt":          self._d("stt_start", "stt_end"),
            "ai_first":     self._d("ai_start", "ai_first_token"),
            "ai_total":     self._d("ai_start", "ai_end"),
            "tts_first":    self._d("tts_start", "tts_first_audio"),
            "tts_total":    self._d("tts_start", "tts_end"),
            "eos_to_audio": self._d("eos", "tts_first_audio"),
            "turn_total":   self._d("t0", "turn_end"),
        }
        if extra:
            rec.update(extra)
        self.turns.append(rec)
        return rec

    @staticmethod
    def fmt(rec):
        def s(x):
            return f"{x:.2f}s" if isinstance(x, (int, float)) else "  -  "
        return (
            f"[STT] {s(rec['stt'])}   "
            f"[AI first] {s(rec['ai_first'])}  [AI] {s(rec['ai_total'])}   "
            f"[TTS first] {s(rec['tts_first'])}  [TTS] {s(rec['tts_total'])}   "
            f"[EOS->AUDIO] {s(rec['eos_to_audio'])}   "
            f"[TURN] {s(rec['turn_total'])}"
        )

    def summary(self):
        if not self.turns:
            return "No measured turns."
        keys = ["stt", "ai_first", "ai_total", "tts_first",
                "tts_total", "eos_to_audio", "turn_total"]
        out = ["", "=" * 62, "LATENCY SUMMARY  (%d turns)" % len(self.turns), "=" * 62]
        for k in keys:
            vals = [t[k] for t in self.turns if isinstance(t.get(k), (int, float))]
            if vals:
                out.append(
                    f"  {k:<14} avg {sum(vals)/len(vals):5.2f}s   "
                    f"min {min(vals):5.2f}s   max {max(vals):5.2f}s"
                )
        out.append("=" * 62)
        return "\n".join(out)


# ============================================================
# ASYNC SUPABASE WRITER  (keeps the DB off the spoken path)
# ============================================================

class AsyncWriter:
    def __init__(self, name="supabase"):
        self.q = queue.Queue()
        self.name = name
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def submit(self, fn, *args, **kwargs):
        self.q.put((fn, args, kwargs))

    def _run(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            fn, args, kwargs = item
            try:
                fn(*args, **kwargs)
            except Exception as e:
                print(f"[{self.name}] deferred write failed: {e}")
            finally:
                self.q.task_done()

    def drain(self, timeout=5.0):
        end = time.time() + timeout
        while not self.q.empty() and time.time() < end:
            time.sleep(0.05)


# ============================================================
# MODEL LOADING
# ============================================================

_whisper_model = None
_tts_model = None
_tts_voice = None
_goonj = None
_torch = None


def load_stt():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    import whisper
    print(f"[BOOT] loading Whisper '{WHISPER_MODEL}' ...")
    t = time.perf_counter()
    _whisper_model = whisper.load_model(WHISPER_MODEL)
    # warm
    _whisper_model.transcribe(
        np.zeros(SAMPLE_RATE, dtype=np.float32),
        language="en", fp16=False, temperature=0,
    )
    print(f"[BOOT] Whisper ready ({time.perf_counter()-t:.1f}s)")
    return _whisper_model


def load_tts():
    """Load Kokoro + Priya voice once and warm the espeak phonemizer."""
    global _tts_model, _tts_voice, _goonj, _torch
    if _tts_model is not None:
        return

    print("[BOOT] loading Priya / Kokoro ...")
    t = time.perf_counter()
    import torch
    from kokoro import KModel
    import kokoro_generate as goonj

    _torch = torch
    _goonj = goonj

    voice_path = goonj.resolve_voice("en_priya")
    voice = torch.load(voice_path, map_location="cpu", weights_only=True)
    if not isinstance(voice, torch.Tensor):
        voice = torch.as_tensor(voice)
    _tts_voice = voice

    _tts_model = KModel(
        repo_id="hexgrad/Kokoro-82M",
        config=str(goonj.ROOT / "config.json"),
        model=str(goonj.ROOT / "kokoro_hindi_final.pth"),
    ).to("cpu").eval()

    # Warm the espeak G2P backend (first call is ~2.5s, then ~0.1s).
    try:
        _ = goonj.phonemize("Hello, this is Voxera.", "en")
    except Exception as e:
        print(f"[BOOT] phonemizer warmup warning: {e}")

    print(f"[BOOT] Priya ready ({time.perf_counter()-t:.1f}s, "
          f"voice pack {tuple(voice.shape)})")


def _priya_embedding(phoneme_count):
    v = _tts_voice
    if v.ndim == 3:
        idx = max(0, min(phoneme_count - 1, v.shape[0] - 1))
        return v[idx]                       # (1, 256)
    if v.ndim == 2:
        idx = max(0, min(phoneme_count - 1, v.shape[0] - 1))
        return v[idx].unsqueeze(0)
    return v.unsqueeze(0)


# ============================================================
# STT
# ============================================================

def _dedupe_transcript(text):
    """Whisper loops on trailing near-silence / room noise and emits
    'no, no, no, no ...' or 'Okay. Okay. Okay. ...' or a repeated sentence.
    Collapse those runs. Handles space- AND comma/punctuation-separated
    repeats (the earlier version missed 'no, no, no')."""
    if not text:
        return text
    # collapse an immediately-repeated word, separator = spaces and/or punct
    text = re.sub(r"\b(\w+)(?:[\s,;.!?/-]+\1\b){2,}", r"\1", text,
                  flags=re.IGNORECASE)
    # collapse an immediately-repeated short phrase (up to 4 words)
    text = re.sub(r"\b((?:\w+[\s,;.!?/-]+){1,4}\w+)\b(?:[\s,;.!?/-]+\1\b){1,}",
                  r"\1", text, flags=re.IGNORECASE)
    seen = []
    out = []
    for s in re.split(r"(?<=[.!?])\s+", text):
        key = re.sub(r"\s+", " ", s.lower()).strip(" .!?,")
        if not key:
            continue
        if key in seen[-3:]:
            continue
        seen.append(key)
        out.append(s.strip())
    text = " ".join(out).strip()
    # last resort: a very long, very low-diversity transcript is noise, not
    # speech - keep just the first sentence / few words.
    words = text.split()
    if len(words) > 25 and len(set(w.lower().strip(".,!?") for w in words)) <= 4:
        first = re.split(r"(?<=[.!?])\s+", text)[0]
        text = first if len(first.split()) <= 12 else " ".join(words[:8])
    return text


def transcribe(audio, tracker=None):
    model = load_stt()
    dur = len(audio) / SAMPLE_RATE

    if dur < MIN_AUDIO_SECONDS:
        return ""
    if rms(audio) < MIN_RMS_FOR_STT and peak(audio) < MIN_PEAK_FOR_STT:
        return ""

    try:
        sf.write(DEBUG_AUDIO, audio, SAMPLE_RATE)
    except Exception:
        pass

    if tracker:
        tracker.mark("stt_start")
    t = time.perf_counter()
    kw = dict(
        language="en",
        fp16=False,
        temperature=0,
        condition_on_previous_text=False,
        no_speech_threshold=0.5,
        logprob_threshold=-1.0,
        compression_ratio_threshold=2.3,
        initial_prompt="",
        verbose=False,
    )
    try:
        try:
            result = model.transcribe(audio, hallucination_silence_threshold=1.0, **kw)
        except TypeError:
            result = model.transcribe(audio, **kw)
    except Exception as e:
        print(f"[STT] error: {e}")
        if tracker:
            tracker.mark("stt_end")
        return ""
    if tracker:
        tracker.mark("stt_end")

    text = _dedupe_transcript(re.sub(r"\s+", " ", result.get("text", "")).strip())
    # Hard cap: a real phone turn is never this long. Anything past ~90 words
    # is a Whisper loop on noise - keep the front so context/latency stay sane.
    words = text.split()
    if len(words) > 90:
        text = " ".join(words[:90]) + " ..."

    segs = result.get("segments", [])
    if segs and dur < 3.0:
        conf = 1.0 - np.mean([float(s.get("no_speech_prob", 0.0)) for s in segs])
        if conf < 0.28:
            return ""

    print(f"[STT] {time.perf_counter()-t:.2f}s  \"{text}\"")
    return text


# ============================================================
# LLM  (single streamed call, think=False)
# ============================================================

_SENTENCE_END = re.compile(r"[.!?…]['\")\]]?\s")


def _clean_reply(text):
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = text.strip()
    # Strip a leading speaker label the small model sometimes adds.
    text = re.sub(r"^\s*(voxera|assistant|ai|response)\s*[:\-]\s*", "",
                  text, flags=re.IGNORECASE)
    # Strip surrounding quotes.
    if len(text) >= 2 and text[0] in "\"'" and text[-1] in "\"'":
        text = text[1:-1].strip()
    text = text.replace("`", "").strip()
    # Drop a trailing "assistant asking permission" meta-question - Voxera
    # should give the next step, not ask the caller whether to.
    sents = re.split(r"(?<=[.!?])\s+", text)
    if len(sents) > 1 and _META_Q.search(sents[-1]):
        text = " ".join(sents[:-1]).strip()
    return text


_META_Q = re.compile(
    r"\b(should i (?:tell|recommend|advise|say|suggest|have|ask|book|let|call)"
    r"|shall i\b|would you (?:like|want) me to|do you want me to"
    r"|should i recommend|would you like me to)\b", re.IGNORECASE)


def llm_respond(system_prompt, history, patient_text,
                tracker=None, on_sentence=None,
                max_tokens=LLM_MAX_TOKENS, temperature=0.4):
    """One streamed chat call.

    history: list of {"role": "user"|"assistant", "content": str} (recent turns)
    on_sentence(str): optional callback fired as soon as a full sentence is
                      available, so TTS for sentence 1 can start while the
                      model is still writing sentence 2.
    Returns the full cleaned reply string.
    """

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-8:])
    messages.append({"role": "user", "content": patient_text})

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "stream": True,
        "think": False,
        "keep_alive": LLM_KEEP_ALIVE,
        "options": {
            "temperature": temperature,
            "top_p": 0.85,
            "num_predict": max_tokens,
            "num_ctx": LLM_NUM_CTX,
            "repeat_penalty": 1.1,
        },
    }

    if tracker:
        tracker.mark("ai_start")

    full = ""
    emitted = ""
    first = True
    try:
        with requests.post(OLLAMA_URL, json=payload, stream=True,
                           timeout=LLM_TIMEOUT) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                tok = obj.get("message", {}).get("content", "")
                if tok:
                    if first and tracker:
                        tracker.mark("ai_first_token")
                        first = False
                    full += tok
                    if on_sentence:
                        # flush complete sentences as they finish
                        while True:
                            m = _SENTENCE_END.search(full, len(emitted))
                            if not m:
                                break
                            chunk = full[len(emitted):m.end()].strip()
                            emitted = full[:m.end()]
                            cleaned = _clean_reply(chunk)
                            if cleaned:
                                on_sentence(cleaned)
                if obj.get("done"):
                    break
    except Exception as e:
        print(f"[AI] error: {e}")
        if tracker:
            tracker.mark("ai_end")
        return ""

    if tracker:
        tracker.mark("ai_end")

    cleaned_full = _clean_reply(full)

    if on_sentence:
        rest = _clean_reply(full[len(emitted):])
        if rest:
            on_sentence(rest)

    return cleaned_full


# ============================================================
# TTS
# ============================================================

_tts_cache = {}          # exact-text -> ndarray  (fixed lines: greeting, emergencies)


# --- optional multilingual speech (voxera_multilang) ------------------------------------------
# English is untouched. When a call switches to Hindi/Marathi, voxera.py sets the speech language and
# synthesize() routes through the same Kokoro model with that language's phonemizer; playback, barge-in and
# AEC are the same code path either way.
_ml_synth = None         # callable(text, lang) -> float32 mono @ 24 kHz, or None
_speak_lang = "en"


def set_speech_language(lang, synth=None):
    global _speak_lang, _ml_synth
    _speak_lang = lang or "en"
    if synth is not None:
        _ml_synth = synth


def _tts_key(text, lang=None):
    lang = _speak_lang if lang is None else lang
    k = re.sub(r"\s+", " ", (text or "").strip()).lower()
    return k if lang == "en" or _ml_synth is None else f"{lang}:{k}"


def prewarm_phrases(phrases, lang=None):
    """Pre-render fixed lines (greeting, canned emergency responses) so they
    play instantly during the call. `lang` pre-renders them in Hindi/Marathi."""
    load_tts()
    for p in phrases:
        if not p:
            continue
        k = _tts_key(p, lang or "en")
        if k in _tts_cache:
            continue
        a = _ml_synth(p, lang) if (lang and lang != "en" and _ml_synth is not None) else _synth_raw(p)
        if a is not None:
            _tts_cache[k] = a
    print(f"[BOOT] pre-rendered {len(_tts_cache)} fixed phrase(s)")


def _synth_raw(text):
    phonemes = _goonj.phonemize(text, "en")
    if not phonemes:
        return None
    phonemes = phonemes[:509]
    ref_s = _priya_embedding(len(phonemes)).to("cpu")
    with _torch.inference_mode():
        audio = _tts_model(phonemes, ref_s, speed=TTS_SPEED)
    a = (audio.detach().cpu().numpy()
         if hasattr(audio, "detach") else np.asarray(audio))
    a = np.asarray(a, dtype=np.float32).squeeze()
    if a.ndim != 1:
        a = a.reshape(-1)
    if len(a) == 0 or not np.isfinite(a).all():
        return None
    return a


def synthesize(text, tracker=None):
    """text -> float32 mono ndarray @ 24 kHz  (None on failure)."""
    load_tts()
    if not text or not text.strip():
        return None
    if len(text.split()) > 120:
        text = " ".join(text.split()[:120])

    cached = _tts_cache.get(_tts_key(text))
    if cached is not None:
        if tracker and "tts_start" not in tracker._t:
            tracker.mark("tts_start")
        print(f"[TTS] cached  ({len(cached)/TTS_SAMPLE_RATE:.1f}s audio)  \"{text[:60]}\"")
        return cached

    if tracker and "tts_start" not in tracker._t:
        tracker.mark("tts_start")
    t = time.perf_counter()
    try:
        if _speak_lang != "en" and _ml_synth is not None:
            a = _ml_synth(text, _speak_lang)
            if a is None:
                return None
            print(f"[TTS] synth[{_speak_lang}] {time.perf_counter()-t:.2f}s  "
                  f"({len(a)/TTS_SAMPLE_RATE:.1f}s audio)  \"{text[:40]}\"")
            return a
        phonemes = _goonj.phonemize(text, "en")
        if not phonemes:
            return None
        phonemes = phonemes[:509]
        ref_s = _priya_embedding(len(phonemes)).to("cpu")
        with _torch.inference_mode():
            audio = _tts_model(phonemes, ref_s, speed=TTS_SPEED)
        a = (audio.detach().cpu().numpy()
             if hasattr(audio, "detach") else np.asarray(audio))
        a = np.asarray(a, dtype=np.float32).squeeze()
        if a.ndim != 1:
            a = a.reshape(-1)
        if len(a) == 0 or not np.isfinite(a).all():
            return None
    except Exception as e:
        print(f"[TTS] error: {e}")
        return None

    print(f"[TTS] synth {time.perf_counter()-t:.2f}s  "
          f"({len(a)/TTS_SAMPLE_RATE:.1f}s audio)  \"{text[:60]}\"")
    return a


# ============================================================
# PLAYBACK  (+ optional experimental AEC barge-in)
# ============================================================

_playback_lock = threading.Lock()


def play(audio, sr=TTS_SAMPLE_RATE):
    """Blocking playback. Returns 'finished' or 'error'."""
    if audio is None or len(audio) == 0:
        return "error"
    try:
        with _playback_lock:
            sd.play(audio, sr, device=OUTPUT_DEVICE)
            sd.wait()
        return "finished"
    except Exception as e:
        print(f"[PLAY] error: {e}")
        try:
            sd.stop()
        except Exception:
            pass
        return "error"


# ---- Barge-in gate constants (shared with the pure decision fn / tests) ----
BARGE_SPEECH_PROB    = 0.85     # spectral speech probability on AEC-cleaned audio
BARGE_MIN_CLEAN_RMS  = 0.005    # absolute floor for cleaned near-end energy
BARGE_MIN_SPEECH_S   = 0.32     # NET accumulated qualifying speech (gaps decay, not reset)
BARGE_RESIDUAL_MULT  = 3.0      # cleaned energy must exceed 3x the measured echo residual
BARGE_MAX_FARNEAR_CORR = 0.45   # raw near/far correlation above this = it's echo
BARGE_MIN_CONSEC     = 3        # need at least one unbroken run this long to start a candidate
BARGE_GAP_GRACE      = 8        # gaps up to 80 ms inside a word don't erode the accumulator
BARGE_DECAY_FACTOR   = 0.5      # once past the grace gap, each frame subtracts 0.5x a frame
BARGE_WARMUP_S       = 0.30     # ignore the first 300 ms (AEC convergence)


def barge_in_decision(speech_prob, clean_rms, sustained_seconds,
                      residual_floor=0.0, farnear_corr=0.0,
                      consec_frames=999):
    """Pure, testable multi-signal near-end-speech gate for AEC barge-in.

    Priya is interrupted only when ALL hold:
      * strong spectral speech probability on the AEC-cleaned signal
      * cleaned near-end energy above BOTH an absolute floor and 3x the
        measured echo residual for this call
      * low correlation between the raw near-end and the far-end (Priya)
        frame  -- high correlation means we're just hearing Priya
      * at least one unbroken run of >= BARGE_MIN_CONSEC qualifying frames
        (rejects isolated clicks / single-frame spikes)
      * NET accumulated qualifying speech >= BARGE_MIN_SPEECH_S seconds
        (a leaky accumulator: gaps between words decay it, they don't reset
        it -- requiring 0.35 s of *unbroken* frames is too strict for real
        speech and was why a normal "Wait" didn't register)

    A single high speech_probability value is deliberately NOT enough --
    that is what produced the earlier false barge-ins.
    """
    dyn = max(BARGE_MIN_CLEAN_RMS, residual_floor * BARGE_RESIDUAL_MULT)
    return bool(
        speech_prob >= BARGE_SPEECH_PROB
        and clean_rms >= dyn
        and farnear_corr <= BARGE_MAX_FARNEAR_CORR
        and consec_frames >= BARGE_MIN_CONSEC
        and sustained_seconds >= BARGE_MIN_SPEECH_S
    )


def _corr(a, b):
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    na = np.linalg.norm(a); nb = np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(abs(np.dot(a, b)) / (na * nb))


def _barge_frame_eval(prob, clean_rms, farnear_corr, dyn_rms):
    """Per-10ms-frame gate. Returns (qualifies, failed_reason_or_None)."""
    if prob < BARGE_SPEECH_PROB:
        return False, "prob"
    if clean_rms < dyn_rms:
        return False, "rms"
    if farnear_corr > BARGE_MAX_FARNEAR_CORR:
        return False, "corr"
    return True, None


def play_with_barge_in(audio, sr=TTS_SAMPLE_RATE):
    """True-duplex playback with WebRTC AEC + a strict multi-signal barge-in
    gate. Returns ('finished'|'error', pending_pre_roll_chunks_or_None).

    Precision over sensitivity: a false interruption is a failure, a slightly
    late one is fine.
    """
    try:
        from pywebrtc_audio import AudioProcessor
    except Exception:
        return play(audio, sr), None

    RATE = 16000
    FRAME = 160                     # 10 ms
    FRAME_S = FRAME / RATE
    PRE_ROLL = 0.4

    far = _resample(audio, sr, RATE)
    total = len(far)

    proc = AudioProcessor(
        sample_rate=RATE, num_channels=1,
        echo_cancellation=True, noise_suppression=True,
        auto_gain_control=False, ns_level=2, stream_delay_ms=40,
    )

    stop_event = threading.Event()
    done_event = threading.Event()
    recent = deque(maxlen=max(1, int(PRE_ROLL / FRAME_S)))
    captured = []
    events = deque()                     # (drained + printed by the main thread)
    st = {
        "pos": 0, "consec": 0, "best_run": 0, "speech": 0.0, "gap": 0,
        "interrupted": False, "cand": False, "fail_reason": None,
        "elapsed": 0.0, "floor_samples": [], "residual_floor": 0.0, "dyn": 0.0,
        "n_eval": 0, "n_all": 0, "max_prob": 0.0, "max_cr": 0.0, "min_corr": 1.0,
        "peak_prob": 0.0, "peak_rms": 0.0,
    }

    def cb(indata, outdata, frames, time_info, status):
        pos = st["pos"]
        end = min(pos + frames, total)
        f = np.zeros(frames, dtype=np.float32)
        if pos < total:
            f[: end - pos] = far[pos:end]
        near = np.asarray(indata[:, 0], dtype=np.float32).copy()
        try:
            clean = proc.process(near, f)
            prob = float(proc.speech_probability)
        except Exception:
            clean, prob = near, 0.0
        cr = rms(clean)
        recent.append(clean.copy())
        st["elapsed"] += FRAME_S

        # phase 1: AEC convergence.  phase 2: measure this call's echo residual.
        if st["elapsed"] < BARGE_WARMUP_S:
            pass
        elif len(st["floor_samples"]) < 40:
            st["floor_samples"].append(cr)
            if len(st["floor_samples"]) == 40:
                st["residual_floor"] = float(np.percentile(st["floor_samples"], 90))
                st["dyn"] = max(BARGE_MIN_CLEAN_RMS,
                                st["residual_floor"] * BARGE_RESIDUAL_MULT)
        else:
            corr = _corr(near, f)
            ok, reason = _barge_frame_eval(prob, cr, corr, st["dyn"])
            st["n_eval"] += 1
            st["max_prob"] = max(st["max_prob"], prob)
            st["max_cr"] = max(st["max_cr"], cr)
            if cr >= st["dyn"]:
                st["min_corr"] = min(st["min_corr"], corr)

            if ok:
                st["n_all"] += 1
                st["consec"] += 1
                st["gap"] = 0
                st["best_run"] = max(st["best_run"], st["consec"])
                st["peak_prob"] = max(st["peak_prob"], prob)
                st["peak_rms"] = max(st["peak_rms"], cr)
                # a candidate needs at least one short unbroken run first
                if st["consec"] >= BARGE_MIN_CONSEC:
                    if not st["cand"]:
                        st["cand"] = True
                        events.append(
                            "[BARGE] candidate — near-end speech-like activity "
                            f"(prob {prob:.2f}, rms {cr:.4f}, corr {corr:.2f}); "
                            "validating...")
                    st["speech"] += FRAME_S            # leaky accumulator
            else:
                if st["consec"] >= 2 and reason:
                    st["fail_reason"] = reason
                st["consec"] = 0
                if st["cand"]:
                    st["gap"] += 1
                    # short gaps between phonemes/words don't erode the count;
                    # only a real pause does
                    if st["gap"] > BARGE_GAP_GRACE:
                        st["speech"] = max(0.0, st["speech"]
                                           - FRAME_S * BARGE_DECAY_FACTOR)

            if (st["cand"] and st["speech"] >= BARGE_MIN_SPEECH_S
                    and st["best_run"] >= BARGE_MIN_CONSEC
                    and not st["interrupted"]):
                st["interrupted"] = True
                captured.extend(list(recent))
                events.append(
                    f"[BARGE] CONFIRMED human speech — prob {st['peak_prob']:.2f}, "
                    f"rms {st['peak_rms']:.4f} (floor {st['residual_floor']:.4f}, "
                    f"dyn {st['dyn']:.4f}), {st['speech']:.2f}s net over "
                    f"{st['n_all']} frames. Stopping Priya.")
                stop_event.set()

        outdata[:, 0] = f
        st["pos"] += frames
        if st["pos"] >= total:
            done_event.set()

    try:
        with sd.Stream(samplerate=RATE, blocksize=FRAME,
                       device=(MIC_DEVICE, OUTPUT_DEVICE), channels=1,
                       dtype="float32", callback=cb) as stream:
            while not done_event.is_set() and not stop_event.is_set():
                while events:
                    print(events.popleft())
                time.sleep(0.01)
            if stop_event.is_set():
                stream.abort()
            else:
                stream.stop()
    except Exception as e:
        print(f"[PLAY/AEC] error: {e}  (falling back to plain playback)")
        return play(audio, sr), None

    while events:
        print(events.popleft())

    if st["interrupted"]:
        return "finished", list(captured)

    # Not confirmed — say why. The reason is derived from the run's AGGREGATES
    # (not the last frame's break reason, which was misleading): pick the FIRST
    # gate that no frame ever cleared; if every gate was cleared at some point
    # but never together for long enough, it was a brief sound, not speech.
    had_activity = (st["n_all"] > 0 or st["max_prob"] >= BARGE_SPEECH_PROB
                    or st["max_cr"] >= st["dyn"])
    if st["n_eval"] and had_activity:
        thr = (f"gate: prob>={BARGE_SPEECH_PROB} rms>={st['dyn']:.4f} "
               f"corr<={BARGE_MAX_FARNEAR_CORR}; need >={BARGE_MIN_SPEECH_S}s net")
        if st["max_prob"] < BARGE_SPEECH_PROB:
            why = (f"speech probability never high enough "
                   f"(peak {st['max_prob']:.2f} < {BARGE_SPEECH_PROB})")
        elif st["max_cr"] < st["dyn"]:
            why = (f"near-end never loud enough vs the echo floor "
                   f"(peak {st['max_cr']:.4f} < {st['dyn']:.4f})")
        elif st["min_corr"] > BARGE_MAX_FARNEAR_CORR:
            why = (f"near-end stayed correlated with Priya's audio = echo "
                   f"(best corr {st['min_corr']:.2f} > {BARGE_MAX_FARNEAR_CORR})")
        else:
            why = (f"a brief sound, not sustained speech "
                   f"({st['n_all']}/{st['n_eval']} frames cleared all gates, "
                   f"longest run {st['best_run']} frames, "
                   f"net {st['speech']:.2f}s < {BARGE_MIN_SPEECH_S}s)")
        print(f"[BARGE] rejected — {why}. [{thr}]")
    else:
        print("[BARGE] no near-end speech — Priya finished uninterrupted. "
              f"[echo floor {st['residual_floor']:.4f}, "
              f"max near-end rms {st['max_cr']:.4f}, max prob {st['max_prob']:.2f}]")
    return "finished", None


# ============================================================
# MICROPHONE CAPTURE  (energy VAD, proven baseline behaviour)
# ============================================================

class MicCapture:
    def __init__(self, device=MIC_DEVICE):
        self.device = device
        self.q = queue.Queue()
        self._stream = None
        self._muted = False
        self.start_threshold = MIN_START_THRESHOLD
        self.end_threshold = MIN_END_THRESHOLD
        self.noise_floor = 0.0015          # measured idle RMS (set by calibrate)

    # -- stream lifecycle -------------------------------------------------
    def _callback(self, indata, frames, time_info, status):
        if self._muted:
            return
        try:
            self.q.put_nowait(indata[:, 0].copy().astype(np.float32))
        except Exception:
            pass

    def open(self):
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            blocksize=CHUNK_SIZE, device=self.device, callback=self._callback,
        )
        self._stream.start()

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def flush(self):
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    # -- pause the mic while Priya is speaking so her audio never enters
    #    the capture queue (this was the STT-contamination bug).
    def pause(self):
        self._muted = True
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
        self.flush()

    def resume(self, settle=0.20):
        self.flush()
        if self._stream is not None:
            try:
                self._stream.start()
            except Exception:
                # stream may need reopening after a stop on some backends
                try:
                    self._stream.close()
                except Exception:
                    pass
                self.open()
        self._muted = False
        if settle > 0:                     # drop the first frames after resume
            time.sleep(settle)
            self.flush()

    # -- calibration ----------------------------------------------------
    def calibrate(self):
        print("[MIC] calibrating (stay quiet ~1.5s) ...")
        self.flush()
        levels = []
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < CALIBRATION_SECONDS:
            try:
                levels.append(rms(self.q.get(timeout=0.25)))
            except queue.Empty:
                pass
        noise = float(np.median(levels)) if levels else 0.0015

        self.noise_floor = noise

        if FORCE_START_THRESHOLD > 0:
            st = FORCE_START_THRESHOLD
            en = FORCE_END_THRESHOLD if FORCE_END_THRESHOLD > 0 else st * 0.6
            print(f"[MIC] noise={noise:.5f}  start={st:.5f}  end={en:.5f}  (forced)")
        else:
            st = min(MAX_START_THRESHOLD, max(MIN_START_THRESHOLD, noise * 2.5))
            en = min(MAX_END_THRESHOLD,
                     max(MIN_END_THRESHOLD, noise * 1.6, st * 0.55))
            print(f"[MIC] noise={noise:.5f}  start={st:.5f}  end={en:.5f}")
            if noise > 0.012:
                print("[MIC] WARNING: idle noise is high. Voxera adapts the "
                      "endpoint to it, but for best results run  python "
                      "mic_check.py  and set VOXERA_START_THRESHOLD / "
                      "VOXERA_END_THRESHOLD in .env")

        self.start_threshold, self.end_threshold = st, en
        self.flush()
        return st, en

    # -- turn capture -------------------------------------------------
    def wait_for_speech(self, prime_chunks=None):
        """Block until sustained speech onset. Returns pre-roll chunk list."""
        pre = deque(maxlen=PRE_ROLL_CHUNKS)
        if prime_chunks:
            pre.extend(prime_chunks)
        speech = 0.0
        while True:
            try:
                chunk = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            pre.append(chunk)
            if rms(chunk) >= self.start_threshold:
                speech += CHUNK_SECONDS
            else:
                speech = max(0.0, speech - CHUNK_SECONDS * 0.5)
            if speech >= MIN_SPEECH_SECONDS:
                return list(pre)

    def record_turn(self, first_chunks):
        chunks = list(first_chunks)
        silence = 0.0        # strict-silence run (fixed / noise-anchored / rel-drop)
        soft = 0.0           # "clearly quieter than speaking" run (noise-robust)
        seen_speech = True
        noise = max(self.noise_floor, 1e-4)
        # running estimate of how loud THIS patient is speaking this turn.
        # Ignore clipping spikes so one loud frame can't inflate the floor.
        speech_level = 0.0
        for c in first_chunks:
            speech_level = max(speech_level, min(rms(c), 0.5))
        dbg = os.getenv("VOXERA_DEBUG_VAD") == "1"
        last_level = 0.0
        n = 0
        t0 = time.perf_counter()
        while True:
            if time.perf_counter() - t0 >= MAX_TURN_SECONDS:
                if dbg:
                    print(f"[VAD] MAX_TURN hit  speech_level={speech_level:.4f} "
                          f"last={last_level:.4f} noise={noise:.4f}")
                break
            try:
                chunk = self.q.get(timeout=0.25)
            except queue.Empty:
                silence += 0.25
                soft += 0.25
                if seen_speech and silence >= END_SILENCE_SECONDS:
                    break
                continue
            chunks.append(chunk)
            dur = len(chunk) / SAMPLE_RATE
            level = rms(chunk)
            last_level = level
            n += 1

            loud_speaker = speech_level > max(self.end_threshold * 3,
                                              noise * ENDPOINT_NOISE_MULT * 1.3)

            # STRICT silence: below the fixed threshold, OR - once the patient
            # is clearly louder than the room - back down near the measured
            # idle noise floor, OR a big relative drop from the speaking level.
            noise_ceiling = noise * ENDPOINT_NOISE_MULT if loud_speaker else 0.0
            rel_floor = (speech_level * ENDPOINT_DROP_RATIO
                         if loud_speaker else 0.0)
            is_silence = (level < self.end_threshold
                          or (noise_ceiling and level < noise_ceiling)
                          or (rel_floor and level < rel_floor))

            # SOFT silence: clearly below speaking volume (helps a room whose
            # background noise sits above every strict threshold - end after a
            # longer hold so a genuine mid-sentence pause is not clipped).
            is_soft = loud_speaker and level < speech_level * 0.55

            if is_silence:
                silence += dur
                soft += dur
                speech_level *= 0.997
            elif is_soft:
                silence = 0.0
                soft += dur
                speech_level = max(speech_level * 0.997, level)
            else:
                silence = 0.0
                soft = 0.0
                seen_speech = True
                speech_level = max(speech_level * 0.997, min(level, 0.5))

            if seen_speech and silence >= END_SILENCE_SECONDS:
                if dbg:
                    print(f"[VAD] endpoint@strict {n*CHUNK_SECONDS:.1f}s "
                          f"speech_level={speech_level:.4f} noise_ceil="
                          f"{noise_ceiling:.4f} rel={rel_floor:.4f}")
                break
            if seen_speech and soft >= END_SILENCE_SECONDS * 2.5:
                if dbg:
                    print(f"[VAD] endpoint@soft {n*CHUNK_SECONDS:.1f}s "
                          f"speech_level={speech_level:.4f} last={level:.4f}")
                break

        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks).astype(np.float32)

        # Trim trailing silence: keep ~0.2s after the last clearly-voiced frame.
        # Shortens Whisper's input and starves the "no no no ..." hallucination.
        keep_floor = max(self.end_threshold,
                         noise * ENDPOINT_NOISE_MULT if speech_level > noise * 3
                         else self.end_threshold)
        step = CHUNK_SIZE
        last_voiced = len(audio)
        for i in range(len(audio) - step, step, -step):
            if rms(audio[i:i + step]) >= keep_floor:
                last_voiced = i + step
                break
        cut = min(len(audio), last_voiced + int(0.20 * SAMPLE_RATE))
        if cut < len(audio) - int(0.05 * SAMPLE_RATE):
            audio = audio[:cut]
        else:
            trim = int(POST_ROLL_SECONDS * SAMPLE_RATE)
            if trim and len(audio) > trim:
                audio = audio[:-trim]
        return audio

    def capture_utterance(self, prime_chunks=None):
        # Discard anything buffered before this turn started listening
        # (stale frames / any residual echo). prime_chunks (a real barge-in
        # pre-roll) is kept.
        if not prime_chunks:
            self.flush()
        first = self.wait_for_speech(prime_chunks=prime_chunks)
        return self.record_turn(first)


# ============================================================
# HIGH-LEVEL: speak()  (synthesize + play, with timing)
# ============================================================

def speak(text, tracker=None, allow_barge_in=False, mic=None):
    """Synthesize `text` with Priya and play it.

    mic: a MicCapture. It is ALWAYS paused for the duration of playback so
    Priya's own audio can never enter the capture queue (that was the STT
    contamination bug). In AEC barge-in mode the duplex stream owns the mic
    device while it runs, then MicCapture is resumed.

    Returns dict: {"ok", "interrupted", "pending_audio"}
    """
    audio = synthesize(text, tracker=tracker)
    if audio is None:
        return {"ok": False, "interrupted": False, "pending_audio": None}

    if tracker:
        tracker.mark("tts_end")
        tracker.mark("tts_first_audio")   # first audio == playback start (file-based)

    use_aec = allow_barge_in and BARGE_MODE == "aec"

    if mic is not None:
        mic.pause()
    try:
        if use_aec:
            status, pending = play_with_barge_in(audio)
            return {"ok": status != "error",
                    "interrupted": pending is not None,
                    "pending_audio": pending}
        status = play(audio)
        return {"ok": status != "error", "interrupted": False,
                "pending_audio": None}
    finally:
        if mic is not None:
            mic.resume()


# ============================================================
# CONVERSATION MEMORY
# ============================================================

_NAME_RX = re.compile(
    r"\b(?:my name is|my name's|i am|i'm|this is|it's|its|call me)\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b")
_TEMP_RX = re.compile(
    r"\b(?:fever|temperature|temp)\b[^.\d]{0,25}?(\d{2,3}(?:\.\d)?)"
    r"|\b(\d{2,3}(?:\.\d)?)\s*(?:degrees|deg|f\b|fahrenheit|°)")
_DURATION_RX = re.compile(
    r"\b(?:since|for|for the (?:last|past)|going on)\s+"
    r"((?:a|an|one|two|three|four|five|\d+)\s*"
    r"(?:hour|day|week|month|year)s?|yesterday|last night|this morning|"
    r"a few days|a couple of days|a couple days)\b")
_SYMPTOM_WORDS = [
    "fever", "cough", "headache", "sore throat", "runny nose", "blocked nose",
    "congestion", "chest pain", "chest tightness", "shortness of breath",
    "nausea", "vomiting", "diarrhea", "dizziness", "fatigue", "tired",
    "weakness", "rash", "chills", "body ache", "back pain", "stomach pain",
    "abdominal pain", "ear pain", "toothache", "bleeding", "swelling",
    "shortness of breath", "palpitations", "numbness", "blurred vision",
]
_MED_RX = re.compile(
    r"\b(?:took|take|taking|taken|had|on|been on|using)\s+(?:some |a |the |my )?"
    r"(paracetamol|acetaminophen|tylenol|ibuprofen|advil|aspirin|"
    r"antibiotics?|amoxicillin|azithromycin|crocin|dolo|combiflam|"
    r"cough syrup|antacids?|insulin|metformin|[a-z]+cillin)\b")
_BARE_TEMP_RX = re.compile(r"\b(?:about|around|like|it'?s|its|at)\s+(\d{2,3}(?:\.\d)?)\b")
_APPT_RX = re.compile(
    r"\b(?:book|schedule|make|set up|need|want|get)\s+(?:an? )?appointment\b"
    r"|\bsee (?:a |the )?doctor\b|\bcome in\b")

# --- safety-profile fields used by the OTC layer (voxera_care) ---
_AGE_RX = re.compile(
    r"\b(?:i'?m|i am|aged|age(?:d)?)\s+(\d{1,3})\b"
    r"|\b(\d{1,3})\s*(?:years?\s*old|y(?:rs?)?\s*old|y[/ ]?o)\b")
_CHILD_AGE_RX = re.compile(
    r"\b(?:my|the)\s+(?:son|daughter|child|kid|boy|girl|baby|toddler|infant)\b"
    r"[^.\d]{0,30}?(\d{1,2})\b"
    r"|\b(\d{1,2})[- ]?(?:year|yr)s?[- ]?old\s+"
    r"(?:son|daughter|child|kid|boy|girl)\b")
_CHILD_RX = re.compile(
    r"\b(?:my|the|our)\s+(?:son|daughter|child|kid|children|baby|toddler|infant"
    r"|little one|newborn)\b|\bfor my kids?\b|\bmy \d{1,2} year old\b")
_PREG_RX = re.compile(
    r"\b(?:i'?m|i am|she'?s|she is)\s+pregnant\b|\b(\d{1,2})\s*weeks?\s*pregnant\b"
    r"|\bin my (?:first|second|third) trimester\b|\bexpecting a baby\b")
_ALLERGY_RX = re.compile(
    r"\ballerg(?:ic|y)\s+to\s+([a-z][a-z0-9 ,\-]{2,60}?)(?=[.;]|\s+but\b|$)")
_ALLERGY_SPLIT = re.compile(r"\s*(?:,|\band\b)\s*")
_CONDITIONS = {
    "asthma": r"\basthma\b",
    "diabetes": r"\bdiabet(?:es|ic)\b",
    "high blood pressure": r"\b(?:high blood pressure|hypertension)\b",
    "kidney disease": r"\bkidney (?:disease|problem|failure|issues?)\b",
    "liver disease": r"\bliver (?:disease|problem|failure|issues?)\b|\bcirrhosis\b|\bhepatitis\b",
    "stomach ulcer": r"\b(?:stomach|peptic|gastric) ulcer\b",
    "heart failure": r"\bheart (?:failure|disease|condition)\b",
    "blood thinners": r"\b(?:blood thinner|blood thinners|warfarin|anticoagulant)\b",
    "epilepsy": r"\bepilep(?:sy|tic)\b|\bseizure disorder\b",
}
_COND_RX = {k: re.compile(v, re.IGNORECASE) for k, v in _CONDITIONS.items()}


def extract_facts(text, facts):
    """Deterministic, cheap structured-state extraction from one patient
    utterance. Mutates and returns `facts`. Never calls an LLM."""
    if not text:
        return facts
    low = text.lower()

    m = _NAME_RX.search(text)
    if m and "facts" not in (m.group(1) or "").lower():
        cand = m.group(1).strip()
        if cand.lower() not in ("not", "sure", "here", "having", "feeling", "so",
                                "just", "really", "still", "also", "the"):
            facts.setdefault("patient_name", cand)

    m = _TEMP_RX.search(low)
    if not m and ("fever" in facts.get("symptoms", []) or "fever" in low
                  or "temperature" in low):
        m = _BARE_TEMP_RX.search(low)
    if m:
        val = next((g for g in m.groups() if g), None)
        try:
            f = float(val)
            if 95 <= f <= 112:
                facts["temperature_f"] = val
        except (ValueError, TypeError):
            pass

    m = _DURATION_RX.search(low)
    if m:
        facts["duration"] = m.group(1)

    syms = set(facts.get("symptoms", []))
    for w in _SYMPTOM_WORDS:
        if w in low:
            # respect simple negation right before the symptom
            idx = low.find(w)
            pre = low[max(0, idx - 24):idx]
            if not re.search(r"\b(?:no|not|without|any|never|don'?t|doesn'?t|"
                             r"didn'?t|haven'?t|hasn'?t|isn'?t)\b", pre):
                syms.add(w)
    if syms:
        facts["symptoms"] = sorted(syms)

    meds = set(facts.get("medications", []))
    for mm in _MED_RX.finditer(low):
        meds.add(mm.group(1))
    if meds:
        facts["medications"] = sorted(meds)

    if _APPT_RX.search(low):
        facts["appointment_requested"] = True

    # --- safety profile (for the OTC layer) ---
    cm = _CHILD_AGE_RX.search(low)
    if cm:
        v = next((g for g in cm.groups() if g), None)
        if v:
            facts["age"] = int(v)
            facts["is_child"] = True
    elif _CHILD_RX.search(low):
        facts["is_child"] = True
    if "age" not in facts:
        am = _AGE_RX.search(low)
        if am:
            v = next((g for g in am.groups() if g), None)
            try:
                iv = int(v)
                if 0 <= iv <= 120:
                    facts["age"] = iv
                    if iv < 16:
                        facts["is_child"] = True
            except (TypeError, ValueError):
                pass

    pm = _PREG_RX.search(low)
    if pm and not re.search(r"\bnot\b[^.]{0,15}pregnant", low):
        facts["pregnant"] = True

    allergies = set(facts.get("allergies", []))
    for am in _ALLERGY_RX.finditer(low):
        for a in _ALLERGY_SPLIT.split(am.group(1)):
            a = a.strip().rstrip(" .,")
            if a and 2 < len(a) < 40 and a not in ("it", "them", "that"):
                allergies.add(a)
    if allergies:
        facts["allergies"] = sorted(allergies)

    conds = set(facts.get("conditions", []))
    for name, rx in _COND_RX.items():
        if rx.search(low) and not re.search(
                r"\b(?:no|not|don'?t have|without)\b[^.]{0,20}" + re.escape(name.split()[0]),
                low):
            conds.add(name)
    if conds:
        facts["conditions"] = sorted(conds)

    return facts


def facts_line(facts):
    """Compact single-line summary for the LLM context / logs."""
    if not facts:
        return ""
    bits = []
    if facts.get("patient_name"):
        bits.append(f"name={facts['patient_name']}")
    if facts.get("symptoms"):
        bits.append("symptoms=" + ",".join(facts["symptoms"]))
    if facts.get("duration"):
        bits.append(f"duration={facts['duration']}")
    if facts.get("temperature_f"):
        bits.append(f"temp={facts['temperature_f']}F")
    if facts.get("medications"):
        bits.append("meds=" + ",".join(facts["medications"]))
    if facts.get("age") is not None:
        bits.append(f"age={facts['age']}")
    if facts.get("is_child"):
        bits.append("child=yes")
    if facts.get("pregnant"):
        bits.append("pregnant=yes")
    if facts.get("allergies"):
        bits.append("allergies=" + ",".join(facts["allergies"]))
    if facts.get("conditions"):
        bits.append("conditions=" + ",".join(facts["conditions"]))
    if facts.get("appointment_requested"):
        bits.append("appointment_requested=yes")
    return "; ".join(bits)


class Memory:
    """Local rolling window used for LLM context (fast, no DB round-trip)
    plus a compact structured 'facts' dict for logging / summaries."""

    # A single turn should never be huge - a Whisper loop can produce a
    # multi-hundred-word transcript that would balloon the LLM prompt and its
    # CPU processing time. Cap what we keep for context.
    MAX_TURN_CHARS = 700

    def __init__(self, max_turns=8):
        self.turns = []               # {"role": "user"|"assistant", "content"}
        self.max_turns = max_turns
        self.facts = {}

    def _cap(self, text):
        text = (text or "").strip()
        if len(text) > self.MAX_TURN_CHARS:
            text = text[: self.MAX_TURN_CHARS].rsplit(" ", 1)[0] + " ..."
        return text

    def add_user(self, text):
        if text:
            text = self._cap(text)
            self.turns.append({"role": "user", "content": text})
            extract_facts(text, self.facts)
            self._trim()

    def last_assistant(self):
        for t in reversed(self.turns):
            if t["role"] == "assistant":
                return t["content"]
        return ""

    def context_excluding_last_user(self):
        turns = self.turns[:-1] if self.turns and self.turns[-1]["role"] == "user" \
            else self.turns
        lines = []
        for t in turns[-8:]:
            who = "PATIENT" if t["role"] == "user" else "VOXERA"
            lines.append(f"{who}: {t['content']}")
        return "\n".join(lines)

    def add_assistant(self, text):
        if text:
            self.turns.append({"role": "assistant", "content": self._cap(text)})
            self._trim()

    def _trim(self):
        if len(self.turns) > self.max_turns * 2:
            self.turns = self.turns[-self.max_turns * 2:]

    def history(self):
        return list(self.turns)

    def transcript(self):
        lines = []
        for t in self.turns:
            who = "PATIENT" if t["role"] == "user" else "VOXERA"
            lines.append(f"{who}: {t['content']}")
        return "\n".join(lines)
