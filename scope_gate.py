# ============================================================
# VOXERA — AI HEALTHCARE SCOPE CLASSIFIER
# ============================================================
#
# Qwen3:1.7b  -> FAST scope classification
# Qwen3:4b    -> Actual healthcare reasoning
#
# NO keyword lists.
# NO hardcoded healthcare topics.
# NO semantic-example matching.
#
# The AI decides whether the patient's message is
# healthcare-related.
# ============================================================

import requests
import json
import re
import time


OLLAMA_URL = "http://localhost:11434/api/chat"

SCOPE_MODEL = "qwen3:1.7b"


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are Voxera's healthcare scope classifier.

Your ONLY job is to decide whether a patient's message is
related to a health, medical, physical, mental, injury,
symptom, medication, emergency, healthcare, or caregiving
concern.

You are NOT the healthcare assistant.
You must NOT answer the patient's question.

Classify based on the MEANING and INTENT of the patient's
message, not individual keywords.

Examples of healthcare-related intent include:
- describing symptoms
- asking about something happening to their body
- asking about an injury
- asking about medicine
- asking whether a symptom is serious
- asking what to do about a health concern
- describing an emergency
- asking about another person's health
- asking about a child, parent, friend, or other person's
  medical problem

A message does NOT have to contain medical terminology
to be healthcare-related.

For example:
"I'm feeling really strange today"
can be healthcare-related depending on context.

Clearly unrelated requests such as recipes, coding,
entertainment, schoolwork, shopping, or general trivia
are outside the healthcare scope.

IMPORTANT:
Return ONLY valid JSON.
Do not explain your reasoning.
Do not use markdown.
Do not use <think> tags.

Required format:

{"scope":"HEALTHCARE"}

or

{"scope":"OUT_OF_SCOPE"}
"""


# ============================================================
# REMOVE QWEN THINKING
# ============================================================

def clean_output(text):

    if not text:
        return ""

    # Remove Qwen's thinking block if present.
    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    return text.strip()


# ============================================================
# EXTRACT CLASSIFICATION
# ============================================================

def parse_scope(text):

    text = clean_output(text)

    # Look for JSON object.
    match = re.search(
        r'\{\s*"scope"\s*:\s*"(HEALTHCARE|OUT_OF_SCOPE)"\s*\}',
        text,
        flags=re.IGNORECASE
    )

    if match:
        scope = match.group(1).upper()

        return scope

    # Fallback: if model ignored JSON but clearly returned
    # one of the two allowed labels.
    upper = text.upper()

    if "HEALTHCARE" in upper and "OUT_OF_SCOPE" not in upper:
        return "HEALTHCARE"

    if "OUT_OF_SCOPE" in upper:
        return "OUT_OF_SCOPE"

    return None


# ============================================================
# AI SCOPE CLASSIFICATION
# ============================================================

def scope_check(patient_text):

    patient_text = patient_text.strip()

    if not patient_text:

        return {
            "scope": "OUT_OF_SCOPE",
            "success": False,
            "time": 0
        }

    payload = {
        "model": SCOPE_MODEL,

        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": patient_text
            }
        ],

        "stream": False,

        "think": False,

        "options": {
            "temperature": 0,
            "num_predict": 20
        },

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
        }
    }

    start = time.perf_counter()

    try:

        response = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        raw = data.get("message", {}).get(
            "content",
            ""
        )

        scope = parse_scope(raw)

        elapsed = time.perf_counter() - start

        if scope is None:

            print()
            print("⚠️ Scope classifier returned invalid output.")
            print("RAW:", repr(raw))

            return {
                "scope": "OUT_OF_SCOPE",
                "success": False,
                "time": elapsed
            }

        return {
            "scope": scope,
            "success": True,
            "time": elapsed
        }

    except Exception as e:

        elapsed = time.perf_counter() - start

        print()
        print("❌ Scope classifier error:")
        print(e)

        # Fail CLOSED.
        #
        # If the classifier fails, do not send an unknown
        # request into the healthcare brain.
        return {
            "scope": "OUT_OF_SCOPE",
            "success": False,
            "time": elapsed
        }


# ============================================================
# TEST MODE
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("VOXERA — AI HEALTHCARE SCOPE CLASSIFIER")
    print("=" * 60)
    print()
    print("🤖 Model:", SCOPE_MODEL)
    print("🧠 AI semantic classification")
    print("🚫 No keyword rules")
    print("🚫 No hardcoded topic lists")
    print()
    print("Type 'exit' to stop.")
    print()

    while True:

        patient = input("PATIENT: ").strip()

        if patient.lower() == "exit":
            break

        print()
        print("🧠 AI checking scope...")

        result = scope_check(patient)

        print(
            f"⏱️ Scope classification: "
            f"{result['time']:.2f}s"
        )

        print(
            "📌 SCOPE:",
            result["scope"]
        )

        print(
            "✅ Classifier:",
            "AI decision"
            if result["success"]
            else "Fallback"
        )

        print("-" * 60)