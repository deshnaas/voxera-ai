# ============================================================
# VOXERA — REAL-TIME CONVERSATIONAL HEALTHCARE VOICE AGENT
# ============================================================
#
# PIPELINE
#
# Microphone
#     ↓
# Robust Voice Activity Detection
#     ↓
# Whisper (speed-configurable)
#     ↓
# Fast Healthcare Brain — Qwen3 1.7B by default
#     ↓
# Priya / Kokoro
#     ↓
# STRICT JSON RESPONSE EXTRACTION
#     ↓
# Priya / Kokoro
#     ↓
# Speaker
#
# IMPORTANT
# - No ENTER required
# - No keyword list for healthcare scope
# - Scope decided semantically by AI
# - Qwen thinking is NEVER spoken
# - Healthcare model must return patient-facing speech
# - Priya voice pack: (510, 1, 256)
# - Correct Kokoro voice indexing: pack[len(phonemes)-1]
# - TTS failure never kills conversation
# - VAD uses adaptive start/end thresholds
# ============================================================

import os
import re
import sys
import time
import queue
from collections import deque

import numpy as np
import sounddevice as sd
import soundfile as sf
import whisper
import requests


# ============================================================
# BARGE-IN ONLY
# ============================================================
# These settings affect ONLY interruption while Priya is speaking.
# The patient VAD/turn settings below remain unchanged.
BARGE_MIN_SPEECH_SECONDS = 0.25
BARGE_PRE_ROLL_SECONDS = 0.40
BARGE_ECHO_CORRELATION = 0.80
BARGE_SEARCH_SECONDS = 0.45

pending_barge_audio = None
start_threshold_for_barge = 0.0090


def _resample_for_barge(audio, src_rate, dst_rate):
    audio = np.asarray(audio, dtype=np.float32).flatten()
    if len(audio) == 0 or src_rate == dst_rate:
        return audio
    new_len = max(1, int(round(len(audio) * dst_rate / src_rate)))
    old_x = np.linspace(0.0, 1.0, len(audio), endpoint=False)
    new_x = np.linspace(0.0, 1.0, new_len, endpoint=False)
    return np.interp(new_x, old_x, audio).astype(np.float32)


def _normalised_correlation(a, b):
    a = np.asarray(a, dtype=np.float32).flatten()
    b = np.asarray(b, dtype=np.float32).flatten()
    n = min(len(a), len(b))
    if n < int(SAMPLE_RATE * 0.04):
        return 0.0
    a = a[:n] - np.mean(a[:n])
    b = b[:n] - np.mean(b[:n])
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-8:
        return 0.0
    return float(abs(np.dot(a, b)) / denom)


def _looks_like_priya_echo(mic_chunk, playback_audio_16k, playback_position):
    """Cheap local echo check using already-resampled playback audio."""
    if playback_audio_16k is None or len(playback_audio_16k) == 0:
        return False

    mic = np.asarray(mic_chunk, dtype=np.float32).flatten()
    if len(mic) == 0:
        return False

    center = int(playback_position)
    half = len(mic)
    search = int(BARGE_SEARCH_SECONDS * SAMPLE_RATE)
    step = max(1, int(0.04 * SAMPLE_RATE))

    start_pos = max(0, center - search)
    end_pos = min(len(playback_audio_16k), center + search + half)

    for pos in range(start_pos, max(start_pos + 1, end_pos - half + 1), step):
        ref = playback_audio_16k[pos:pos + len(mic)]
        if len(ref) < len(mic) * 0.85:
            continue
        corr = _normalised_correlation(mic, ref)
        if corr >= BARGE_ECHO_CORRELATION:
            return True

    return False


def _wait_for_barge_in(playback_audio, playback_rate):
    global pending_barge_audio

    recent = deque(
        maxlen=max(1, int(BARGE_PRE_ROLL_SECONDS / CHUNK_SECONDS))
    )
    speech_chunks = []
    speech_time = 0.0

    # IMPORTANT: resample ONCE, not once per microphone chunk.
    playback_audio_16k = _resample_for_barge(
        playback_audio, playback_rate, SAMPLE_RATE
    )
    playback_position = 0

    while playback_position < len(playback_audio_16k):
        try:
            chunk = audio_queue.get(timeout=CHUNK_SECONDS)
        except queue.Empty:
            playback_position += CHUNK_SIZE
            continue

        chunk = np.asarray(chunk, dtype=np.float32).flatten()
        recent.append(chunk)

        level = rms(chunk)

        # Only do the relatively expensive correlation when the mic
        # actually contains enough energy to plausibly be speech.
        is_echo = False
        if level >= start_threshold_for_barge:
            is_echo = _looks_like_priya_echo(
                chunk,
                playback_audio_16k,
                playback_position
            )

        if level >= start_threshold_for_barge and not is_echo:
            speech_chunks.append(chunk)
            speech_time += len(chunk) / SAMPLE_RATE

            if speech_time >= BARGE_MIN_SPEECH_SECONDS:
                print()
                print("🛑 BARGE-IN detected — Voxera stopped speaking.")
                pending_barge_audio = list(recent) + speech_chunks
                return True
        else:
            speech_chunks.clear()
            speech_time = 0.0

        playback_position += len(chunk)

    return False


# ============================================================
# CONFIGURATION
# ============================================================

SAMPLE_RATE = 16000

CHUNK_SECONDS = 0.10
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_SECONDS)

# YOUR WORKING MICROPHONE
MICROPHONE_DEVICE = 1


# ============================================================
# VAD CONFIGURATION
# ============================================================

CALIBRATION_SECONDS = 1.5

# Speech must remain above the START threshold this long.
MIN_SPEECH_SECONDS = 0.30

# Silence after speech before turn ends.
#
# Increased from 1.35 so natural pauses don't cut the user off.
END_SILENCE_SECONDS = 1.15

# Maximum patient turn.
MAX_TURN_SECONDS = 30.0

# Audio before speech onset.
PRE_ROLL_SECONDS = 0.50

# Audio retained after detected ending.
POST_ROLL_SECONDS = 0.15

PRE_ROLL_CHUNKS = max(
    1,
    int(PRE_ROLL_SECONDS / CHUNK_SECONDS)
)


# ============================================================
# VAD THRESHOLD LIMITS
# ============================================================

# These are deliberately based on your actual microphone levels.
#
# Your previous logs showed speech around:
#
# RMS ~ 0.008
# RMS ~ 0.010
#
# Therefore a start threshold of 0.012 can completely prevent
# speech detection.
#

MIN_START_THRESHOLD = 0.0045
MAX_START_THRESHOLD = 0.0090

MIN_END_THRESHOLD = 0.0025
MAX_END_THRESHOLD = 0.0060


# ============================================================
# AUDIO QUALITY
# ============================================================

MIN_AUDIO_SECONDS = 0.70

MIN_RMS_FOR_TRANSCRIPTION = 0.0012
MIN_PEAK_FOR_TRANSCRIPTION = 0.012


# ============================================================
# OLLAMA
# ============================================================

OLLAMA_URL = "http://localhost:11434/api/chat"

# ============================================================
# SPEED-FIRST MODE
# ============================================================
# One LLM call per turn is much better for a phone conversation.
# The scope gate is kept in the file, but disabled by default.
USE_SCOPE_GATE = False

SCOPE_MODEL = "qwen3:1.7b"
# 1.7B is the default fast brain. Set VOXERA_HEALTHCARE_MODEL=qwen3:4b
# later if you want to trade latency for more reasoning capacity.
HEALTHCARE_MODEL = os.getenv(
    "VOXERA_HEALTHCARE_MODEL",
    "qwen3:1.7b"
)

WHISPER_MODEL_NAME = os.getenv(
    "VOXERA_WHISPER_MODEL",
    "base"
)

SCOPE_TIMEOUT = 8
HEALTHCARE_TIMEOUT = 15

SCOPE_MAX_TOKENS = 8
HEALTHCARE_MAX_TOKENS = 90

# Keep Ollama models resident between turns.
OLLAMA_KEEP_ALIVE = -1

# Set True only when you want WAV debug files on disk.
SAVE_DEBUG_AUDIO = False
SAVE_DEBUG_TTS = False


# ============================================================
# TTS
# ============================================================

TTS_SPEED = 1.10
TTS_SAMPLE_RATE = 24000


# ============================================================
# PATHS
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

GOONJ_DIR = os.path.join(
    BASE_DIR,
    "models",
    "goonj"
)

TTS_OUTPUT = os.path.join(
    BASE_DIR,
    "voxera_priya_response.wav"
)

DEBUG_AUDIO = os.path.join(
    BASE_DIR,
    "last_patient_audio.wav"
)

if GOONJ_DIR not in sys.path:
    sys.path.insert(0, GOONJ_DIR)


# ============================================================
# GLOBALS
# ============================================================

audio_queue = queue.Queue()

conversation = []

whisper_model = None

priya_model = None
priya_voice = None
goonj = None
torch = None
priya_audio_cache = None

TTS_DEVICE = "cpu"


# ============================================================
# HEADER
# ============================================================

print()
print("=" * 65)
print("VOXERA — REAL-TIME CONVERSATIONAL HEALTHCARE VOICE AGENT")
print("=" * 65)
print()


# ============================================================
# LOAD WHISPER
# ============================================================

print(f"🧠 Loading Whisper {WHISPER_MODEL_NAME.upper()}...")
print("   This happens once.")
print()

try:

    whisper_model = whisper.load_model(WHISPER_MODEL_NAME)

except Exception as e:

    print()
    print("❌ WHISPER LOAD ERROR")
    print(e)
    raise

print("✅ Whisper ready.")
print()


# ============================================================
# LOAD PRIYA / KOKORO
# ============================================================

print("🔊 Loading Priya voice engine...")
print("   This happens once.")
print()

try:

    import torch as _torch

    torch = _torch

    from kokoro import KModel
    import kokoro_generate as _goonj

    goonj = _goonj

    # --------------------------------------------------------
    # Resolve Priya voice pack
    # --------------------------------------------------------

    PRIYA_VOICE_PATH = goonj.resolve_voice(
        "en_priya"
    )

    print(
        f"   Voice: {PRIYA_VOICE_PATH}"
    )

    priya_voice = torch.load(
        PRIYA_VOICE_PATH,
        map_location="cpu",
        weights_only=True
    )

    if not isinstance(
        priya_voice,
        torch.Tensor
    ):

        priya_voice = torch.as_tensor(
            priya_voice
        )

    print(
        f"   Voice pack shape: "
        f"{tuple(priya_voice.shape)}"
    )

    # --------------------------------------------------------
    # Validate Priya pack
    #
    # Expected:
    #
    # (510, 1, 256)
    #
    # Kokoro official voice packs are indexed according to
    # phoneme length.
    # --------------------------------------------------------

    if priya_voice.ndim != 3:

        raise RuntimeError(
            "Priya voice pack must have 3 dimensions. "
            f"Got {tuple(priya_voice.shape)}"
        )

    if priya_voice.shape[1] != 1:

        raise RuntimeError(
            "Unexpected Priya voice pack shape: "
            f"{tuple(priya_voice.shape)}"
        )

    if priya_voice.shape[2] != 256:

        raise RuntimeError(
            "Unexpected Priya embedding dimension: "
            f"{tuple(priya_voice.shape)}"
        )

    if priya_voice.shape[0] < 1:

        raise RuntimeError(
            "Priya voice pack is empty."
        )

    # --------------------------------------------------------
    # Device
    # --------------------------------------------------------

    if torch.cuda.is_available():

        TTS_DEVICE = "cuda"

    else:

        TTS_DEVICE = "cpu"

    print(
        f"   Device: {TTS_DEVICE}"
    )

    # --------------------------------------------------------
    # Kokoro model
    # --------------------------------------------------------

    KOKORO_MODEL_PATH = str(
        goonj.ROOT / "kokoro_hindi_final.pth"
    )

    KOKORO_CONFIG_PATH = str(
        goonj.ROOT / "config.json"
    )

    print(
        "   Loading Kokoro model..."
    )

    priya_model = KModel(
        repo_id="hexgrad/Kokoro-82M",
        config=KOKORO_CONFIG_PATH,
        model=KOKORO_MODEL_PATH
    )

    priya_model = (
        priya_model
        .to(TTS_DEVICE)
        .eval()
    )

    print("   ✅ Kokoro ready.")
    print("   ✅ Priya ready.")
    print()

except Exception as e:

    print()
    print("❌ PRIYA LOAD ERROR")
    print(e)
    print()

    raise


# ============================================================
# AUDIO CALLBACK
# ============================================================

def audio_callback(
    indata,
    frames,
    time_info,
    status
):

    if status:

        print(
            f"\n[Audio] {status}"
        )

    try:

        chunk = (
            indata[:, 0]
            .copy()
            .astype(np.float32)
        )

        audio_queue.put_nowait(
            chunk
        )

    except Exception:

        pass


# ============================================================
# RMS
# ============================================================

def rms(audio):

    if audio is None:
        return 0.0

    if len(audio) == 0:
        return 0.0

    audio = np.asarray(
        audio,
        dtype=np.float32
    )

    value = np.sqrt(
        np.mean(
            audio * audio
        )
    )

    return float(value)


# ============================================================
# PEAK
# ============================================================

def peak(audio):

    if audio is None:
        return 0.0

    if len(audio) == 0:
        return 0.0

    return float(
        np.max(
            np.abs(audio)
        )
    )


# ============================================================
# CLEAR AUDIO QUEUE
# ============================================================

def clear_audio_queue():

    while True:

        try:

            audio_queue.get_nowait()

        except queue.Empty:

            break


# ============================================================
# MICROPHONE CALIBRATION
# ============================================================

def calibrate_microphone():

    print("🎧 Calibrating microphone...")
    print("   Stay quiet for a moment...")
    print()

    levels = []

    start = time.perf_counter()

    while (
        time.perf_counter() - start
        < CALIBRATION_SECONDS
    ):

        try:

            chunk = audio_queue.get(
                timeout=0.25
            )

            levels.append(
                rms(chunk)
            )

        except queue.Empty:

            pass

    if not levels:

        noise_floor = 0.0015

    else:

        # Median is more stable than a high percentile.
        noise_floor = float(
            np.median(levels)
        )

    # --------------------------------------------------------
    # Adaptive start threshold.
    #
    # We deliberately keep this LOW because your microphone
    # produces speech around RMS 0.008-0.010.
    # --------------------------------------------------------

    start_threshold = noise_floor * 2.5

    start_threshold = max(
        MIN_START_THRESHOLD,
        start_threshold
    )

    start_threshold = min(
        MAX_START_THRESHOLD,
        start_threshold
    )

    # --------------------------------------------------------
    # End threshold is lower than start threshold.
    #
    # This hysteresis prevents a normal quiet syllable from
    # instantly ending the turn.
    # --------------------------------------------------------

    end_threshold = start_threshold * 0.55

    end_threshold = max(
        MIN_END_THRESHOLD,
        end_threshold
    )

    end_threshold = min(
        MAX_END_THRESHOLD,
        end_threshold
    )

    print(
        f"🎚️ Noise floor: "
        f"{noise_floor:.6f}"
    )

    print(
        f"🎚️ Speech start threshold: "
        f"{start_threshold:.6f}"
    )

    print(
        f"🎚️ Speech end threshold: "
        f"{end_threshold:.6f}"
    )

    print()

    return (
        start_threshold,
        end_threshold
    )


# ============================================================
# WAIT FOR SPEECH
# ============================================================

def wait_for_speech(
    start_threshold
):

    print(
        "🎤 Listening for you..."
    )

    pre_roll = deque(
        maxlen=PRE_ROLL_CHUNKS
    )

    speech_time = 0.0

    while True:

        try:

            chunk = audio_queue.get(
                timeout=1.0
            )

        except queue.Empty:

            continue

        pre_roll.append(
            chunk
        )

        level = rms(chunk)

        # ----------------------------------------------------
        # Speech onset
        # ----------------------------------------------------

        if level >= start_threshold:

            speech_time += CHUNK_SECONDS

        else:

            # Don't instantly forget a small dip.
            speech_time = max(
                0.0,
                speech_time - CHUNK_SECONDS * 0.5
            )

        # ----------------------------------------------------
        # Confirm speech
        # ----------------------------------------------------

        if speech_time >= MIN_SPEECH_SECONDS:

            print(
                "🗣️ Patient speaking..."
            )

            return list(
                pre_roll
            )


# ============================================================
# RECORD PATIENT TURN
# ============================================================

def record_turn(
    first_chunks,
    start_threshold,
    end_threshold
):

    chunks = list(
        first_chunks
    )

    total_duration = (
        sum(
            len(x)
            for x in chunks
        )
        / SAMPLE_RATE
    )

    silence_time = 0.0

    speech_seen = True

    start_time = time.perf_counter()

    while True:

        # ----------------------------------------------------
        # Hard maximum
        # ----------------------------------------------------

        if (
            time.perf_counter()
            - start_time
            >= MAX_TURN_SECONDS
        ):

            print(
                "⏱️ Maximum turn length reached."
            )

            break

        try:

            chunk = audio_queue.get(
                timeout=0.25
            )

        except queue.Empty:

            continue

        chunks.append(
            chunk
        )

        duration = (
            len(chunk)
            / SAMPLE_RATE
        )

        total_duration += duration

        level = rms(chunk)

        # ----------------------------------------------------
        # SPEECH
        # ----------------------------------------------------

        if level >= end_threshold:

            silence_time = 0.0
            speech_seen = True

        # ----------------------------------------------------
        # SILENCE
        # ----------------------------------------------------

        else:

            silence_time += duration

        # ----------------------------------------------------
        # END OF TURN
        #
        # Only allow silence to terminate AFTER speech has
        # actually been detected.
        # ----------------------------------------------------

        if (
            speech_seen
            and silence_time
            >= END_SILENCE_SECONDS
        ):

            print(
                "⏸️ End of patient turn detected."
            )

            break

    # --------------------------------------------------------
    # Combine
    # --------------------------------------------------------

    if not chunks:

        return np.array(
            [],
            dtype=np.float32
        )

    audio = np.concatenate(
        chunks
    ).astype(
        np.float32
    )

    # --------------------------------------------------------
    # Trim a small amount of trailing silence only.
    # --------------------------------------------------------

    trim_samples = int(
        POST_ROLL_SECONDS
        * SAMPLE_RATE
    )

    if (
        trim_samples > 0
        and len(audio) > trim_samples
    ):

        audio = audio[
            :-trim_samples
        ]

    print()
    print("🎧 Captured audio:")

    print(
        f"   Duration: "
        f"{len(audio) / SAMPLE_RATE:.2f}s"
    )

    print(
        f"   Peak: "
        f"{peak(audio):.6f}"
    )

    print(
        f"   RMS: "
        f"{rms(audio):.6f}"
    )

    return audio


# ============================================================
# WHISPER TRANSCRIPTION
# ============================================================

def transcribe(
    audio
):

    duration = (
        len(audio)
        / SAMPLE_RATE
    )

    audio_rms = rms(audio)
    audio_peak = peak(audio)

    if duration < MIN_AUDIO_SECONDS:

        print(
            f"⚠️ Audio too short "
            f"({duration:.2f}s)."
        )

        return ""

    if (
        audio_rms < MIN_RMS_FOR_TRANSCRIPTION
        and
        audio_peak < MIN_PEAK_FOR_TRANSCRIPTION
    ):

        print(
            "⚠️ Audio too quiet."
        )

        return ""

    # Disk I/O is disabled by default for low-latency calls.
    if SAVE_DEBUG_AUDIO:
        try:
            sf.write(DEBUG_AUDIO, audio, SAMPLE_RATE)
            print(f"💾 Debug audio: {DEBUG_AUDIO}")
        except Exception as e:
            print(f"⚠️ Debug audio error: {e}")

    print(
        "🧠 Transcribing..."
    )

    start = time.perf_counter()

    try:

        result = whisper_model.transcribe(

            audio,

            language="en",

            fp16=bool(getattr(__import__("torch"), "cuda").is_available()),

            temperature=0,

            condition_on_previous_text=False,

            no_speech_threshold=0.65,

            logprob_threshold=-1.0,

            compression_ratio_threshold=2.4,

            initial_prompt=None,

            verbose=False
        )

    except Exception as e:

        print()
        print(
            "❌ WHISPER ERROR"
        )

        print(e)

        return ""

    elapsed = (
        time.perf_counter()
        - start
    )

    print(
        f"STT: {elapsed:.2f}s"
    )

    text = (
        result.get(
            "text",
            ""
        )
        .strip()
    )

    # --------------------------------------------------------
    # Confidence
    # --------------------------------------------------------

    segments = result.get(
        "segments",
        []
    )

    if segments:

        confidence_values = []

        for segment in segments:

            no_speech = float(
                segment.get(
                    "no_speech_prob",
                    0.0
                )
            )

            confidence_values.append(
                1.0 - no_speech
            )

        if confidence_values:

            confidence = (
                sum(confidence_values)
                /
                len(confidence_values)
            )

            print(
                f"🎯 Speech confidence: "
                f"{confidence:.2f}"
            )

            # Only reject extremely weak short recordings.
            if (
                confidence < 0.30
                and duration < 3.0
            ):

                print(
                    "⚠️ Very low speech confidence."
                )

                return ""

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    if not text:

        print(
            "⚠️ Whisper returned no speech."
        )

        return ""

    return text


# ============================================================
# SCOPE CLASSIFIER
# ============================================================

SCOPE_SYSTEM_PROMPT = """
You are Voxera's healthcare scope classifier.

Your ONLY task is classification.

Read the patient's message and determine whether the message
is about a health, medical, physical, mental wellbeing,
injury, symptom, illness, medication, treatment, emergency,
healthcare, or caregiving concern.

Classify based on meaning and intent.

Return ONLY this exact JSON:

{"scope":"HEALTHCARE"}

OR

{"scope":"OUT_OF_SCOPE"}

Do not answer the patient.
Do not explain.
Do not reason aloud.
Do not output anything else.
"""


def classify_scope(
    patient_text
):

    payload = {

        "model": SCOPE_MODEL,

        "messages": [

            {
                "role": "system",
                "content": SCOPE_SYSTEM_PROMPT
            },

            {
                "role": "user",
                "content": patient_text
            }

        ],

        "stream": False,

        "think": False,

        "format": {

            "type": "object",

            "properties": {

                "scope": {

                    "type": "string",

                    "enum": [
                        "HEALTHCARE",
                        "OUT_OF_SCOPE"
                    ]
                }

            },

            "required": [
                "scope"
            ]

        },

        "options": {

            "temperature": 0,

            "num_predict": SCOPE_MAX_TOKENS,

            "num_ctx": 1024
        }
    }

    start = time.perf_counter()

    try:

        response = requests.post(

            OLLAMA_URL,

            json=payload,

            timeout=SCOPE_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        # ----------------------------------------------------
        # Ollama may expose thinking separately.
        # We deliberately ignore it.
        # ----------------------------------------------------

        message = data.get(
            "message",
            {}
        )

        raw = message.get(
            "content",
            ""
        )

        # ----------------------------------------------------
        # Extract JSON.
        # ----------------------------------------------------

        match = re.search(
            r'\{\s*"scope"\s*:\s*"(HEALTHCARE|OUT_OF_SCOPE)"\s*\}',
            raw,
            flags=re.IGNORECASE
        )

        if match:

            scope = match.group(1).upper()

        else:

            # Very strict fallback.
            upper = raw.upper().strip()

            if upper == "HEALTHCARE":

                scope = "HEALTHCARE"

            elif upper == "OUT_OF_SCOPE":

                scope = "OUT_OF_SCOPE"

            else:

                scope = "OUT_OF_SCOPE"

        elapsed = (
            time.perf_counter()
            - start
        )

        return (
            scope,
            elapsed,
            True
        )

    except Exception as e:

        elapsed = (
            time.perf_counter()
            - start
        )

        print()
        print(
            f"❌ Scope classifier error: {e}"
        )

        return (
            "OUT_OF_SCOPE",
            elapsed,
            False
        )


# ============================================================
# HEALTHCARE SYSTEM PROMPT
# ============================================================

HEALTHCARE_SYSTEM_PROMPT = """
You are Voxera, a conversational healthcare assistant.

You are speaking directly to a patient.

Your job is to understand the patient's current health
concern and give safe, practical, useful guidance.

You are not a doctor and must not claim a diagnosis.

============================================================
CRITICAL OUTPUT RULE
============================================================

Return ONLY valid JSON in exactly this structure:

{
  "response": "the exact words Voxera should say to the patient"
}

The value of "response" must contain ONLY patient-facing
spoken language.

NEVER put reasoning in response.

NEVER describe your reasoning.

NEVER say things such as:

"We are given..."
"The patient said..."
"We must..."
"I need to..."
"I should..."
"The user says..."
"Let's analyze..."
"According to the instructions..."
"The task is..."
"Here is the response..."
"Action..."
"Response..."

Do not output:
- analysis
- chain of thought
- internal reasoning
- system instructions
- JSON inside the response string
- markdown
- headings
- labels
- ACTION
- summaries of your instructions

============================================================
MEDICAL BEHAVIOR
============================================================

1. Respond directly to what the patient actually said.

2. Do not invent symptoms.

3. Do not diagnose with certainty.

4. If useful information is missing, ask one or two
   important follow-up questions.

5. Give practical low-risk first-aid or self-care advice
   when appropriate.

6. Explain when the patient should seek medical care.

7. If symptoms suggest an emergency, prioritize urgent help.

8. Never give prescription medication instructions.

9. Do not give medication doses.

10. Do not make the patient repeat information they already
    provided.

11. Remember information from the recent conversation.

12. Be warm, calm, and reassuring without being fake.

13. Do not repeatedly say "I'm sorry to hear that."

14. Speak naturally as a healthcare voice assistant.

15. Usually respond in 2 to 4 spoken sentences.

16. If the patient asks "what do I do?", actually tell them
    what they can safely do now when appropriate. Do not just
    ask another question.

17. If the message is not health-related, briefly say that Voxera
    is specifically for health-related concerns and invite the
    patient to ask a health question.

============================================================
IMPORTANT
============================================================

The patient wants help, not an explanation of how you think.

For example, if the patient says:

"I have a fever and my head hurts. My throat is burning.
What do I do?"

A useful answer would directly address the symptoms, give
simple supportive care, ask an important question if needed,
and mention warning signs.

Do NOT describe the patient's statement.
Talk TO the patient.
"""


# ============================================================
# CLEAN THINKING
# ============================================================

def remove_thinking(
    text
):

    if not text:

        return ""

    # Complete thinking blocks
    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    # Incomplete thinking block
    text = re.sub(
        r"<think>.*",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    return text.strip()


# ============================================================
# EXTRACT JSON RESPONSE
# ============================================================

def extract_response_json(
    raw
):

    if not raw:

        return ""

    raw = remove_thinking(
        raw
    ).strip()

    # --------------------------------------------------------
    # Direct JSON
    # --------------------------------------------------------

    try:

        import json

        obj = json.loads(
            raw
        )

        if isinstance(obj, dict):

            response = obj.get(
                "response",
                ""
            )

            if isinstance(
                response,
                str
            ):

                return response.strip()

    except Exception:

        pass

    # --------------------------------------------------------
    # JSON embedded inside extra garbage
    # --------------------------------------------------------

    match = re.search(
        r'\{\s*"response"\s*:\s*"((?:\\.|[^"\\])*)"\s*\}',
        raw,
        flags=re.DOTALL
    )

    if match:

        try:

            import json

            return json.loads(
                '"' + match.group(1) + '"'
            ).strip()

        except Exception:

            pass

    return ""


# ============================================================
# RESPONSE VALIDATION
# ============================================================

def is_patient_facing_response(
    text
):

    if not text:

        return False

    text = text.strip()

    lower = text.lower()

    # --------------------------------------------------------
    # Never let obvious reasoning reach TTS.
    # --------------------------------------------------------

    forbidden = [

        "we are given",
        "we must",
        "we need to",
        "i need to respond",
        "i should respond",
        "the patient said",
        "the patient message",
        "the user says",
        "the task is",
        "let's analyze",
        "chain of thought",
        "reasoning:",
        "analysis:",
        "according to the instructions",
        "as an ai",
        "system prompt",
        "action:",
        "response:",
        "here is the response"
    ]

    for phrase in forbidden:

        if phrase in lower:

            return False

    # --------------------------------------------------------
    # Never speak raw JSON.
    # --------------------------------------------------------

    if text.startswith("{"):

        return False

    if text.startswith("["):

        return False

    # --------------------------------------------------------
    # Don't allow massive accidental output.
    # --------------------------------------------------------

    if len(
        text.split()
    ) > 110:

        return False

    # --------------------------------------------------------
    # Don't allow empty/punctuation-only output.
    # --------------------------------------------------------

    if not re.search(
        r"[A-Za-z]",
        text
    ):

        return False

    return True


# ============================================================
# BUILD HEALTHCARE CONTEXT
# ============================================================

def build_healthcare_messages(
    patient_text
):

    messages = [

        {
            "role": "system",
            "content": HEALTHCARE_SYSTEM_PROMPT
        }

    ]

    # --------------------------------------------------------
    # Only include ACTUAL conversation.
    #
    # Never include raw model reasoning.
    # --------------------------------------------------------

    recent = conversation[-6:]

    for item in recent:

        if item["role"] not in (
            "user",
            "assistant"
        ):

            continue

        if not item["content"]:

            continue

        messages.append({

            "role": item["role"],

            "content": item["content"]

        })

    messages.append({

        "role": "user",

        "content": patient_text

    })

    return messages


# ============================================================
# HEALTHCARE RESPONSE GENERATION
# ============================================================

def generate_healthcare_response(
    patient_text
):
    """Generate one short patient-facing response with minimum latency."""
    messages = build_healthcare_messages(patient_text)

    payload = {
        "model": HEALTHCARE_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "format": {
            "type": "object",
            "properties": {
                "response": {"type": "string"}
            },
            "required": ["response"]
        },
        "options": {
            "temperature": 0.10,
            "top_p": 0.80,
            "num_predict": HEALTHCARE_MAX_TOKENS,
            "num_ctx": 2048,
            "repeat_penalty": 1.08
        }
    }

    print(f"🧠 Voxera healthcare brain ({HEALTHCARE_MODEL})...")
    start = time.perf_counter()

    try:
        response = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=HEALTHCARE_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        raw = message.get("content", "")

        elapsed = time.perf_counter() - start
        print(f"AI: {elapsed:.2f}s")

        cleaned = extract_response_json(raw)
        if is_patient_facing_response(cleaned):
            return cleaned

        # Do NOT spend another full model call repairing output.
        # The phone call gets a safe fallback instead.
        print("⚠️ AI output failed validation; using safe fallback.")
        return (
            "I want to make sure I give you safe advice. "
            "Could you tell me a little more about what you're experiencing?"
        )

    except Exception as e:
        elapsed = time.perf_counter() - start
        print()
        print(f"❌ HEALTHCARE BRAIN ERROR after {elapsed:.2f}s")
        print(e)
        return (
            "I'm having trouble processing that right now. "
            "Please tell me again what you're experiencing."
        )


# ============================================================
# EMERGENCY / SAFETY CHECK
# ============================================================

def emergency_override(
    text
):

    lower = text.lower()

    # --------------------------------------------------------
    # Physical emergencies.
    #
    # This is intentionally small and conservative.
    # It does NOT decide healthcare scope.
    # --------------------------------------------------------

    physical_emergencies = [

        "can't breathe",
        "cannot breathe",
        "difficulty breathing",
        "trouble breathing",
        "hard to breathe",
        "struggling to breathe",
        "suffocating",

        "severe chest pain",
        "crushing chest pain",

        "unconscious",
        "passed out",
        "not conscious",

        "severe bleeding",
        "bleeding heavily",

        "vomiting blood",
        "throwing up blood",

        "coughing blood",

        "seizure"
    ]

    for phrase in physical_emergencies:

        if phrase in lower:

            return (
                "urgent",
                "This could be an emergency. Please seek "
                "urgent medical help now, and if you are in "
                "immediate danger, contact your local emergency "
                "service or have someone nearby help you."
            )

    # --------------------------------------------------------
    # Explicit self-harm / suicidal language.
    #
    # This is handled separately from normal healthcare
    # reasoning because it requires immediate supportive
    # escalation.
    # --------------------------------------------------------

    self_harm_phrases = [

        "i want to die",
        "i wanna die",
        "i'm ready to die",
        "im ready to die",
        "i am ready to die",
        "i want to kill myself",
        "i wanna kill myself",
        "i'm going to kill myself",
        "im going to kill myself",
        "i plan to kill myself",
        "i don't want to live",
        "i dont want to live"
    ]

    for phrase in self_harm_phrases:

        if phrase in lower:

            return (
                "crisis",
                "I'm really glad you told me. Please don't stay "
                "alone with this right now. Move away from anything "
                "you could use to hurt yourself and get a trusted "
                "person to stay with you. If you might hurt "
                "yourself right now, contact your local emergency "
                "service or go to the nearest emergency department."
            )

    return None


# ============================================================
# SAVE CONVERSATION
# ============================================================

def save_conversation(
    patient_text,
    response
):

    # --------------------------------------------------------
    # Store ONLY actual spoken content.
    # --------------------------------------------------------

    if patient_text:

        conversation.append({

            "role": "user",

            "content": patient_text

        })

    if response:

        conversation.append({

            "role": "assistant",

            "content": response

        })

    # --------------------------------------------------------
    # Keep memory tiny.
    # --------------------------------------------------------

    if len(conversation) > 10:

        del conversation[:-10]


# ============================================================
# PRIYA VOICE EMBEDDING
# ============================================================

def get_priya_embedding(
    phoneme_count
):

    voice = priya_voice

    # --------------------------------------------------------
    # EXPECTED PRIYA PACK:
    #
    # (510, 1, 256)
    #
    # Kokoro voice packs contain a style embedding for each
    # possible phoneme length.
    #
    # IMPORTANT:
    #
    # The correct index is:
    #
    #     len(phonemes) - 1
    #
    # NOT len(phonemes)
    #
    # This was the source of your previous TTS failures.
    # --------------------------------------------------------

    if voice.ndim == 3:

        max_index = voice.shape[0] - 1

        index = phoneme_count - 1

        index = max(
            0,
            min(
                index,
                max_index
            )
        )

        ref_s = voice[index]

        # ----------------------------------------------------
        # (1, 256) is exactly what KModel expects.
        # ----------------------------------------------------

        if ref_s.ndim != 2:

            raise RuntimeError(
                "Invalid Priya selected embedding shape: "
                f"{tuple(ref_s.shape)}"
            )

        if ref_s.shape != (
            1,
            256
        ):

            raise RuntimeError(
                "Unexpected Priya embedding shape: "
                f"{tuple(ref_s.shape)}"
            )

        return ref_s

    # --------------------------------------------------------
    # Support (N,256) packs too.
    # --------------------------------------------------------

    if voice.ndim == 2:

        max_index = voice.shape[0] - 1

        index = phoneme_count - 1

        index = max(
            0,
            min(
                index,
                max_index
            )
        )

        ref_s = voice[index]

        if ref_s.shape != (
            256,
        ):

            raise RuntimeError(
                "Unexpected Priya embedding shape: "
                f"{tuple(ref_s.shape)}"
            )

        # KModel expects [1,256]
        return ref_s.unsqueeze(0)

    # --------------------------------------------------------
    # Single embedding.
    # --------------------------------------------------------

    if voice.ndim == 1:

        if voice.shape[0] != 256:

            raise RuntimeError(
                "Unexpected Priya embedding dimension: "
                f"{tuple(voice.shape)}"
            )

        return voice.unsqueeze(0)

    raise RuntimeError(
        "Unsupported Priya voice embedding dimensions: "
        f"{tuple(voice.shape)}"
    )


# ============================================================
# PRIYA TTS
# ============================================================

def generate_priya(
    text
):
    global priya_audio_cache

    if not text:
        return False

    print()
    print("🔊 Generating Priya voice...")

    words = text.split()
    if len(words) > 90:
        print("⚠️ Response too long for TTS.")
        return False

    start = time.perf_counter()

    try:
        phonemes = goonj.phonemize(text, "en")
        if not phonemes:
            raise RuntimeError("Phonemizer returned empty phonemes.")

        phoneme_count = min(len(phonemes), 509)
        if len(phonemes) > 509:
            phonemes = phonemes[:509]

        ref_s = get_priya_embedding(phoneme_count).to(TTS_DEVICE)

        with torch.inference_mode():
            audio = priya_model(
                phonemes,
                ref_s,
                speed=TTS_SPEED
            )

        if audio is None:
            raise RuntimeError("Kokoro returned no audio.")

        if isinstance(audio, torch.Tensor):
            audio_numpy = audio.detach().cpu().numpy()
        else:
            audio_numpy = np.asarray(audio, dtype=np.float32)

        audio_numpy = np.asarray(audio_numpy, dtype=np.float32).squeeze()
        if audio_numpy.ndim != 1:
            audio_numpy = audio_numpy.reshape(-1)

        if len(audio_numpy) == 0:
            raise RuntimeError("Kokoro generated empty audio.")

        if not np.isfinite(audio_numpy).all():
            raise RuntimeError("Kokoro generated invalid audio.")

        # Keep the generated waveform in memory. Avoid writing a WAV and
        # reading it back before every response.
        priya_audio_cache = audio_numpy

        if SAVE_DEBUG_TTS:
            sf.write(TTS_OUTPUT, audio_numpy, TTS_SAMPLE_RATE)
            print(f"[goonj] wrote: {TTS_OUTPUT}")

        elapsed = time.perf_counter() - start
        duration = len(audio_numpy) / TTS_SAMPLE_RATE
        print(f"[goonj] duration: {duration:.2f}s")
        print(f"TTS generation: {elapsed:.2f}s")
        return True

    except Exception as e:
        priya_audio_cache = None
        print()
        print("❌ PRIYA TTS ERROR")
        print(repr(e))
        return False


# ============================================================
# PLAY PRIYA
# ============================================================

def play_priya():
    global start_threshold_for_barge, priya_audio_cache

    print("🔊 Voxera speaking...")

    try:
        audio = priya_audio_cache
        if audio is None or len(audio) == 0:
            raise RuntimeError("No generated Priya audio in memory.")

        clear_audio_queue()

        sd.play(audio, TTS_SAMPLE_RATE)

        interrupted = _wait_for_barge_in(
            audio,
            TTS_SAMPLE_RATE
        )

        if interrupted:
            sd.stop()
            print("🎤 Listening to the interrupted patient turn...")
            return "barge"

        sd.wait()
        print("✅ Voxera finished speaking.")
        clear_audio_queue()
        return True

    except Exception as e:
        try:
            sd.stop()
        except Exception:
            pass

        print()
        print("❌ PLAYBACK ERROR")
        print(e)
        return False


# ============================================================
# PROCESS PATIENT TURN
# ============================================================

def process_turn(
    patient_text
):
    """Safety first, then one fast healthcare-model call per turn."""

    emergency = emergency_override(patient_text)
    if emergency:
        action, response = emergency
        save_conversation(patient_text, response)
        return action, response

    # Optional semantic scope gate. Disabled by default because a
    # second LLM request adds avoidable phone-call latency.
    if USE_SCOPE_GATE:
        print("🔎 Checking healthcare scope...")
        scope, scope_time, success = classify_scope(patient_text)
        print(f"⏱️ Scope: {scope_time:.2f}s")
        print(f"📌 Scope: {scope}")
        print("✅ Classifier: AI decision" if success else "⚠️ Classifier: fallback")

        if scope != "HEALTHCARE":
            response = (
                "I'm here specifically to help with health-related "
                "concerns. If you have a health concern, tell me "
                "what you're experiencing and I'll help."
            )
            save_conversation(patient_text, response)
            return "out_of_scope", response

    print("✅ Processing patient request...")
    response = generate_healthcare_response(patient_text)
    save_conversation(patient_text, response)
    return "respond", response


# ============================================================
# OLLAMA WARMUP
# ============================================================

def warmup_ollama():
    """Load the active model before the first real patient turn."""
    print("🔥 Warming up Ollama model...")
    start = time.perf_counter()
    payload = {
        "model": HEALTHCARE_MODEL,
        "messages": [
            {"role": "user", "content": "Say OK."}
        ],
        "stream": False,
        "think": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "num_predict": 2,
            "num_ctx": 512
        }
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=30)
        r.raise_for_status()
        print(f"✅ Ollama ready in {time.perf_counter() - start:.2f}s")
    except Exception as e:
        print(f"⚠️ Ollama warmup failed: {e}")
        print("   The first real turn may take longer.")


# ============================================================
# MAIN
# ============================================================

def main():
    global start_threshold_for_barge, pending_barge_audio

    print(f"⚡ FAST MODE: scope gate={USE_SCOPE_GATE}, brain={HEALTHCARE_MODEL}, whisper={WHISPER_MODEL_NAME}")
    print(f"⚡ End-of-speech silence: {END_SILENCE_SECONDS:.2f}s")
    print()
    print("🎧 Starting microphone...")

    try:

        with sd.InputStream(

            samplerate=SAMPLE_RATE,

            channels=1,

            dtype="float32",

            blocksize=CHUNK_SIZE,

            device=MICROPHONE_DEVICE,

            callback=audio_callback

        ):

            # ------------------------------------------------
            # Initial calibration
            # ------------------------------------------------

            clear_audio_queue()

            (
                start_threshold,
                end_threshold
            ) = calibrate_microphone()

            start_threshold_for_barge = start_threshold

            clear_audio_queue()

            # ------------------------------------------------
            # READY
            # ------------------------------------------------

            print(
                "🎤 Microphone ready."
            )

            print()
            print(
                "🚀 VOXERA IS READY."
            )

            print(
                "=" * 65
            )

            print(
                "Speak naturally."
            )

            print(
                "No ENTER required."
            )

            print(
                "Normal pauses are allowed."
            )

            print(
                "Voxera will detect when you finish."
            )

            print(
                "Press CTRL+C to stop."
            )

            print(
                "=" * 65
            )

            print()

            # ------------------------------------------------
            # CONTINUOUS CONVERSATION
            # ------------------------------------------------

            while True:

                # --------------------------------------------
                # If the patient interrupted Priya, continue
                # recording that SAME turn.
                # --------------------------------------------
                if pending_barge_audio is not None:

                    first_chunks = pending_barge_audio
                    pending_barge_audio = None

                else:

                    # ----------------------------------------
                    # Flush stale audio.
                    # ----------------------------------------

                    clear_audio_queue()

                    # ----------------------------------------
                    # WAIT FOR PATIENT
                    # ----------------------------------------

                    first_chunks = wait_for_speech(
                        start_threshold
                    )

                # --------------------------------------------
                # RECORD
                # --------------------------------------------

                audio = record_turn(

                    first_chunks,

                    start_threshold,

                    end_threshold
                )

                # --------------------------------------------
                # TRANSCRIBE
                # --------------------------------------------

                text = transcribe(
                    audio
                )

                if not text:

                    print()
                    print(
                        "🔄 No reliable speech captured."
                    )

                    print(
                        "🎤 Listening again..."
                    )

                    continue

                # --------------------------------------------
                # PATIENT TEXT
                # --------------------------------------------

                print()
                print(
                    "-" * 65
                )

                print(
                    "PATIENT:"
                )

                print(
                    text
                )

                print(
                    "-" * 65
                )

                # --------------------------------------------
                # AI + TTS latency measurement
                # --------------------------------------------

                turn_start = time.perf_counter()

                try:

                    action, response = process_turn(
                        text
                    )

                except Exception as e:

                    print()
                    print(
                        "❌ VOXERA BRAIN ERROR"
                    )

                    print(e)

                    response = (
                        "I'm having trouble processing that "
                        "right now. Please tell me again what "
                        "you're experiencing."
                    )

                    action = "error"

                # --------------------------------------------
                # PATIENT-FACING RESPONSE
                # --------------------------------------------

                print()
                print(
                    "VOXERA:"
                )

                print(
                    response
                )

                print()
                print(
                    "ACTION:",
                    action
                )

                # --------------------------------------------
                # TTS
                # --------------------------------------------

                tts_ok = generate_priya(
                    response
                )

                if tts_ok:

                    play_priya()

                else:

                    print(
                        "⚠️ Priya failed."
                    )

                    print(
                        "   Continuing conversation."
                    )

                total_latency = time.perf_counter() - turn_start
                print(f"⏱️ Turn processing + TTS ready: {total_latency:.2f}s")

                print()
                print(
                    "🔄 Your turn..."
                )


    except KeyboardInterrupt:

        print()
        print()
        print(
            "🛑 VOXERA STOPPED."
        )

    except Exception as e:

        print()
        print()
        print(
            "❌ VOXERA ERROR"
        )

        print(
            repr(e)
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()