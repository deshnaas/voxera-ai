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
# Whisper SMALL
#     ↓
# AI Healthcare Scope Gate — Qwen3 1.7B
#     ↓
# Healthcare Brain — Qwen3 4B
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
import threading
from collections import deque

import numpy as np
import sounddevice as sd
import soundfile as sf
import whisper
import requests

try:
    from pywebrtc_audio import AudioProcessor
except ImportError:
    AudioProcessor = None


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
MIN_SPEECH_SECONDS = 0.45

# Silence after speech before turn ends.
#
# Increased from 1.35 so natural pauses don't cut the user off.
END_SILENCE_SECONDS = 1.70

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

SCOPE_MODEL = "qwen3:1.7b"
HEALTHCARE_MODEL = "qwen3:1.7b"

SCOPE_TIMEOUT = 10
HEALTHCARE_TIMEOUT = 35

SCOPE_MAX_TOKENS = 12
HEALTHCARE_MAX_TOKENS = 80


# ============================================================
# TTS
# ============================================================

TTS_SPEED = 1.10
TTS_SAMPLE_RATE = 24000

# ============================================================
# REAL-TIME BARGE-IN / AEC
# ============================================================
# Barge-in uses a true duplex audio stream.  The exact audio frame
# sent to the speaker is simultaneously supplied to WebRTC AEC3 as
# the far-end reference.  This is important: AEC cannot work reliably
# from an unrelated copy of the TTS waveform after the fact.
BARGE_RATE = 16000
BARGE_FRAME_SAMPLES = 160          # 10 ms at 16 kHz
BARGE_MIN_SPEECH_SECONDS = 0.30
BARGE_SPEECH_PROBABILITY = 0.82
BARGE_MIN_CLEAN_RMS = 0.0030
BARGE_STREAM_DELAY_MS = 40
BARGE_PRE_ROLL_SECONDS = 0.40


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

# Barge-in audio captured by the AEC duplex stream.
pending_barge_audio = None

conversation = []

whisper_model = None

priya_model = None
priya_voice = None
goonj = None
torch = None

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

print("🧠 Loading Whisper SMALL...")
print("   This happens once.")
print()

try:

    whisper_model = whisper.load_model("small")

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

    # End-of-turn threshold must stay above the measured idle noise floor.
    # Otherwise a noisy mic can remain permanently "speaking" until the
    # 30-second safety cap.
    end_threshold = max(
        noise_floor * 1.60,
        start_threshold * 0.55,
        MIN_END_THRESHOLD,
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

    # --------------------------------------------------------
    # Save debug WAV
    # --------------------------------------------------------

    try:

        sf.write(
            DEBUG_AUDIO,
            audio,
            SAMPLE_RATE
        )

        print(
            f"💾 Debug audio: "
            f"{DEBUG_AUDIO}"
        )

    except Exception as e:

        print(
            f"⚠️ Debug audio error: {e}"
        )

    print(
        "🧠 Transcribing..."
    )

    start = time.perf_counter()

    try:

        result = whisper_model.transcribe(

            audio,

            language="en",

            fp16=False,

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

    # A model returning only its own name is not a patient response.
    if lower in {"voxera", "assistant", "response", "answer"}:
        return False

    # Patient-facing speech should contain more than a bare token.
    if len(text.split()) < 3:
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

    recent = conversation[-4:]

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

    messages = build_healthcare_messages(
        patient_text
    )

    payload = {

        "model": HEALTHCARE_MODEL,

        "messages": messages,

        "stream": False,

        # VERY IMPORTANT FOR QWEN3
        "think": False,

        "format": {

            "type": "object",

            "properties": {

                "response": {

                    "type": "string"
                }

            },

            "required": [
                "response"
            ]

        },

        "options": {

            "temperature": 0.10,

            "top_p": 0.80,

            "num_predict": HEALTHCARE_MAX_TOKENS,

            "num_ctx": 2048,

            "repeat_penalty": 1.08
        }
    }

    print(
        "🧠 Voxera healthcare brain..."
    )

    start = time.perf_counter()

    try:

        response = requests.post(

            OLLAMA_URL,

            json=payload,

            timeout=HEALTHCARE_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        message = data.get(
            "message",
            {}
        )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Qwen3 can expose internal thinking separately.
        #
        # We ONLY read message["content"].
        #
        # We NEVER read message["thinking"].
        # ----------------------------------------------------

        raw = message.get(
            "content",
            ""
        )

        elapsed = (
            time.perf_counter()
            - start
        )

        print(
            f"AI: {elapsed:.2f}s"
        )

        cleaned = extract_response_json(
            raw
        )

        if is_patient_facing_response(
            cleaned
        ):

            return cleaned

        # ----------------------------------------------------
        # If JSON extraction failed, make ONE repair request.
        #
        # This repair request receives ONLY the patient's
        # current message. It does NOT receive the bad output.
        # Therefore the hallucinated reasoning cannot propagate.
        # ----------------------------------------------------

        print(
            "⚠️ Invalid patient-facing AI output."
        )

        print(
            "🔁 Running clean response retry..."
        )

        retry_payload = {

            "model": HEALTHCARE_MODEL,

            "messages": [

                {
                    "role": "system",

                    "content": """
You are Voxera, a healthcare voice assistant.

Respond directly to the patient.

Return ONLY valid JSON:

{"response":"..."}

The response value must contain ONLY the words Voxera
should speak aloud.

Do not explain your reasoning.
Do not summarize the task.
Do not repeat the patient's message.
Do not mention instructions.
Do not mention being an AI.
Do not output analysis.
Do not output markdown.

Give useful, medically cautious guidance.
Ask a useful question when needed.
If the patient needs urgent help, clearly say so.

Keep the spoken response concise.
"""
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

                    "response": {

                        "type": "string"
                    }

                },

                "required": [
                    "response"
                ]

            },

            "options": {

                "temperature": 0.05,

                "top_p": 0.75,

                "num_predict": 110,

                "num_ctx": 2048,

                "repeat_penalty": 1.08
            }
        }

        retry_start = time.perf_counter()

        retry = requests.post(

            OLLAMA_URL,

            json=retry_payload,

            timeout=HEALTHCARE_TIMEOUT
        )

        retry.raise_for_status()

        retry_data = retry.json()

        retry_message = retry_data.get(
            "message",
            {}
        )

        retry_raw = retry_message.get(
            "content",
            ""
        )

        retry_elapsed = (
            time.perf_counter()
            - retry_start
        )

        print(
            f"Retry AI: "
            f"{retry_elapsed:.2f}s"
        )

        retry_clean = extract_response_json(
            retry_raw
        )

        if is_patient_facing_response(
            retry_clean
        ):

            return retry_clean

        # ----------------------------------------------------
        # Final safe fallback.
        # ----------------------------------------------------

        print(
            "❌ AI failed patient-facing validation."
        )

        # ----------------------------------------------------
        # Deterministic final fallback.
        # NEVER speak a malformed/empty model result.
        # ----------------------------------------------------
        emergency_fallback = emergency_override(patient_text)

        if emergency_fallback:
            return emergency_fallback[1]

        return (
            "I want to make sure I give you safe advice. "
            "Please tell me what you are experiencing and I will help you."
        )

    except Exception as e:

        print()
        print(
            "❌ HEALTHCARE BRAIN ERROR"
        )

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

    lower = re.sub(r"\s+", " ", text.lower()).strip()

    # --------------------------------------------------------
    # CRITICAL physical emergency detection.
    # This runs BEFORE scope classification and BEFORE the LLM.
    # It is deliberately conservative and deterministic.
    # --------------------------------------------------------
    critical_patterns = [
        r"(?:very\s+|extremely\s+|severe(?:ly)?\s+|crushing\s+|terrible\s+|unbearable\s+).*chest\s+pain",
        r"chest\s+pain.*(?:very\s+|extremely\s+|severe(?:ly)?\s+|crushing\s+|terrible\s+|unbearable\s+)",
        r"chest\s+pain.*(?:spreading|radiat(?:e|ing|es)?)",
        r"(?:pain|hurting|ache).*left\s+(?:arm|hand)",
        r"left\s+(?:arm|hand).*?(?:pain|hurting|ache)",
        r"(?:can't|cannot|unable|hard|trouble|difficulty|struggling)\s+(?:to\s+)?breathe",
        r"(?:suffocating|not getting enough air|gasping for air)",
        r"(?:unconscious|passed out|not conscious|fainted and not responding)",
        r"(?:severe|heavy|uncontrolled)\s+bleeding",
        r"(?:vomiting|throwing up|coughing)\s+(?:a\s+)?lot\s+of\s+blood",
        r"(?:vomiting|throwing up|coughing)\s+blood",
        r"seizure",
    ]

    for pattern in critical_patterns:
        if re.search(pattern, lower):
            return (
                "urgent",
                "This may be a medical emergency. Please call 112 now, "
                "or have someone nearby call for you. Do not drive yourself. "
                "If you can, have someone stay with you while you get emergency help."
            )

    # --------------------------------------------------------
    # Very high fever / serious systemic symptoms.
    # Keep this deterministic so a malformed LLM response can
    # never turn a potentially serious presentation into nonsense.
    # --------------------------------------------------------
    fever_match = re.search(
        r"(?:fever|temperature)\D{0,20}(10[4-9](?:\.\d+)?|11\d(?:\.\d+)?)",
        lower
    )

    severe_fever_context = any(
        phrase in lower
        for phrase in [
            "could not walk",
            "can't walk",
            "cannot walk",
            "unable to walk",
            "very weak",
            "extremely weak",
            "confused",
            "fainting",
            "severe headache",
            "very bad headache",
        ]
    )

    if fever_match and severe_fever_context:
        return (
            "urgent",
            "A fever of 104 degrees with these symptoms needs urgent medical evaluation. "
            "Please have someone take you to an emergency department now. "
            "If you cannot travel safely or you are getting worse, call 112 for emergency help."
        )

    # --------------------------------------------------------
    # Explicit self-harm / suicidal language.
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
                "I'm really glad you told me. Please don't stay alone right now. "
                "Move away from anything you could use to hurt yourself and have a trusted person stay with you. "
                "If you may hurt yourself now, call 112 or go to the nearest emergency department."
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

    if not text:

        return False

    print()
    print(
        "🔊 Generating Priya voice..."
    )

    # --------------------------------------------------------
    # TTS protection.
    # --------------------------------------------------------

    words = text.split()

    if len(words) > 110:

        print(
            "⚠️ Response too long for TTS."
        )

        return False

    start = time.perf_counter()

    try:

        # ----------------------------------------------------
        # Phonemize
        # ----------------------------------------------------

        phonemes = goonj.phonemize(
            text,
            "en"
        )

        if not phonemes:

            raise RuntimeError(
                "Phonemizer returned empty phonemes."
            )

        phoneme_count = len(
            phonemes
        )

        print(
            f"   Phonemes: {phoneme_count}"
        )

        # ----------------------------------------------------
        # Kokoro max phoneme length is around 510.
        #
        # Do NOT let an oversized response crash the model.
        # ----------------------------------------------------

        MAX_PHONEMES = 509

        if phoneme_count > MAX_PHONEMES:

            print(
                "⚠️ Response exceeds Kokoro phoneme limit."
            )

            # This should almost never happen because the AI
            # response is already length-limited.
            phonemes = phonemes[
                :MAX_PHONEMES
            ]

            phoneme_count = len(
                phonemes
            )

        # ----------------------------------------------------
        # CRITICAL:
        #
        # Priya pack:
        #
        #     (510, 1, 256)
        #
        # Select:
        #
        #     pack[len(phonemes)-1]
        #
        # This gives:
        #
        #     (1,256)
        # ----------------------------------------------------

        ref_s = get_priya_embedding(
            phoneme_count
        )

        ref_s = ref_s.to(
            TTS_DEVICE
        )

        # ----------------------------------------------------
        # Generate
        # ----------------------------------------------------

        with torch.inference_mode():

            audio = priya_model(

                phonemes,

                ref_s,

                speed=TTS_SPEED
            )

        if audio is None:

            raise RuntimeError(
                "Kokoro returned no audio."
            )

        if isinstance(
            audio,
            torch.Tensor
        ):

            audio_numpy = (
                audio
                .detach()
                .cpu()
                .numpy()
            )

        else:

            audio_numpy = np.asarray(
                audio,
                dtype=np.float32
            )

        audio_numpy = np.asarray(
            audio_numpy,
            dtype=np.float32
        ).squeeze()

        if audio_numpy.ndim != 1:

            audio_numpy = (
                audio_numpy
                .reshape(-1)
            )

        if len(audio_numpy) == 0:

            raise RuntimeError(
                "Kokoro generated empty audio."
            )

        # ----------------------------------------------------
        # Remove NaN / infinity.
        # ----------------------------------------------------

        if not np.isfinite(
            audio_numpy
        ).all():

            raise RuntimeError(
                "Kokoro generated invalid audio."
            )

        # ----------------------------------------------------
        # Write WAV
        # ----------------------------------------------------

        sf.write(
            TTS_OUTPUT,
            audio_numpy,
            TTS_SAMPLE_RATE
        )

        elapsed = (
            time.perf_counter()
            - start
        )

        duration = (
            len(audio_numpy)
            / TTS_SAMPLE_RATE
        )

        print(
            f"[goonj] wrote: "
            f"{TTS_OUTPUT}"
        )

        print(
            f"[goonj] duration: "
            f"{duration:.2f}s"
        )

        print(
            f"TTS generation: "
            f"{elapsed:.2f}s"
        )

        return True

    except Exception as e:

        print()
        print(
            "❌ PRIYA TTS ERROR"
        )

        print(
            repr(e)
        )

        return False


# ============================================================
# PLAY PRIYA
# ============================================================

def _resample_audio(audio, src_rate, dst_rate):
    """Small dependency-free mono resampler for the TTS reference."""
    audio = np.asarray(audio, dtype=np.float32).flatten()
    if src_rate == dst_rate or len(audio) == 0:
        return audio
    new_len = max(1, int(round(len(audio) * dst_rate / src_rate)))
    old_x = np.arange(len(audio), dtype=np.float64)
    new_x = np.linspace(0, len(audio) - 1, new_len, dtype=np.float64)
    return np.interp(new_x, old_x, audio).astype(np.float32)


def _rms_array(x):
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x * x) + 1e-12))


def play_priya():
    """
    Play Priya in a real full-duplex stream and detect true barge-in.

    The old implementation called sd.play() and then tried to reconstruct
    the speaker reference while the microphone was already running.  That
    is not synchronized closely enough for AEC3.  Here the output callback
    creates the exact 10-ms far-end frame that is both played and passed to
    AEC3, while the same callback receives the near-end microphone frame.
    """
    global pending_barge_audio

    print("🔊 Voxera speaking...")
    pending_barge_audio = None

    if AudioProcessor is None:
        print("⚠️ pywebrtc-audio is not installed; using normal playback.")
        try:
            audio, sample_rate = sf.read(TTS_OUTPUT, dtype="float32")
            if audio.ndim > 1:
                audio = np.mean(audio, axis=1)
            sd.play(audio, sample_rate)
            sd.wait()
            print("✅ Voxera finished speaking.")
            return "finished"
        except Exception as e:
            print("❌ PLAYBACK ERROR")
            print(e)
            return "error"

    try:
        audio, sample_rate = sf.read(TTS_OUTPUT, dtype="float32")
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)

        # AEC3 supports 16 kHz.  The resampled signal is ALSO the signal
        # actually sent to the speaker, so the far-end reference is exact.
        far_audio = _resample_audio(audio, sample_rate, BARGE_RATE)
        total_samples = len(far_audio)

        processor = AudioProcessor(
            sample_rate=BARGE_RATE,
            num_channels=1,
            echo_cancellation=True,
            noise_suppression=True,
            auto_gain_control=False,
            ns_level=2,
            stream_delay_ms=BARGE_STREAM_DELAY_MS,
        )

        stop_event = threading.Event()
        finished_event = threading.Event()
        captured = []
        recent = deque(maxlen=max(1, int(BARGE_PRE_ROLL_SECONDS * 100)))
        speech_time = 0.0
        playback_pos = 0
        lock = threading.Lock()
        state = {"interrupted": False, "error": None}

        def callback(indata, outdata, frames, time_info, status):
            nonlocal playback_pos, speech_time

            if status:
                # Don't spam the console for routine callback status flags.
                pass

            # Exact far-end frame for this output callback.
            end = min(playback_pos + frames, total_samples)
            far = np.zeros(frames, dtype=np.float32)
            if playback_pos < total_samples:
                n = end - playback_pos
                far[:n] = far_audio[playback_pos:end]

            # Copy the microphone frame before returning from the callback.
            near = np.asarray(indata[:, 0], dtype=np.float32).copy()
            clean = processor.process(near, far)
            prob = float(processor.speech_probability)
            clean_rms = _rms_array(clean)

            recent.append(clean.copy())

            # We deliberately require BOTH strong spectral speech evidence
            # and meaningful cleaned energy, sustained for 300 ms.  This is
            # much harder for speaker leakage/room noise to trigger.
            speech = (
                prob >= BARGE_SPEECH_PROBABILITY
                and clean_rms >= BARGE_MIN_CLEAN_RMS
            )

            if speech:
                speech_time += frames / BARGE_RATE
            else:
                speech_time = max(0.0, speech_time - 0.04)

            if speech_time >= BARGE_MIN_SPEECH_SECONDS and not state["interrupted"]:
                state["interrupted"] = True
                with lock:
                    captured.clear()
                    captured.extend(list(recent))
                stop_event.set()

            # The same far frame is physically played here.
            outdata[:, 0] = far
            playback_pos += frames

            if playback_pos >= total_samples:
                finished_event.set()

        # A true duplex stream: microphone and speaker run together at the
        # same sample rate and frame size, which is the structure AEC3 needs.
        with sd.Stream(
            samplerate=BARGE_RATE,
            blocksize=BARGE_FRAME_SAMPLES,
            device=(MICROPHONE_DEVICE, None),
            channels=1,
            dtype="float32",
            callback=callback,
        ) as stream:
            while not finished_event.is_set() and not stop_event.is_set():
                time.sleep(0.01)

            if stop_event.is_set():
                stream.abort()
            else:
                stream.stop()

        if state["interrupted"]:
            with lock:
                pending_barge_audio = list(captured)
            print()
            print("🛑 BARGE-IN detected — Voxera stopped speaking.")
            print("🎤 Listening to the interrupted patient turn...")
            return "barge_in"

        print("✅ Voxera finished speaking.")
        return "finished"

    except Exception as e:
        print()
        print("❌ PLAYBACK ERROR")
        print(e)
        return "error"


# ============================================================
# PROCESS PATIENT TURN
# ============================================================

def process_turn(
    patient_text
):

    # --------------------------------------------------------
    # SAFETY OVERRIDE
    # --------------------------------------------------------

    emergency = emergency_override(
        patient_text
    )

    if emergency:

        action, response = emergency

        save_conversation(
            patient_text,
            response
        )

        return (
            action,
            response
        )

    # --------------------------------------------------------
    # SCOPE
    # --------------------------------------------------------

    print(
        "🔎 Checking healthcare scope..."
    )

    scope, scope_time, success = classify_scope(
        patient_text
    )

    print(
        f"⏱️ Scope: "
        f"{scope_time:.2f}s"
    )

    print(
        f"📌 Scope: "
        f"{scope}"
    )

    if success:

        print(
            "✅ Classifier: AI decision"
        )

    else:

        print(
            "⚠️ Classifier: fallback"
        )

    # --------------------------------------------------------
    # OUT OF SCOPE
    # --------------------------------------------------------

    if scope != "HEALTHCARE":

        response = (
            "I'm here specifically to help with health-related "
            "concerns. If you have a health concern, tell me "
            "what you're experiencing and I'll help."
        )

        save_conversation(
            patient_text,
            response
        )

        return (
            "out_of_scope",
            response
        )

    print(
        "✅ Healthcare request."
    )

    # --------------------------------------------------------
    # HEALTHCARE BRAIN
    # --------------------------------------------------------

    response = generate_healthcare_response(
        patient_text
    )

    save_conversation(
        patient_text,
        response
    )

    return (
        "respond",
        response
    )


# ============================================================
# MAIN
# ============================================================

def main():
    global pending_barge_audio

    print("🎧 Starting microphone...")

    try:
        # --------------------------------------------------------
        # CALIBRATE ONCE USING THE ORIGINAL INPUT STREAM
        # --------------------------------------------------------
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=CHUNK_SIZE,
            device=MICROPHONE_DEVICE,
            callback=audio_callback,
        ):
            clear_audio_queue()
            start_threshold, end_threshold = calibrate_microphone()
            clear_audio_queue()

        print("🎤 Microphone ready.")
        print()
        print("🚀 VOXERA IS READY.")
        print("=" * 65)
        print("Speak naturally.")
        print("No ENTER required.")
        print("Normal pauses are allowed.")
        print("Voxera will detect when you finish.")
        print("Press CTRL+C to stop.")
        print("=" * 65)
        print()

        while True:
            # ====================================================
            # LISTEN / RECORD
            # ====================================================
            # The ordinary VAD remains the original baseline.  We only
            # open the mic stream while the patient is being captured.
            # During Priya playback, play_priya() owns the duplex stream.
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=CHUNK_SIZE,
                device=MICROPHONE_DEVICE,
                callback=audio_callback,
            ):
                clear_audio_queue()

                if pending_barge_audio is not None:
                    first_chunks = pending_barge_audio
                    pending_barge_audio = None
                    print("🎤 Continuing your interrupted turn...")
                else:
                    first_chunks = wait_for_speech(start_threshold)

                audio = record_turn(
                    first_chunks,
                    start_threshold,
                    end_threshold,
                )

            # ====================================================
            # TRANSCRIBE
            # ====================================================
            text = transcribe(audio)

            if not text:
                print()
                print("🔄 No reliable speech captured.")
                print("🎤 Listening again...")
                continue

            print()
            print("-" * 65)
            print("PATIENT:")
            print(text)
            print("-" * 65)

            # ====================================================
            # AI
            # ====================================================
            try:
                action, response = process_turn(text)
            except Exception as e:
                print()
                print("❌ VOXERA BRAIN ERROR")
                print(e)
                response = (
                    "I'm having trouble processing that right now. "
                    "Please tell me again what you're experiencing."
                )
                action = "error"

            print()
            print("VOXERA:")
            print(response)
            print()
            print("ACTION:", action)

            # ====================================================
            # TTS + TRUE FULL-DUPLEX BARGE-IN
            # ====================================================
            tts_ok = generate_priya(response)

            if tts_ok:
                play_priya()
            else:
                print("⚠️ Priya failed.")
                print("   Continuing conversation.")

            print()
            print("🔄 Your turn...")

    except KeyboardInterrupt:
        print()
        print()
        print("🛑 VOXERA STOPPED.")

    except Exception as e:
        print()
        print()
        print("❌ VOXERA ERROR")
        print(repr(e))


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()