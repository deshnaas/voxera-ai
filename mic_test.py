import sounddevice as sd
import numpy as np
import wave
import time

SAMPLE_RATE = 16000
CHANNELS = 1
SECONDS = 5

print("=" * 60)
print("VOXERA MICROPHONE TEST")
print("=" * 60)

device = sd.default.device[0]

print(f"\n🎙️ Microphone: {sd.query_devices(device)['name']}")
print("\nSpeak normally for 5 seconds.")
print("Say: 'Hello Voxera, can you hear me?'")
print("\n🎤 RECORDING...\n")

audio = sd.rec(
    int(SECONDS * SAMPLE_RATE),
    samplerate=SAMPLE_RATE,
    channels=CHANNELS,
    dtype="float32",
    device=device
)

for i in range(SECONDS):
    time.sleep(1)
    current = audio[: int((i + 1) * SAMPLE_RATE)]

    rms = float(np.sqrt(np.mean(current ** 2)))
    peak = float(np.max(np.abs(current)))

    print(f"   second {i + 1}: RMS={rms:.6f} | PEAK={peak:.6f}")

sd.wait()

filename = "mic_test.wav"

audio_int16 = np.int16(np.clip(audio[:, 0], -1, 1) * 32767)

with wave.open(filename, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(audio_int16.tobytes())

print("\n✅ Recording finished.")
print(f"💾 Saved as: {filename}")

overall_rms = float(np.sqrt(np.mean(audio ** 2)))
overall_peak = float(np.max(np.abs(audio)))

print("\nRESULT")
print("-" * 60)
print(f"RMS  : {overall_rms:.6f}")
print(f"PEAK : {overall_peak:.6f}")

if overall_peak < 0.001:
    print("\n❌ NO USABLE MICROPHONE AUDIO DETECTED.")
else:
    print("\n✅ MICROPHONE IS CAPTURING AUDIO.")