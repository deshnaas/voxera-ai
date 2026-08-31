import requests
import json
import re


LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"


UNDERSTANDING_PROMPT = """
You are Voxera's fast medical conversation understanding module.

Extract only information explicitly stated by the patient.

Do NOT diagnose.
Do NOT explain anything.
Do NOT ask questions.

Return ONLY compact JSON.

Schema:
{
  "symptoms": [],
  "duration": null,
  "onset": null,
  "severity": null,
  "associated_symptoms": [],
  "triggers": [],
  "patient_feeling": null,
  "topic": "SYMPTOM | REPORT | PRESCRIPTION | MEDICAL_QUESTION | GENERAL"
}

Rules:
- Never invent information.
- Use null when unknown.
- Keep values extremely short.
- Understand natural English, Hindi and Hinglish.
- Resolve "it", "that", "this" using the supplied context.
- If the patient reports a symptom, use SYMPTOM.
- If discussing a medical/lab report, use REPORT.
- If discussing medicines or prescriptions, use PRESCRIPTION.
- If asking a general medical question, use MEDICAL_QUESTION.
- Otherwise use GENERAL.
"""


def _extract_json(text):

    if not text:
        return None

    text = text.strip()

    # Remove markdown fences if the model adds them.
    text = re.sub(
        r"```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"```\s*",
        "",
        text
    )

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        return None

    try:
        return json.loads(
            text[start:end + 1]
        )

    except json.JSONDecodeError:
        return None


def _fallback(message):

    return {
        "symptoms": [],
        "duration": None,
        "onset": None,
        "severity": None,
        "associated_symptoms": [],
        "triggers": [],
        "patient_feeling": message,
        "topic": "GENERAL"
    }


def understand(
    message,
    conversation_context=""
):

    prompt = f"""
PATIENT:
{message}

CONTEXT:
{conversation_context}

JSON:
"""

    payload = {
        "messages": [
            {
                "role": "system",
                "content": UNDERSTANDING_PROMPT
            },
            {
                "role": "user",
                "content": prompt
            }
        ],

        "temperature": 0.0,

        # Understanding only needs a tiny JSON response.
        "max_tokens": 80,

        "stream": False
    }

    try:

        response = requests.post(
            LLAMA_URL,
            json=payload,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        content = (
            data["choices"][0]["message"]["content"]
        )

        result = _extract_json(content)

        if result is None:

            print(
                "[Understanding] Invalid JSON."
            )

            return _fallback(message)

        # Ensure schema is always complete.

        result.setdefault(
            "symptoms",
            []
        )

        result.setdefault(
            "duration",
            None
        )

        result.setdefault(
            "onset",
            None
        )

        result.setdefault(
            "severity",
            None
        )

        result.setdefault(
            "associated_symptoms",
            []
        )

        result.setdefault(
            "triggers",
            []
        )

        result.setdefault(
            "patient_feeling",
            None
        )

        result.setdefault(
            "topic",
            "GENERAL"
        )

        return result

    except Exception as e:

        print(
            f"[Understanding error: {e}]"
        )

        return _fallback(message)