# ============================================================
# VOXERA — MICROPHONE DIAGNOSTIC
# ============================================================
#
#   python mic_check.py
#
# Run this once before a demo on a new machine / room.
# It measures your idle noise floor and your speaking level,
# then prints the VOXERA_START_THRESHOLD / VOXERA_END_THRESHOLD
# values to paste into .env if auto-calibration isn't reliable.
# ============================================================

import time
import numpy as np
import sounddevice as sd

import voxera_core as vx

SR = vx.SAMPLE_RATE
CH = vx.CHUNK_SIZE


def _measure(seconds, label):
    print(f"\n{label} ({seconds:.0f}s) ...")
    frames = []
    q = []

    def cb(indata, n, ti, status):
        q.append(indata[:, 0].copy())

    with sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                        blocksize=CH, device=vx.MIC_DEVICE, callback=cb):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            time.sleep(0.05)
    for c in q:
        frames.append(vx.rms(c))
    frames = np.array(frames) if frames else np.array([0.0])
    return frames


def main():
    dev = sd.query_devices(vx.MIC_DEVICE)
    print("=" * 60)
    print("VOXERA MIC CHECK")
    print("=" * 60)
    print(f"device {vx.MIC_DEVICE}: {dev['name']}")

    idle = _measure(3.0, "Stay SILENT")
    noise = float(np.median(idle))
    noise_p90 = float(np.percentile(idle, 90))

    input("\nPress ENTER, then say: "
          "'Hi, I've had a fever since yesterday.'  ... ")
    speech = _measure(4.0, "Speak NOW")
    sp_med = float(np.median(speech))
    sp_p75 = float(np.percentile(speech, 75))
    sp_max = float(np.max(speech))

    print("\n" + "-" * 60)
    print(f"idle   : median {noise:.5f}   p90 {noise_p90:.5f}")
    print(f"speech : median {sp_med:.5f}   p75 {sp_p75:.5f}   max {sp_max:.5f}")
    print("-" * 60)

    if sp_p75 < noise_p90 * 1.5:
        print("\n⚠️  Speech is not clearly above the noise floor.")
        print("   Move the mic closer, raise input gain in Windows Sound "
              "settings, or reduce background noise, then re-run.")
        return

    start = round(max(noise_p90 * 1.4, (noise_p90 + sp_med) / 2), 5)
    end = round(max(noise_p90 * 1.1, start * 0.6), 5)

    print("\n✅ Recommended .env settings:\n")
    print(f"VOXERA_START_THRESHOLD={start}")
    print(f"VOXERA_END_THRESHOLD={end}")
    print("\n(Leave them unset to use automatic calibration instead.)")


if __name__ == "__main__":
    main()
