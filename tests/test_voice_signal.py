#   python tests/test_voice_signal.py
#   Model-independent by default; the real SpeechBrain worker test runs only if the voice venv is ready
#   (set VOXERA_TEST_VOICE_MODEL=1 to force it; the first run downloads ~1 GB of weights).
import os
import sys
import time

import numpy as np

from _pf_fixtures import run_all
from voxera_patientfetch import voice_signal as vs
from voxera_patientfetch.models import VoiceSignal

SR = 16000


class NoWorker(vs.WorkerClient):
    """A worker that is not installed."""
    def __init__(self):
        super().__init__(python="C:/definitely/not/here/python.exe")


class FakeWorker(vs.WorkerClient):
    def __init__(self, probs):
        super().__init__(python=sys.executable)
        self._p = probs
        self._ready = True
        self._proc = type("P", (), {"poll": lambda s: None})()

    def posteriors(self, audio):
        return self._p


def speechlike(seconds=3.0, f0=140.0, rate=4.0, amp=0.1, vibrato=0.0, noise=0.002, seed=1):
    """Synthetic 'syllabic' voiced signal: harmonic tone, amplitude-modulated at `rate` Hz."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    f = f0 * (1 + vibrato * np.sin(2 * np.pi * 3.0 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    sig = sum(np.sin(k * ph) / k for k in (1, 2, 3, 4))
    env = 0.5 * (1 + np.sin(2 * np.pi * rate * t - np.pi / 2)) ** 1.5
    x = amp * env * sig / 2 + noise * rng.standard_normal(len(t))
    return x.astype(np.float32)


def analyzer(worker=None):
    a = vs.VoiceSignalAnalyzer(client=worker or NoWorker(), use_model=worker is not None)
    return a


# ---------------------------------------------------------------------------

def test_features_on_synthetic_speech_are_sane():
    f = vs.acoustic_features(speechlike(rate=4.0))
    assert 0.02 < f["rms"] < 0.2
    assert 2.5 <= f["speech_rate"] <= 8.0, f["speech_rate"]      # peaks per second of ACTIVE speech
    assert f["pitch_variability"] is not None and f["pitch_variability"] < 0.06        # steady pitch
    assert vs.signal_quality(f) in ("good", "fair")


def test_faster_and_more_variable_speech_scores_higher_arousal_than_calm_speech():
    calm = vs.acoustic_arousal(vs.acoustic_features(speechlike(rate=3.0, vibrato=0.01, amp=0.04)))
    agit = vs.acoustic_arousal(vs.acoustic_features(speechlike(rate=6.5, vibrato=0.25, amp=0.15)))
    assert calm is not None and agit is not None and agit > calm


def test_output_shape_has_the_spec_fields():
    d = analyzer().analyze(speechlike()).to_dict()
    for k in ("arousal_level", "emotion_signal", "confidence", "speech_rate", "rms", "pitch_variability", "signal_quality"):
        assert k in d
    assert "not a diagnostic" in d["disclaimer"].lower()


def test_too_short_audio_is_unavailable_not_an_error():
    s = analyzer().analyze(np.zeros(int(0.4 * SR), dtype=np.float32) + 0.05)
    assert not s.available and s.arousal_level == "unavailable" and s.reason == "audio_too_short"


def test_silence_and_too_quiet_audio_are_unavailable():
    s = analyzer().analyze(np.zeros(3 * SR, dtype=np.float32))
    assert not s.available and s.reason in ("too_quiet", "insufficient_features")
    s = analyzer().analyze(np.random.default_rng(0).standard_normal(3 * SR).astype(np.float32) * 0.0005)
    assert not s.available


def test_bad_or_unsupported_input_never_raises():
    a = analyzer()
    for bad in (None, [], "text", np.array([np.nan] * 32000, dtype=np.float32), np.zeros((0,), np.float32)):
        s = a.analyze(bad)
        assert isinstance(s, VoiceSignal) and not s.available


def test_heavy_noise_and_clipping_degrade_to_unavailable_or_low_confidence():
    noisy = (np.random.default_rng(3).standard_normal(3 * SR) * 0.3).astype(np.float32)
    s = analyzer().analyze(noisy)
    assert (not s.available) or (s.signal_quality == "poor" and s.confidence < 0.45)
    clipped = np.clip(speechlike(amp=2.0), -1, 1)
    s2 = analyzer().analyze(clipped)
    assert (not s2.available) or s2.signal_quality == "poor"


def test_missing_model_falls_back_to_acoustics_only_and_never_claims_high():
    s = analyzer().analyze(speechlike(rate=7.0, vibrato=0.3, amp=0.2))
    if s.available:
        assert s.arousal_level in ("low", "moderate") and s.confidence <= 0.4      # weak evidence, capped


def test_model_posteriors_map_to_arousal_but_confidence_stays_conservative():
    calm = analyzer(FakeWorker({"neu": 0.9, "ang": 0.03, "hap": 0.03, "sad": 0.04})).analyze(speechlike(rate=3.0, amp=0.06))
    hot = analyzer(FakeWorker({"neu": 0.05, "ang": 0.85, "hap": 0.05, "sad": 0.05})).analyze(speechlike(rate=6.5, vibrato=0.25, amp=0.15))
    assert calm.available and hot.available
    assert calm.arousal_level in ("low", "moderate") and hot.arousal_level in ("moderate", "high")
    assert hot.confidence <= 0.9 and calm.confidence <= 0.9


def test_labels_never_contain_a_diagnosis_or_psychological_claim():
    for probs in ({"neu": 0.1, "ang": 0.8, "hap": 0.05, "sad": 0.05}, {"neu": 0.1, "ang": 0.05, "hap": 0.05, "sad": 0.8},
                  {"neu": 0.25, "ang": 0.25, "hap": 0.25, "sad": 0.25}, {"neu": 0.9, "ang": 0.05, "hap": 0.03, "sad": 0.02}):
        for audio in (speechlike(rate=7, vibrato=0.3, amp=0.2), speechlike(rate=3, amp=0.05)):
            s = analyzer(FakeWorker(probs)).analyze(audio)
            assert s.emotion_signal in vs.ALLOWED_EMOTION, s.emotion_signal
            blob = (s.emotion_signal + " " + s.arousal_level + " " + s.disclaimer).lower()
            for w in ("panic", "anxiety", "fear", "angry", "unstable", "depress", "attack"):
                assert w not in blob.replace("not a diagnostic", ""), (w, blob)
    assert vs.display_label(VoiceSignal(available=True, arousal_level="high")) == "High arousal"
    assert vs.display_label(None) == "Uncertain"


def test_voice_signal_object_cannot_express_an_emergency():
    fields = set(VoiceSignal().to_dict())
    assert not ({"emergency", "is_emergency", "escalate", "diagnosis"} & fields)


def test_disabled_by_env_returns_unavailable():
    os.environ["VOXERA_VOICE_SIGNAL"] = "off"
    try:
        s = vs.VoiceSignalAnalyzer(client=NoWorker(), use_model=False).analyze(speechlike())
        assert not s.available and s.reason == "disabled"
    finally:
        os.environ.pop("VOXERA_VOICE_SIGNAL", None)


def test_async_submit_never_blocks_the_caller():
    class Slow(FakeWorker):
        def posteriors(self, audio):
            time.sleep(1.5)
            return self._p
    a = analyzer(Slow({"neu": 0.9, "ang": 0.03, "hap": 0.03, "sad": 0.04}))
    t0 = time.time()
    a.submit(speechlike())
    assert time.time() - t0 < 0.2                     # returned immediately
    assert a.latest() is None
    deadline = time.time() + 6
    while a.latest() is None and time.time() < deadline:
        time.sleep(0.05)
    assert a.latest() is not None and a.latest().available
    a.close()


def test_submit_copies_the_buffer_so_mic_buffer_reuse_is_safe():
    a = analyzer()
    buf = speechlike()
    a.submit(buf)
    buf[:] = 0.0                                       # the mic layer reuses its buffer
    deadline = time.time() + 5
    while a.latest() is None and time.time() < deadline:
        time.sleep(0.05)
    assert a.latest() is not None and a.latest().available
    a.close()


def test_dead_worker_is_survivable():
    class Dead(vs.WorkerClient):
        def __init__(self):
            super().__init__(python=sys.executable)
            self._ready = False
    s = analyzer(Dead()).analyze(speechlike())
    assert isinstance(s, VoiceSignal)                  # acoustics-only or unavailable; never an exception


def test_real_worker_end_to_end_if_available():
    client = vs.WorkerClient(startup_timeout=1500)
    want = os.getenv("VOXERA_TEST_VOICE_MODEL") == "1"
    marker = os.path.join(os.path.dirname(vs.__file__), ".model_cache", "READY")
    if not client.available or not (want or os.path.exists(marker)):
        print("     (skipped: voice venv/model not warmed; set VOXERA_TEST_VOICE_MODEL=1 to run)")
        return
    a = vs.VoiceSignalAnalyzer(client=client)
    a.warm()
    t0 = time.time()
    while not client.ready and not client._failed and time.time() - t0 < 1500:
        time.sleep(1)
    assert client.ready, client._failed
    wav = os.path.join(os.path.dirname(os.path.dirname(vs.__file__)), "last_patient_audio.wav")
    if os.path.exists(wav):
        import soundfile as sf
        x, sr = sf.read(wav, dtype="float32")
        s = a.analyze(x, sr)
        assert s.available and s.emotion_signal in vs.ALLOWED_EMOTION and (s.latency_ms or 0) < 3000, s
    a.close()


if __name__ == "__main__":
    sys.exit(1 if run_all(dict(globals())) else 0)
