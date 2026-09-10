# Unit tests for the pure multi-signal barge-in decision gate.
#   python tests/test_barge_in.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voxera_core import (
    barge_in_decision,
    BARGE_SPEECH_PROB, BARGE_MIN_CLEAN_RMS, BARGE_MIN_SPEECH_S,
    BARGE_RESIDUAL_MULT, BARGE_MAX_FARNEAR_CORR, BARGE_MIN_CONSEC,
)

# (name, prob, clean_rms, net_sustained_s, residual_floor, farnear_corr,
#  max_unbroken_run_frames, expected)
CASES = [
    ("silent patient (Priya only)",          0.05, 0.0008, 0.00, 0.0010, 0.05, 0,  False),
    ("speaker echo, high far/near corr",      0.95, 0.0120, 0.60, 0.0015, 0.80, 20, False),
    ("echo residual, energy below 3x floor",  0.95, 0.0035, 0.60, 0.0020, 0.20, 20, False),
    ("room noise / fan (low prob)",           0.30, 0.0020, 1.00, 0.0010, 0.10, 30, False),
    ("one high-prob frame, no run",           0.98, 0.0200, 0.05, 0.0010, 0.10, 1,  False),
    ("brief cough (short run, short net)",    0.90, 0.0150, 0.10, 0.0010, 0.15, 2,  False),
    ("prob below threshold",                  0.78, 0.0150, 0.50, 0.0010, 0.10, 20, False),
    ("energy at absolute floor only",         0.95, 0.0048, 0.50, 0.0010, 0.10, 20, False),
    ("genuine 'Wait.' (gappy real speech)",   0.90, 0.0110, 0.34, 0.0012, 0.12, 16, True),
    ("genuine quiet sustained speech",        0.88, 0.0065, 0.40, 0.0018, 0.10, 12, True),
    ("clear interrupting sentence",           0.98, 0.0300, 0.80, 0.0015, 0.08, 40, True),
]


def run():
    print(f"gate: prob>={BARGE_SPEECH_PROB} rms>=max({BARGE_MIN_CLEAN_RMS}, "
          f"{BARGE_RESIDUAL_MULT}x floor) corr<={BARGE_MAX_FARNEAR_CORR} "
          f"run>={BARGE_MIN_CONSEC} frames  net_speech>={BARGE_MIN_SPEECH_S}s\n")
    failed = 0
    for name, p, r, s, fl, c, cons, expected in CASES:
        got = barge_in_decision(p, r, s, residual_floor=fl,
                                farnear_corr=c, consec_frames=cons)
        mark = "ok  " if got == expected else "FAIL"
        if got != expected:
            failed += 1
        print(f"  {mark} expect={expected!s:5} got={got!s:5}  {name}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
