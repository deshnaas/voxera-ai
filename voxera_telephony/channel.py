"""One phone call's audio channel: the object Voxera's call loop uses instead of the laptop mic and speakers.

  caller audio in  ->  feed()                    (called by the WebSocket receiver, 8 kHz PCM from Exotel)
  Voxera hears     ->  capture_utterance()       (endpointing: waits for speech, returns 16 kHz float audio)
  Voxera speaks    ->  speak() / play()          (24 kHz TTS -> line rate -> paced 100 ms chunks)

Things a real phone call needs that a laptop demo does not:
  * no echo cancellation - the network already removed the echo, so barge-in is simple: the caller talks over
    Voxera -> stop talking at once (Exotel `clear`) and keep what they said;
  * never dead air - if the reply is not ready after ~1.6 s, say "one moment" (in the caller's language);
  * silence - "hello, are you still there?", then a polite goodbye and hang-up;
  * caller hangs up at any moment (`stop` event) - unblocks everything and ends the call cleanly.

The channel knows nothing about Exotel's JSON; the server gives it `send(dict)` and calls `feed()` / `close()`.
"""

from __future__ import annotations

import base64
import collections
import os
import queue
import re
import threading
import time
from typing import Callable, Optional

import numpy as np

from .audio import FRAME_S, TTS_SR, VOX_SR, exotel_chunks, float_to_pcm16, pcm16_to_float, level_for_stt, phone_level, resample, split_sentences
from .errors import CallEnded

_END = object()
FRAME = int(VOX_SR * FRAME_S)                       # 320 samples @ 16 kHz


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a * a) + 1e-12)) if len(a) else 0.0


class PhoneChannel:
    def __init__(self, send: Callable[[dict], None], stream_sid: str = "", sample_rate: int = 8000,
                 synth: Optional[Callable] = None, filler: Optional[Callable[[], Optional[np.ndarray]]] = None,
                 is_cached: Optional[Callable[[str], bool]] = None,
                 idle_prompt: Optional[Callable[[], None]] = None, idle_goodbye: Optional[Callable[[], None]] = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.send = send
        self.stream_sid = stream_sid
        self.sr = int(sample_rate)
        self.synth = synth                            # text -> 24 kHz float (Voxera's synthesize)
        self.filler = filler                          # () -> 24 kHz float "one moment" in the caller's language
        self.is_cached = is_cached                    # text -> already pre-rendered? (then no need to stream)
        self.idle_prompt = idle_prompt                # speaks "are you still there?"
        self.idle_goodbye = idle_goodbye              # speaks the goodbye before hanging up on a silent line
        self.clock, self.sleep = clock, sleep

        # tuning (env-overridable; defaults chosen for PSTN speech)
        self.end_silence_s = _f("VOXERA_PHONE_END_SILENCE", 0.85)     # Hindi/Marathi callers pause; not too eager
        self.max_utterance_s = _f("VOXERA_PHONE_MAX_UTTERANCE", 20.0)
        self.min_speech_s = _f("VOXERA_PHONE_MIN_SPEECH", 0.25)
        self.idle_s = _f("VOXERA_PHONE_IDLE", 9.0)
        self.filler_after_s = _f("VOXERA_PHONE_FILLER_AFTER", 2.8)   # only when the reply is genuinely slow
        self.barge_s = _f("VOXERA_PHONE_BARGE_S", 0.36)               # sustained speech needed to interrupt
        self.lead_s = _f("VOXERA_PHONE_LEAD", 0.4)                    # how far ahead of real time we send audio
        self.start_thr = _f("VOXERA_PHONE_START_THRESHOLD", 0.012)
        self.noise = 0.003

        self._q: "queue.Queue[Optional[np.ndarray]]" = queue.Queue()
        self._buf16 = np.zeros(0, dtype=np.float32)
        self._carry = b""
        self._closed = threading.Event()
        self.speaking = threading.Event()
        self._interrupt = threading.Event()
        self._preroll: collections.deque = collections.deque(maxlen=12)
        self._barge_buf: list = []
        self._barge_run = 0
        self._filler_timer: Optional[threading.Timer] = None
        self._filler_playing = False
        self._filler_cooldown = 0
        self._seq = 0
        self._send_lock = threading.Lock()
        self.stats = {"utterances": 0, "barge_ins": 0, "fillers": 0, "idle_prompts": 0, "audio_out_s": 0.0}

    # ------------------------------------------------------------------ inbound (WebSocket thread) -------------
    def feed(self, payload: bytes) -> None:
        """Raw 16-bit PCM from Exotel at the line rate."""
        if self._closed.is_set():
            return
        data = self._carry + payload
        keep = len(data) % 2
        self._carry = data[len(data) - keep:] if keep else b""
        if keep:
            data = data[:-1]
        x = resample(pcm16_to_float(data), self.sr, VOX_SR)
        self._buf16 = np.concatenate([self._buf16, x]) if len(self._buf16) else x
        while len(self._buf16) >= FRAME:
            frame, self._buf16 = self._buf16[:FRAME], self._buf16[FRAME:]
            self._on_frame(frame)

    def _on_frame(self, frame: np.ndarray) -> None:
        if not self.speaking.is_set():
            self._q.put(frame)
            return
        # Voxera is talking: watch for the caller talking over it (barge-in). Phone audio carries no echo of
        # Voxera, so a plain energy test on sustained speech is enough.
        thr = max(self.start_thr * 1.6, self.noise * 5.0)
        self._preroll.append(frame)
        if _rms(frame) > thr:
            if not self._barge_buf:
                self._barge_buf = list(self._preroll)
            else:
                self._barge_buf.append(frame)
            self._barge_run += 1
            if self._barge_run >= int(self.barge_s / FRAME_S):
                self._interrupt.set()
        else:
            if self._barge_buf:
                self._barge_buf.append(frame)
            self._barge_run = max(0, self._barge_run - 1)
            if self._barge_run == 0 and not self._interrupt.is_set():
                self._barge_buf = []                                 # a cough or a click, not an interruption

    def close(self) -> None:
        """The caller hung up (Exotel `stop`) or the server is shutting the call down."""
        self._closed.set()
        self._cancel_filler()
        self._q.put(None)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # ------------------------------------------------------------------ hearing ------------------------------
    def pause(self) -> None: ...                 # MicCapture API compatibility (nothing to pause on a phone line)
    def resume(self) -> None: ...

    def _drain(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def capture_utterance(self, prime_chunks=None) -> np.ndarray:
        """Block until the caller has said something and paused; return it as 16 kHz float audio."""
        self._cancel_filler()
        pre: collections.deque = collections.deque(maxlen=15)
        frames: list = []
        in_speech, run, silence, loud = False, 0, 0, 0
        if prime_chunks:                                            # they interrupted Voxera: keep their first words
            for c in prime_chunks:
                c = np.asarray(c, dtype=np.float32)
                for i in range(0, len(c) - FRAME + 1, FRAME):
                    frames.append(c[i:i + FRAME])
            in_speech = bool(frames)
            loud = len(frames)                                       # what they already said counts as speech
        else:
            self._drain()
        deadline = self.clock() + self.idle_s
        prompted = False
        end_frames = max(1, int(self.end_silence_s / FRAME_S))
        while True:
            try:
                f = self._q.get(timeout=0.1)
            except queue.Empty:
                if self._closed.is_set():
                    raise CallEnded("caller hung up")
                if not in_speech and self.clock() > deadline:
                    if not prompted:                                # "hello? are you still there?"
                        prompted = True
                        self.stats["idle_prompts"] += 1
                        if self.idle_prompt:
                            self.idle_prompt()
                        self._drain()
                        deadline = self.clock() + self.idle_s
                    else:                                           # still nothing: say goodbye and hang up
                        if self.idle_goodbye:
                            self.idle_goodbye()
                        raise CallEnded("line silent")
                continue
            if f is None:
                raise CallEnded("caller hung up")
            r = _rms(f)
            if not in_speech:
                pre.append(f)
                if r < self.noise * 2.5 + 1e-4:                      # learn the line's noise floor from quiet frames
                    self.noise = max(0.0008, 0.97 * self.noise + 0.03 * r)
                thr = max(self.start_thr, self.noise * 3.5)
                run = run + 1 if r > thr else 0
                if run >= 3:                                        # 60 ms of speech
                    in_speech, silence, loud = True, 0, run
                    frames = list(pre)
                continue
            frames.append(f)
            end_thr = max(self.start_thr * 0.55, self.noise * 2.0)
            silence = silence + 1 if r < end_thr else 0
            loud += 1 if r >= end_thr else 0
            if silence >= end_frames or len(frames) * FRAME_S > self.max_utterance_s:
                if loud * FRAME_S < self.min_speech_s:              # a blip, not a sentence: keep listening
                    frames, in_speech, run, silence, loud = [], False, 0, 0, 0
                    pre.clear()
                    continue
                keep = frames[:len(frames) - max(0, silence - 8)]    # trim the tail, keep ~160 ms
                self.stats["utterances"] += 1
                self._arm_filler()
                return level_for_stt(np.concatenate(keep).astype(np.float32))

    # ------------------------------------------------------------------ speaking -----------------------------
    def speak(self, text, tracker=None, allow_barge_in=False, mic=None) -> dict:
        """Same contract as voxera_core.speak(): {"ok", "interrupted", "pending_audio"}."""
        if self.synth is None:
            return {"ok": False, "interrupted": False, "pending_audio": None}
        pieces = self._sentences(text)
        if len(pieces) > 1 and not (self.is_cached and self.is_cached(text)):
            # a long line that is not pre-rendered: speak it sentence by sentence, so the caller hears the first
            # sentence while the rest is still being synthesised (first sound in ~1 sentence, not ~whole reply)
            return self.play_iter(self._synth_stream(pieces, tracker), allow_barge_in=allow_barge_in, tracker=tracker)
        audio = self.synth(text, tracker=tracker)
        if audio is None:
            return {"ok": False, "interrupted": False, "pending_audio": None}
        if tracker:
            tracker.mark("tts_end")
            tracker.mark("tts_first_audio")
        return self.play(audio, allow_barge_in=allow_barge_in)

    def _sentences(self, text: str) -> list:
        parts = split_sentences(text)
        merged: list = []
        for p in parts:                                              # keep tiny bits ("Okay.") attached unless pre-rendered
            short = merged and len(merged[-1].split()) < 4 and not (self.is_cached and self.is_cached(merged[-1]))
            if short:
                merged[-1] += " " + p
            else:
                merged.append(p)
        return merged

    def _synth_stream(self, pieces: list, tracker):
        """Yield 24 kHz audio per sentence; a background thread synthesises ahead while the previous one plays."""
        q: "queue.Queue" = queue.Queue(maxsize=2)

        def produce():
            try:
                for i, s in enumerate(pieces):
                    if self._closed.is_set():
                        break
                    q.put(self.synth(s, tracker=tracker if i == 0 else None))
            finally:
                q.put(_END)
        threading.Thread(target=produce, daemon=True, name="tts-stream").start()
        while True:
            a = q.get()
            if a is _END:
                return
            if a is not None:
                yield a

    def _emit(self, obj: dict) -> None:
        with self._send_lock:
            self.send(obj)

    def _media(self, chunk: bytes) -> dict:
        return {"event": "media", "stream_sid": self.stream_sid, "media": {"payload": base64.b64encode(chunk).decode("ascii")}}

    def play(self, audio24: np.ndarray, allow_barge_in: bool = False) -> dict:
        return self.play_iter(iter([audio24]), allow_barge_in=allow_barge_in)

    def play_iter(self, pieces, allow_barge_in: bool = False, tracker=None) -> dict:
        """Send audio (an iterable of 24 kHz float pieces) to the caller in real time, honouring barge-in."""
        if self._closed.is_set():
            raise CallEnded("caller hung up")
        self._cancel_filler()
        self._interrupt.clear()
        self._barge_buf, self._barge_run = [], 0
        self._preroll.clear()
        self.speaking.set()
        interrupted = False
        t0 = None
        sent = 0.0
        try:
            for audio24 in pieces:
                if t0 is None:
                    t0 = self.clock()
                    if tracker:
                        tracker.mark("tts_end")
                        tracker.mark("tts_first_audio")
                if interrupted:
                    continue
                for chunk in exotel_chunks(float_to_pcm16(phone_level(resample(audio24, TTS_SR, self.sr))), self.sr):
                    if self._closed.is_set():
                        raise CallEnded("caller hung up")
                    if allow_barge_in and self._interrupt.is_set():
                        interrupted = True
                        break
                    self._emit(self._media(chunk))
                    sent += len(chunk) / 2 / self.sr
                    while sent - (self.clock() - t0) > self.lead_s:             # stay a little ahead of real time
                        if self._closed.is_set() or (allow_barge_in and self._interrupt.is_set()):
                            break
                        self.sleep(0.02)
                if interrupted:
                    break
            if t0 is None:
                return {"ok": False, "interrupted": False, "pending_audio": None}
            if not interrupted:                                                 # let the rest play out (still interruptible)
                while self.clock() - t0 < sent:
                    if self._closed.is_set():
                        raise CallEnded("caller hung up")
                    if allow_barge_in and self._interrupt.is_set():
                        interrupted = True
                        break
                    self.sleep(0.02)
            if interrupted:
                self._emit({"event": "clear", "stream_sid": self.stream_sid})     # cut Voxera off mid-word
                self.stats["barge_ins"] += 1
            else:
                self._seq += 1
                self._emit({"event": "mark", "stream_sid": self.stream_sid, "mark": {"name": f"utt-{self._seq}"}})
            self.stats["audio_out_s"] += sent
        finally:
            pending = None
            if interrupted and self._barge_buf:
                pending = [np.concatenate(self._barge_buf).astype(np.float32)]
            self.speaking.clear()
            self._barge_buf, self._barge_run = [], 0
        return {"ok": True, "interrupted": interrupted, "pending_audio": pending}

    # ------------------------------------------------------------------ "one moment" ------------------------
    def _arm_filler(self) -> None:
        if self.filler is None or self.filler_after_s <= 0:
            return
        if self._filler_cooldown > 0:                      # said "one moment" last turn: don't do it again
            self._filler_cooldown -= 1
            return
        self._cancel_filler()
        t = threading.Timer(self.filler_after_s, self._play_filler)
        t.daemon = True
        self._filler_timer = t
        t.start()

    def _cancel_filler(self) -> None:
        t, self._filler_timer = self._filler_timer, None
        if t is not None:
            t.cancel()

    def _play_filler(self) -> None:
        if self._closed.is_set() or self.speaking.is_set():
            return
        try:
            a = self.filler() if self.filler else None
            if a is None or self.speaking.is_set():
                return
            for chunk in exotel_chunks(float_to_pcm16(resample(a, TTS_SR, self.sr)), self.sr):
                self._emit(self._media(chunk))
            self._emit({"event": "mark", "stream_sid": self.stream_sid, "mark": {"name": "filler"}})
            self.stats["fillers"] += 1
            self._filler_cooldown = 2
        except Exception:                                                        # noqa: BLE001  (never break the call)
            pass
