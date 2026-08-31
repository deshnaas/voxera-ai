import whisper
import sounddevice as sd
import ollama
import time

SAMPLE_RATE = 16000
RECORD_SECONDS = 5

print("Loading Whisper...")
whisper_model = whisper.load_model("base")

print("Loading Voxera brain...")
ollama.chat(
    model="qwen3:1.7b",
    messages=[
        {
            "role": "user",
            "content": "/no_think Say READY."
        }
    ],
    think=False,
)

print("\n🎤 Speak for 5 seconds...")

audio = sd.rec(
    int(RECORD_SECONDS * SAMPLE_RATE),
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype="float32"
)

sd.wait()

print("Transcribing...")

start = time.perf_counter()

result = whisper_model.transcribe(
    audio.flatten(),
    language=None,
    fp16=False
)

stt_time = time.perf_counter() - start

user_text = result["text"].strip()

print(f"\nPATIENT: {user_text}")
print(f"STT TIME: {stt_time:.2f}s")

print("\nVoxera is thinking...")

llm_start = time.perf_counter()

response = ollama.chat(
    model="qwen3:1.7b",
    messages=[
        {
            "role": "system",
            "content": (
                "You are Voxera, a healthcare phone assistant. "
                "Speak naturally and briefly. "
                "Do not diagnose or prescribe. "
                "Ask useful follow-up questions when needed. "
                "Keep responses suitable for a phone conversation."
            ),
        },
        {
            "role": "user",
            "content": user_text,
        },
    ],
    think=False,
)

llm_time = time.perf_counter() - llm_start

assistant_text = response["message"]["content"].strip()

print(f"\nVOXERA: {assistant_text}")
print(f"LLM TIME: {llm_time:.2f}s")
print(f"TOTAL AI TIME: {stt_time + llm_time:.2f}s")
