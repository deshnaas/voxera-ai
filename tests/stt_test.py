import whisper
import sounddevice as sd
import numpy as np
import time

SAMPLE_RATE = 16000
RECORD_SECONDS = 5

print("Loading Whisper...")
model = whisper.load_model("base")

print("\n🎤 Speak for 5 seconds...")

audio = sd.rec(
    int(RECORD_SECONDS * SAMPLE_RATE),
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype="float32"
)

sd.wait()

print("Processing speech...")

start = time.perf_counter()

result = model.transcribe(
    audio.flatten(),
    language=None,
    fp16=False
)

elapsed = time.perf_counter() - start

print("\n========== VOXERA STT ==========")
print("Detected language:", result.get("language"))
print("You said:", result["text"].strip())
print(f"STT TIME: {elapsed:.2f}s")
print("================================")