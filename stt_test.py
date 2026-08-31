import whisper
import sounddevice as sd
import numpy as np
import time

# ============================================================
# VOXERA — STT TEST
# ============================================================

SAMPLE_RATE = 16000
MIC_DEVICE = 1
RECORD_SECONDS = 15

print("=" * 60)
print("VOXERA — STT TEST")
print("=" * 60)

print("\n🧠 Loading Whisper SMALL...")
model = whisper.load_model("small")
print("✅ Whisper ready.")

print("\n🎤 Microphone:")
print("   Device:", MIC_DEVICE)
print("   Sample rate:", SAMPLE_RATE)
print(f"   Recording length: {RECORD_SECONDS} seconds")

input("\nPress ENTER when ready...")

print("\n🎙️ RECORDING — SPEAK NOW...")

try:
    audio = sd.rec(
        int(RECORD_SECONDS * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=MIC_DEVICE
    )

    sd.wait()

except Exception as e:
    print("\n❌ MICROPHONE ERROR:")
    print(e)
    raise SystemExit

print("✅ Recording finished.")

# ------------------------------------------------------------
# Convert to mono float32
# ------------------------------------------------------------

audio = audio.flatten().astype(np.float32)

peak = float(np.max(np.abs(audio)))
rms = float(np.sqrt(np.mean(audio ** 2)))

print(f"\n🎧 Audio peak: {peak:.6f}")
print(f"🎧 Audio RMS:  {rms:.6f}")

if peak < 0.005:
    print("⚠️ Audio is extremely quiet.")

elif peak < 0.02:
    print("⚠️ Audio is quiet.")

else:
    print("✅ Audio level looks good.")

# ------------------------------------------------------------
# WHISPER
# ------------------------------------------------------------

print("\n🧠 Transcribing...")

start = time.time()

try:

    result = model.transcribe(
        audio,
        language="en",
        fp16=False,
        temperature=0,
        condition_on_previous_text=False,
        verbose=False
    )

except Exception as e:

    print("\n❌ WHISPER ERROR:")
    print(e)
    raise SystemExit

elapsed = time.time() - start

text = result.get(
    "text",
    ""
).strip()

# ------------------------------------------------------------
# RESULT
# ------------------------------------------------------------

print(f"\n⏱️ STT time: {elapsed:.2f}s")

print("\n" + "=" * 60)
print("TRANSCRIPTION")
print("=" * 60)

print(text if text else "❌ No speech detected.")

print("=" * 60)

print("\n🎤 Device used:", MIC_DEVICE)

print("\n✅ STT TEST COMPLETE.")