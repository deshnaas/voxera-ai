import numpy as np
import sounddevice as sd
import whisper
import ollama
import soundfile as sf
import torch

from kokoro import KPipeline


# ============================================================
# VOXERA — REAL CONVERSATIONAL LOOP
# ============================================================

SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_SECONDS)

# More forgiving than the old 0.01 threshold
ENERGY_THRESHOLD = 0.0003

# How long silence must last before we consider the user done
SILENCE_DURATION = 1.5

MIN_SPEECH_DURATION = 0.4
MAX_UTTERANCE_SECONDS = 30


# ============================================================
# LOAD MODELS
# ============================================================

print("=" * 60)
print("VOXERA — REAL-TIME CONVERSATIONAL TEST")
print("=" * 60)

print("\nLoading Whisper...")
whisper_model = whisper.load_model("base")
print("Whisper ready.")

print("\nLoading Voxera brain...")
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
print("Brain ready.")

print("\nLoading Voxera voice...")
tts_pipeline = KPipeline(
    lang_code="a"
)
print("Voice ready.")


# ============================================================
# AUDIO QUEUE
# ============================================================

audio_queue = queue.Queue()

manual_finish = threading.Event()


# ============================================================
# AUDIO CALLBACK
# ============================================================

def audio_callback(indata, frames, time_info, status):

    if status:
        print(f"[Audio] {status}")

    audio_queue.put(
        indata[:, 0].copy()
    )


# ============================================================
# ENERGY
# ============================================================

def audio_energy(audio):

    if len(audio) == 0:
        return 0.0

    return float(
        np.sqrt(
            np.mean(
                np.square(audio)
            )
        )
    )


def is_speech(audio):

    energy = audio_energy(audio)

    return energy > ENERGY_THRESHOLD


# ============================================================
# CLEAR OLD AUDIO
# ============================================================

def clear_audio_queue():

    while True:

        try:
            audio_queue.get_nowait()

        except queue.Empty:
            break


# ============================================================
# MANUAL ENTER
# ============================================================

def keyboard_listener():

    while True:

        try:

            input()

            manual_finish.set()

        except EOFError:

            break


# ============================================================
# WAIT FOR USER TO START SPEAKING
# ============================================================

def wait_for_speech():

    print("\n🎤 Listening for you...")

    while True:

        chunk = audio_queue.get()

        energy = audio_energy(chunk)

        if energy > ENERGY_THRESHOLD:

            print("🗣️ Patient speaking...")

            return chunk


# ============================================================
# RECORD COMPLETE USER TURN
# ============================================================

def record_turn(first_chunk):

    chunks = [first_chunk]

    total_time = len(first_chunk) / SAMPLE_RATE

    silence_time = 0.0

    manual_finish.clear()

    while True:

        # ----------------------------------------------------
        # Manual finish
        # ----------------------------------------------------

        if manual_finish.is_set():

            print("⏹️ Turn manually finished.")

            break

        chunk = audio_queue.get()

        chunks.append(chunk)

        duration = len(chunk) / SAMPLE_RATE

        total_time += duration

        # ----------------------------------------------------
        # Detect speech
        # ----------------------------------------------------

        if is_speech(chunk):

            silence_time = 0.0

        else:

            silence_time += duration

        # ----------------------------------------------------
        # End turn after silence
        # ----------------------------------------------------

        if (
            silence_time >= SILENCE_DURATION
            and total_time >= MIN_SPEECH_DURATION
        ):

            print("⏸️ Natural pause detected.")

            break

        # ----------------------------------------------------
        # Maximum protection
        # ----------------------------------------------------

        if total_time >= MAX_UTTERANCE_SECONDS:

            print("⏱️ Maximum utterance reached.")

            break

    return np.concatenate(chunks)


# ============================================================
# SPEECH → TEXT
# ============================================================

def transcribe(audio):

    print("🧠 Transcribing...")

    start = time.perf_counter()

    result = whisper_model.transcribe(
        audio,
        language=None,
        fp16=False
    )

    elapsed = time.perf_counter() - start

    text = result["text"].strip()

    print(f"STT: {elapsed:.2f}s")

    return text


# ============================================================
# VOXERA BRAIN
# ============================================================

def think(user_text):

    print("🧠 Voxera is thinking...")

    start = time.perf_counter()

    response = ollama.chat(
        model="qwen3:1.7b",

        messages=[
            {
                "role": "system",
                "content": """
You are Voxera, a warm and calm healthcare conversational assistant.

You are designed for real-time phone conversations.

Rules:

- Speak naturally.
- Keep responses short.
- Do not diagnose.
- Do not prescribe medication.
- Ask ONE useful follow-up question at a time.
- Do not repeat information unnecessarily.
- If the user describes a possible emergency, advise immediate emergency medical help.
- Sound like a caring human conversational assistant, not a medical textbook.
"""
            },

            {
                "role": "user",
                "content": user_text
            }
        ],

        think=False
    )

    elapsed = time.perf_counter() - start

    answer = response["message"]["content"].strip()

    print(f"AI: {elapsed:.2f}s")

    return answer


# ============================================================
# VOXERA TTS
# ============================================================

def speak(text):

    print("🔊 Generating Voxera voice...")

    start = time.perf_counter()

    audio_parts = []

    for result in tts_pipeline(
        text,
        voice="af_heart",
        speed=1.15
    ):

        if result.audio is not None:

            audio_parts.append(
                result.audio.numpy()
            )

    if not audio_parts:

        print("TTS failed.")

        return

    audio = np.concatenate(audio_parts)

    sf.write(
        "tests/voxera_response.wav",
        audio,
        24000
    )

    elapsed = time.perf_counter() - start

    print(f"TTS: {elapsed:.2f}s")
    print("🔊 Voxera speaking...")

    sd.play(
        audio,
        24000
    )

    sd.wait()

    print("✅ Voxera finished speaking.")


# ============================================================
# MAIN CONVERSATION
# ============================================================

def main():

    print("\n")
    print("=" * 60)
    print("VOXERA IS READY")
    print("=" * 60)

    print("""
Speak naturally.

You can pause while talking.
Voxera will wait for you to finish.

Press ENTER only if automatic turn detection fails.

Press CTRL+C to stop.
""")

    threading.Thread(
        target=keyboard_listener,
        daemon=True
    ).start()

    try:

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=CHUNK_SIZE,
            callback=audio_callback
        ):

            while True:

                # Remove audio left over from previous turn
                clear_audio_queue()

                # ------------------------------------------------
                # LISTEN
                # ------------------------------------------------

                first_chunk = wait_for_speech()

                # ------------------------------------------------
                # RECORD
                # ------------------------------------------------

                audio = record_turn(
                    first_chunk
                )

                # ------------------------------------------------
                # STT
                # ------------------------------------------------

                user_text = transcribe(
                    audio
                )

                if not user_text:

                    print("⚠️ No speech detected.")

                    continue

                print()
                print("-" * 60)
                print("PATIENT:")
                print(user_text)
                print("-" * 60)

                # ------------------------------------------------
                # AI
                # ------------------------------------------------

                response = think(
                    user_text
                )

                print()
                print("VOXERA:")
                print(response)

                # ------------------------------------------------
                # TTS
                # ------------------------------------------------

                speak(
                    response
                )

                print()
                print("🔄 Your turn...")


    except KeyboardInterrupt:

        print("\n\nVOXERA STOPPED.")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()