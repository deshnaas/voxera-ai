import json
import time
import requests


URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:4b"


def test(name, messages):

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": "10m",

        "options": {
            "temperature": 0.1,
            "num_ctx": 2048,
            "num_predict": 60,
        },
    }

    print()
    print("=" * 60)
    print(name)
    print("=" * 60)

    start = time.perf_counter()

    response = requests.post(
        URL,
        json=payload,
        timeout=60,
    )

    elapsed = time.perf_counter() - start

    response.raise_for_status()

    data = response.json()

    print()
    print("Response:")
    print(
        data.get("message", {}).get(
            "content",
            ""
        )
    )

    print()
    print("TIMING")
    print("-" * 40)

    total = data.get("total_duration")
    load = data.get("load_duration")
    prompt = data.get("prompt_eval_duration")
    generation = data.get("eval_duration")

    prompt_count = data.get(
        "prompt_eval_count"
    )

    eval_count = data.get(
        "eval_count"
    )

    print(
        f"HTTP elapsed:      {elapsed:.2f}s"
    )

    if total is not None:
        print(
            f"Total:             "
            f"{total / 1e9:.2f}s"
        )

    if load is not None:
        print(
            f"Load:              "
            f"{load / 1e9:.2f}s"
        )

    if prompt is not None:
        print(
            f"Prompt eval:       "
            f"{prompt / 1e9:.2f}s"
        )

    if generation is not None:
        print(
            f"Generation:        "
            f"{generation / 1e9:.2f}s"
        )

    if prompt_count is not None:
        print(
            f"Prompt tokens:     "
            f"{prompt_count}"
        )

    if eval_count is not None:
        print(
            f"Generated tokens:  "
            f"{eval_count}"
        )

    print()


# ============================================================
# TEST 1
# Tiny prompt
# ============================================================

test(
    "TEST 1 — TINY",
    [
        {
            "role": "system",
            "content": (
                "You are a helpful healthcare assistant. "
                "Reply directly to the patient in one sentence."
            ),
        },
        {
            "role": "user",
            "content": (
                "I have a blocked nose."
            ),
        },
    ],
)


# ============================================================
# TEST 2
# Medium prompt
# ============================================================

test(
    "TEST 2 — MEDIUM",
    [
        {
            "role": "system",
            "content": (
                "You are Voxera, a safe conversational healthcare "
                "assistant. Understand the patient's concern, assess "
                "urgency, avoid diagnosis, avoid prescribing, provide "
                "safe general guidance, ask useful follow-up questions, "
                "and speak directly to the patient. Keep responses "
                "concise and natural."
            ),
        },
        {
            "role": "user",
            "content": (
                "I have a blocked nose and a sore throat."
            ),
        },
    ],
)


# ============================================================
# TEST 3
# Current-style prompt
# ============================================================

test(
    "TEST 3 — WITH HISTORY",
    [
        {
            "role": "system",
            "content": """
You are Voxera, a conversational healthcare assistant speaking directly
to a patient on a phone call.

Understand the patient's meaning, classify the request, assess urgency,
and give a concise natural spoken response.

SCOPE:
HEALTHCARE = health, symptoms, illness, injury, first aid, medication,
prescriptions, reports, appointments, doctors, hospitals, mental health,
emergencies, prevention, or other health concerns.

OUT_OF_SCOPE = unrelated requests.

URGENCY:
NORMAL = no obvious immediate danger.
URGENT = prompt medical evaluation may be needed.
EMERGENCY = immediate danger, severe breathing difficulty, severe chest
symptoms, unconsciousness, severe bleeding, poisoning/overdose, or
immediate suicide/self-harm risk.

SAFETY:
Do not diagnose with certainty.
Do not invent symptoms or medical history.
Do not claim to examine the patient.
Do not prescribe medication or give prescription doses.
Do not tell patients to stop/change prescribed medication.
Give practical low-risk self-care when appropriate.
Ask a useful follow-up question when needed.
For emergencies, tell the patient to get immediate emergency medical help.

The response field is spoken directly to the patient.

NEVER put reasoning, analysis, planning, classification, summaries,
instructions, JSON, or internal thoughts in the response.

Keep responses natural, warm, and concise: normally 1-3 sentences.
""",
        },

        {
            "role": "user",
            "content": (
                "Earlier I told you that I had a fever. "
                "You asked how long it had been going on. "
                "I now want to tell you that my nose is blocked."
            ),
        },

        {
            "role": "assistant",
            "content": (
                "Okay, tell me about the blocked nose."
            ),
        },

        {
            "role": "user",
            "content": (
                "I have a blocked nose and a sore throat."
            ),
        },
    ],
)