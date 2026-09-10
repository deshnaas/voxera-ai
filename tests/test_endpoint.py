# Offline measurement of end-of-turn (endpoint) latency.
#
# Drives the REAL MicCapture.record_turn() against a synthetic frame stream:
# real speech (Priya) followed by trailing noise at a chosen RMS, with a
# realistic calibrated (end_threshold, noise_floor) pair. Reports how long
# after the patient stops before the turn is finalized - for a quiet room, a
# moderately noisy room, and a room whose noise floor sits ABOVE the fixed end
# threshold (the real-mic case that used to run to MAX_TURN).
#
#   python tests/test_endpoint.py

import os
import sys
import queue

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import voxera_core as vx

SR = vx.SAMPLE_RATE
CH = vx.CHUNK_SIZE
FRAME_S = CH / SR


def _frames(sig):
    return [sig[i:i + CH].astype(np.float32).copy()
            for i in range(0, len(sig) - CH + 1, CH)]


def _speech():
    vx.load_tts()
    a = vx.synthesize("I've had a fever since yesterday and I feel pretty tired.")
    return vx._resample(a, vx.TTS_SAMPLE_RATE, SR)


def _calib_end_threshold(noise):
    return min(vx.MAX_END_THRESHOLD,
              max(vx.MIN_END_THRESHOLD, noise * 1.6, noise * 2.5 * 0.55))


class FeedMic(vx.MicCapture):
    def __init__(self, frames, end_threshold, noise_floor):
        self.device = None
        self.q = queue.Queue()
        self._stream = None
        self._muted = False
        self.start_threshold = end_threshold * 1.6
        self.end_threshold = end_threshold
        self.noise_floor = noise_floor
        self._total = len(frames)
        for f in frames:
            self.q.put(f)


def measure(name, speech_sig, noise_floor, trail_rms,
            gap_s=0.0, end_silence=None, legacy=False):
    rng = np.random.default_rng(0)
    end_threshold = 0.006 if legacy else _calib_end_threshold(noise_floor)

    speech_frames = _frames(speech_sig)
    n_speech = len(speech_frames)
    if gap_s > 0:
        gap = rng.normal(0, trail_rms, int(SR * gap_s)).astype(np.float32)
        speech_frames = (speech_frames[: n_speech // 2] + _frames(gap)
                         + speech_frames[n_speech // 2:])
        n_speech = len(speech_frames)

    trail = rng.normal(0, trail_rms, int(SR * 8.0)).astype(np.float32)
    frames = speech_frames + _frames(trail)

    last_voiced = max((i for i, f in enumerate(speech_frames)
                       if vx.rms(f) >= max(end_threshold, noise_floor * 2)),
                      default=n_speech - 1)
    n_speech = last_voiced + 1

    saved = (vx.END_SILENCE_SECONDS, vx.ENDPOINT_DROP_RATIO,
             vx.ENDPOINT_NOISE_MULT)
    if end_silence is not None:
        vx.END_SILENCE_SECONDS = end_silence
    if legacy:                       # emulate the pre-fix detector
        vx.ENDPOINT_DROP_RATIO = 0.0
        vx.ENDPOINT_NOISE_MULT = 1e9
    try:
        mic = FeedMic(frames, end_threshold, noise_floor)
        mic.record_turn([])
        consumed = mic._total - mic.q.qsize()
    finally:
        (vx.END_SILENCE_SECONDS, vx.ENDPOINT_DROP_RATIO,
         vx.ENDPOINT_NOISE_MULT) = saved

    endpoint = (consumed - n_speech) * FRAME_S
    kept_all = consumed >= n_speech * 0.92
    print(f"  {name:40} noise={noise_floor:.4f} thr={end_threshold:.4f} "
          f"trail={trail_rms:.4f} -> endpoint {endpoint:.2f}s"
          + ("" if kept_all else "   <-- CLIPPED"))
    return endpoint, kept_all


def main():
    sp = _speech()
    print(f"speech ~{len(sp)/SR:.1f}s   END_SILENCE={vx.END_SILENCE_SECONDS}s  "
          f"drop_ratio={vx.ENDPOINT_DROP_RATIO}  noise_mult={vx.ENDPOINT_NOISE_MULT}\n")

    print("BEFORE (legacy detector: fixed 1.30s window, no adaptive drop):")
    b_q, _ = measure("quiet room", sp, 0.0015, 0.0015, end_silence=1.30, legacy=True)
    b_n, _ = measure("noisy room (noise 0.019 > thr)", sp, 0.019, 0.020,
                     end_silence=1.30, legacy=True)
    print(f"    -> legacy noisy room never endpoints in a real run "
          f"(here the synthetic queue just runs out at {b_n:.1f}s)\n")

    print("AFTER (adaptive: fixed OR noise-anchored OR relative-drop OR soft):")
    a_q, k1 = measure("quiet room",              sp, 0.0015, 0.0015)
    a_m, k2 = measure("moderately noisy room",   sp, 0.0060, 0.0075)
    a_n, k3 = measure("very noisy room (real)",  sp, 0.0190, 0.0210)
    a_x, k4 = measure("very noisy, loud bg 0.05", sp, 0.0190, 0.0500)
    a_g, k5 = measure("0.5s mid-sentence pause", sp, 0.0060, 0.0075, gap_s=0.5)

    ok = True
    for label, v, lo, hi in [("quiet", a_q, 0.55, 1.20),
                             ("moderate", a_m, 0.55, 1.20),
                             ("very noisy", a_n, 0.55, 1.30),
                             ("noisy+loud-bg", a_x, 0.55, 3.0),
                             ("after 0.5s pause", a_g, 0.55, 1.40)]:
        good = lo <= v <= hi
        ok &= good
        print(f"  [{'ok  ' if good else 'FAIL'}] {label:16} endpoint {v:.2f}s "
              f"in [{lo}, {hi}]s")
    for label, kept in [("quiet", k1), ("moderate", k2), ("very noisy", k3),
                        ("noisy+loud-bg", k4), ("mid-pause", k5)]:
        ok &= kept
        print(f"  [{'ok  ' if kept else 'FAIL'}] {label} kept all speech")

    print(f"\n  BEFORE quiet {b_q:.2f}s -> AFTER quiet {a_q:.2f}s   |   "
          f"noisy room: legacy = MAX_TURN, AFTER = {a_n:.2f}s")
    print("\nALL PASS" if ok else "\nFAILURES")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
