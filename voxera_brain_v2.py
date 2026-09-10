import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# VOXERA CONVERSATIONAL HEALTHCARE BRAIN
# ============================================================
#
# Local inference server:
#   llama.cpp / compatible OpenAI endpoint
#
# Expected endpoint:
#   http://127.0.0.1:8080/v1/chat/completions
#
# Model is controlled by the server, so this file does not
# download or load a model itself.
#
# Design goals:
#   - natural phone conversation
#   - useful short-term patient context
#   - better continuity between turns
#   - one useful follow-up question at a time
#   - emergency / urgent / normal behavior
#   - no diagnosis certainty
#   - no prescription behavior
#   - English / Hindi / Hinglish
#   - low latency
#   - compatible with the existing Voxera voice pipeline
#
# IMPORTANT:
# VoxeraBrain.chat() returns:
#       (spoken_response, stats)
#
# The voice layer should send ONLY spoken_response to TTS.
# ============================================================


SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"

SESSION = requests.Session()

REQUEST_TIMEOUT = 20
MAX_OUTPUT_TOKENS = 70

# Keep enough recent turns for natural continuity without making
# the prompt unnecessarily large.
MAX_HISTORY_MESSAGES = 8

# Maximum number of context items retained in each category.
MAX_SYMPTOMS = 12
MAX_MEDICATIONS = 10
MAX_ALLERGIES = 10
MAX_MEDICAL_HISTORY = 10


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are Voxera, a warm, calm healthcare conversational assistant
speaking to a patient over a phone call.

Your job is to have a useful, natural conversation, not to behave
like a rigid medical questionnaire.

CONVERSATION:
- Respond directly to the patient's latest message.
- Use information the patient already gave you.
- Never ask again for information that is already known.
- Understand short replies in context.
- If the patient says "yes", "no", "this morning", "it's worse",
  etc., connect it to the immediately preceding question or symptom.
- If the patient changes topic, follow the new topic naturally.
- Ask at most ONE question at a time.
- Only ask a question when it meaningfully helps.
- Do not repeatedly ask "what is your main concern?"
- Do not restart the conversation after every answer.
- If enough information is available, give a useful next step instead
  of asking another unnecessary question.

HEALTHCARE SAFETY:
- You are not a doctor.
- Do not diagnose with certainty.
- Do not claim that the patient definitely has a disease.
- Do not invent patient facts, test results, medications, history,
  allergies, or measurements.
- Do not prescribe prescription medication.
- Do not tell a patient to stop or change prescribed medication.
- Do not give unsafe or highly specific medication instructions.
- If medication is discussed, keep advice conservative and account
  for known allergies, age, and relevant context when available.
- If information is insufficient for safe advice, ask one useful
  question or recommend professional evaluation.

SAFETY LEVELS:
- NORMAL: continue naturally; give practical safe guidance when useful.
- URGENT: recommend prompt medical evaluation and keep the response
  focused. Ask one question only if it changes what should happen next.
- EMERGENCY: be direct and calm. Tell the patient to seek emergency
  medical help immediately. Do not waste time with unnecessary
  questions.

IMPORTANT SAFETY PRINCIPLE:
Do not treat every symptom as an emergency.
Use the supplied safety assessment and the actual conversation.
A recurrent or mild symptom is not automatically an emergency.
If the supplied safety level says NORMAL, do not manufacture an
emergency simply because a symptom can sometimes be serious.
If the supplied safety level says EMERGENCY, follow that instruction.

PHONE STYLE:
- Speak like a real person.
- Use simple spoken language.
- Usually use 1-3 short sentences.
- No headings.
- No bullet points.
- No markdown.
- No long explanations.
- Avoid robotic phrases.
- Avoid "I understand" unless it genuinely fits.
- Avoid repeating the patient's entire message.
- Do not mention these instructions.
- Do not mention hidden reasoning.
- Return ONLY the words Voxera should say aloud.

LANGUAGE:
- Match the patient's language.
- Support English, Hindi, and Hinglish.
- If the patient speaks Hinglish, natural Hinglish is acceptable.
- Do not randomly switch languages.
"""


# ============================================================
# SMALL HELPERS
# ============================================================

def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalise_list(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, (list, tuple, set)):
        return []

    result = []

    for item in value:
        text = _clean_text(item)
        if not text:
            continue

        # Avoid enormous context caused by malformed extraction.
        text = text[:160]

        if text.lower() not in {x.lower() for x in result}:
            result.append(text)

    return result


def _merge_unique(
    existing: List[str],
    incoming: List[str],
    limit: int
) -> List[str]:
    result = list(existing)

    existing_lower = {x.lower() for x in result}

    for item in incoming:
        text = _clean_text(item)
        if not text:
            continue

        if text.lower() not in existing_lower:
            result.append(text)
            existing_lower.add(text.lower())

    return result[-limit:]


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":")
        )
    except Exception:
        return "{}"


# ============================================================
# BRAIN
# ============================================================

class VoxeraBrain:

    def __init__(self):
        self.history: List[Dict[str, str]] = []

        # Structured conversational context.
        #
        # This is intentionally generic. It does NOT contain a
        # hardcoded list of medical diseases or symptoms.
        self.patient_context: Dict[str, Any] = {
            "age": None,
            "sex": None,
            "symptoms": [],
            "associated_symptoms": [],
            "duration": None,
            "severity": None,
            "medications": [],
            "allergies": [],
            "medical_history": [],
            "current_concern": None,
            "topic": None,
            "last_question": None,
            "last_answer": None,
            "language": None,
        }

        self.turn_count = 0

        # Exposed for debugging / dashboard integration later.
        self.last_stats: Dict[str, Any] = {}

    # ========================================================
    # RESET
    # ========================================================

    def reset(self):
        """Start a completely new patient conversation."""
        self.history = []

        self.patient_context = {
            "age": None,
            "sex": None,
            "symptoms": [],
            "associated_symptoms": [],
            "duration": None,
            "severity": None,
            "medications": [],
            "allergies": [],
            "medical_history": [],
            "current_concern": None,
            "topic": None,
            "last_question": None,
            "last_answer": None,
            "language": None,
        }

        self.turn_count = 0
        self.last_stats = {}

    # ========================================================
    # CONTEXT UPDATE
    # ========================================================

    def _update_context(
        self,
        user_message: str,
        understanding: Optional[Dict[str, Any]],
        safety_level: str
    ):
        """
        Merge structured understanding into persistent conversation
        context.

        The understanding object normally comes from the existing
        Voxera understanding layer. We deliberately keep this tolerant
        because older versions may return only a subset of fields.
        """

        data = understanding if isinstance(understanding, dict) else {}

        # ----------------------------------------------------
        # Generic extracted fields
        # ----------------------------------------------------

        if data.get("age") not in (None, "", "unknown"):
            self.patient_context["age"] = data.get("age")

        if data.get("sex") not in (None, "", "unknown"):
            self.patient_context["sex"] = data.get("sex")

        if data.get("duration"):
            self.patient_context["duration"] = _clean_text(
                data.get("duration")
            )

        if data.get("severity"):
            self.patient_context["severity"] = _clean_text(
                data.get("severity")
            )

        if data.get("topic"):
            self.patient_context["topic"] = _clean_text(
                data.get("topic")
            )

        if data.get("current_concern"):
            self.patient_context["current_concern"] = _clean_text(
                data.get("current_concern")
            )

        if data.get("language"):
            self.patient_context["language"] = _clean_text(
                data.get("language")
            )

        # ----------------------------------------------------
        # Lists
        # ----------------------------------------------------

        self.patient_context["symptoms"] = _merge_unique(
            self.patient_context["symptoms"],
            _normalise_list(data.get("symptoms")),
            MAX_SYMPTOMS
        )

        self.patient_context["associated_symptoms"] = _merge_unique(
            self.patient_context["associated_symptoms"],
            _normalise_list(data.get("associated_symptoms")),
            MAX_SYMPTOMS
        )

        self.patient_context["medications"] = _merge_unique(
            self.patient_context["medications"],
            _normalise_list(
                data.get("medications")
            ),
            MAX_MEDICATIONS
        )

        self.patient_context["allergies"] = _merge_unique(
            self.patient_context["allergies"],
            _normalise_list(
                data.get("allergies")
            ),
            MAX_ALLERGIES
        )

        self.patient_context["medical_history"] = _merge_unique(
            self.patient_context["medical_history"],
            _normalise_list(
                data.get("medical_history")
            ),
            MAX_MEDICAL_HISTORY
        )

        # ----------------------------------------------------
        # Conservative age fallback
        #
        # Only used when the structured understanding layer did
        # not extract age. This handles simple replies such as
        # "I'm 19" without introducing medical keyword lists.
        # ----------------------------------------------------

        if self.patient_context["age"] is None:
            match = re.search(
                r"\b(?:i(?:'m| am)|age\s*(?:is|:)?|aged?)\s*(\d{1,3})\b",
                user_message,
                flags=re.IGNORECASE
            )

            if match:
                try:
                    age = int(match.group(1))

                    if 0 <= age <= 120:
                        self.patient_context["age"] = age
                except ValueError:
                    pass

        # ----------------------------------------------------
        # Current concern
        #
        # Do not overwrite an explicit concern unnecessarily.
        # ----------------------------------------------------

        if not self.patient_context["current_concern"]:
            if self.patient_context["symptoms"]:
                self.patient_context["current_concern"] = (
                    self.patient_context["symptoms"][-1]
                )

        self.patient_context["last_answer"] = user_message

        # Safety is passed separately to the model, but keeping it
        # in context makes debugging and future tool integration easier.
        self.patient_context["safety_level"] = safety_level

    # ========================================================
    # CONTEXT SNAPSHOT
    # ========================================================

    def get_context(self) -> Dict[str, Any]:
        """Return a safe copy for the orchestrator/dashboard."""
        return json.loads(
            json.dumps(
                self.patient_context,
                ensure_ascii=False
            )
        )

    # ========================================================
    # CONTEXT TEXT
    # ========================================================

    def _build_context_text(
        self,
        external_context: str,
        understanding: Optional[Dict[str, Any]],
        safety_level: str
    ) -> str:

        parts = []

        # ----------------------------------------------------
        # Persistent patient context
        # ----------------------------------------------------

        ctx = self.patient_context

        if ctx.get("age") is not None:
            parts.append(f"Age: {ctx['age']}")

        if ctx.get("sex"):
            parts.append(f"Sex: {ctx['sex']}")

        if ctx.get("symptoms"):
            parts.append(
                "Symptoms: " +
                ", ".join(ctx["symptoms"])
            )

        if ctx.get("associated_symptoms"):
            parts.append(
                "Associated symptoms: " +
                ", ".join(ctx["associated_symptoms"])
            )

        if ctx.get("duration"):
            parts.append(
                f"Duration: {ctx['duration']}"
            )

        if ctx.get("severity"):
            parts.append(
                f"Severity: {ctx['severity']}"
            )

        if ctx.get("medications"):
            parts.append(
                "Medications mentioned: " +
                ", ".join(ctx["medications"])
            )

        if ctx.get("allergies"):
            parts.append(
                "Allergies mentioned: " +
                ", ".join(ctx["allergies"])
            )

        if ctx.get("medical_history"):
            parts.append(
                "Medical history mentioned: " +
                ", ".join(ctx["medical_history"])
            )

        if ctx.get("current_concern"):
            parts.append(
                f"Current concern: {ctx['current_concern']}"
            )

        if ctx.get("topic"):
            parts.append(
                f"Current topic: {ctx['topic']}"
            )

        if ctx.get("language"):
            parts.append(
                f"Detected language: {ctx['language']}"
            )

        # ----------------------------------------------------
        # Fresh understanding
        #
        # This is useful because the structured understanding
        # layer may contain information that has not yet become
        # part of the long-term state.
        # ----------------------------------------------------

        if understanding:
            compact_understanding = {}

            for key in (
                "intent",
                "topic",
                "symptoms",
                "associated_symptoms",
                "duration",
                "severity",
                "age",
                "sex",
                "medications",
                "allergies",
                "medical_history",
                "current_concern",
                "language"
            ):
                value = understanding.get(key)

                if value not in (
                    None,
                    "",
                    [],
                    {},
                    "unknown"
                ):
                    compact_understanding[key] = value

            if compact_understanding:
                parts.append(
                    "Latest extracted information: " +
                    _safe_json(compact_understanding)
                )

        # ----------------------------------------------------
        # Existing orchestrator state
        # ----------------------------------------------------

        if external_context:
            external = _clean_text(external_context)

            # Prevent a malformed/huge state object from consuming
            # the whole model context.
            external = external[:1800]

            parts.append(
                "Current orchestrator state: " +
                external
            )

        parts.append(
            f"Current safety level: {safety_level}"
        )

        if not parts:
            return "No previous patient context is available."

        return "\n".join(parts)

    # ========================================================
    # MESSAGE BUILDING
    # ========================================================

    def _build_messages(
        self,
        user_message: str,
        safety_level: str,
        conversation_context: str,
        understanding: Optional[Dict[str, Any]]
    ) -> List[Dict[str, str]]:

        messages: List[Dict[str, str]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.strip()
            }
        ]

        # Recent raw conversation is important for pronouns and
        # short answers such as "yes", "this morning", "same thing".
        if self.history:
            messages.extend(
                self.history[-MAX_HISTORY_MESSAGES:]
            )

        state_text = self._build_context_text(
            external_context=conversation_context,
            understanding=understanding,
            safety_level=safety_level
        )

        current_turn = f"""
PATIENT CONTEXT:
{state_text}

LATEST PATIENT MESSAGE:
{user_message}

INSTRUCTION:
Respond to the latest patient message.
Use the conversation and context above.
Do not repeat questions already answered.
Ask at most one useful follow-up question.
If the situation is normal and enough information is available,
give the most useful safe next step instead of asking unnecessary
questions.
If the safety level is URGENT, recommend prompt medical evaluation.
If the safety level is EMERGENCY, tell the patient to seek
emergency medical help immediately.
Return only the words Voxera should speak aloud.
""".strip()

        messages.append(
            {
                "role": "user",
                "content": current_turn
            }
        )

        return messages

    # ========================================================
    # RESPONSE CLEANING
    # ========================================================

    def _clean_response(self, text: str) -> str:

        text = _clean_text(text)

        if not text:
            return ""

        # Remove accidental code fences.
        text = text.replace("```", "")

        # Remove common model prefixes if they appear.
        prefixes = (
            "Voxera:",
            "Assistant:",
            "Response:",
            "Answer:"
        )

        for prefix in prefixes:
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()

        # Models occasionally emit internal thinking markers.
        # Do not let these reach the phone/TTS layer.
        text = re.sub(
            r"<think>.*?</think>",
            "",
            text,
            flags=re.IGNORECASE | re.DOTALL
        )

        text = re.sub(
            r"<analysis>.*?</analysis>",
            "",
            text,
            flags=re.IGNORECASE | re.DOTALL
        )

        # Remove obvious markdown without touching normal speech.
        text = text.replace("**", "")
        text = text.replace("__", "")

        # Flatten newlines for phone speech.
        text = re.sub(
            r"\s*\n+\s*",
            " ",
            text
        )

        # Avoid huge accidental responses.
        # Prefer complete sentences when possible.
        if len(text) > 650:
            candidate = text[:650]

            last_stop = max(
                candidate.rfind("."),
                candidate.rfind("?"),
                candidate.rfind("!")
            )

            if last_stop >= 180:
                text = candidate[:last_stop + 1]
            else:
                text = candidate.rsplit(" ", 1)[0] + "."

        return text.strip()

    # ========================================================
    # FALLBACK
    # ========================================================

    def _fallback(self, safety_level: str) -> str:

        level = _clean_text(
            safety_level
        ).upper()

        if level == "EMERGENCY":
            return (
                "This could be an emergency. "
                "Please get emergency medical help now."
            )

        if level == "URGENT":
            return (
                "Please get medical care promptly."
            )

        return (
            "Tell me a little more about what's happening."
        )

    # ========================================================
    # SAVE TURN
    # ========================================================

    def _save_turn(
        self,
        user_message: str,
        assistant_message: str
    ):

        self.history.append(
            {
                "role": "user",
                "content": user_message
            }
        )

        self.history.append(
            {
                "role": "assistant",
                "content": assistant_message
            }
        )

        if len(self.history) > MAX_HISTORY_MESSAGES:
            self.history = self.history[
                -MAX_HISTORY_MESSAGES:
            ]

    # ========================================================
    # CHAT
    # ========================================================

    def chat(
        self,
        user_message: str,
        safety_level: str = "NORMAL",
        conversation_context: str = "",
        understanding: Optional[Dict[str, Any]] = None,
        reasoning: Any = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Generate one patient-facing response.

        Returns:
            (
                spoken_response,
                stats
            )
        """

        start = time.perf_counter()

        user_message = _clean_text(user_message)

        if not user_message:
            fallback = self._fallback(
                safety_level
            )

            stats = {
                "elapsed_seconds": 0.0,
                "model": "local",
                "history_messages": len(self.history),
                "turn": self.turn_count,
                "fallback": True
            }

            self.last_stats = stats

            return fallback, stats

        # ----------------------------------------------------
        # Update persistent context BEFORE generating the reply.
        # ----------------------------------------------------

        self._update_context(
            user_message=user_message,
            understanding=understanding,
            safety_level=safety_level
        )

        messages = self._build_messages(
            user_message=user_message,
            safety_level=safety_level,
            conversation_context=conversation_context,
            understanding=understanding
        )

        payload = {
            "messages": messages,

            # Conservative creativity gives natural speech without
            # making safety behavior unnecessarily random.
            "temperature": 0.35,

            "top_p": 0.90,

            # Short phone responses.
            "max_tokens": MAX_OUTPUT_TOKENS,

            "stream": False
        }

        fallback_used = False
        response_text = ""

        try:
            response = SESSION.post(
                SERVER_URL,
                json=payload,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            choices = data.get(
                "choices",
                []
            )

            if choices:
                message = choices[0].get(
                    "message",
                    {}
                )

                response_text = _clean_text(
                    message.get("content", "")
                )

        except requests.exceptions.RequestException as exc:
            print(
                f"[VoxeraBrain] Request error: {exc}"
            )

        except (ValueError, TypeError, KeyError) as exc:
            print(
                f"[VoxeraBrain] Invalid model response: {exc}"
            )

        # ----------------------------------------------------
        # Fallback if local model fails.
        # ----------------------------------------------------

        response_text = self._clean_response(
            response_text
        )

        if not response_text:
            response_text = self._fallback(
                safety_level
            )
            fallback_used = True

        # ----------------------------------------------------
        # Save completed turn.
        # ----------------------------------------------------

        self._save_turn(
            user_message=user_message,
            assistant_message=response_text
        )

        self.turn_count += 1

        elapsed = time.perf_counter() - start

        stats = {
            "elapsed_seconds": round(
                elapsed,
                3
            ),
            "model": "local",
            "server": SERVER_URL,
            "history_messages": len(
                self.history
            ),
            "turn": self.turn_count,
            "fallback": fallback_used,
            "safety_level": safety_level,
            "context": self.get_context()
        }

        self.last_stats = stats

        return response_text, stats

    # ========================================================
    # STREAMING COMPATIBILITY
    # ========================================================

    def chat_stream(
        self,
        user_message: str,
        safety_level: str = "NORMAL",
        conversation_context: str = "",
        understanding: Optional[Dict[str, Any]] = None
    ):
        """
        Compatibility generator.

        The existing voice pipeline uses chat(), so the main path
        remains non-streaming. This generator yields the completed
        spoken response as one piece.
        """

        response, _stats = self.chat(
            user_message=user_message,
            safety_level=safety_level,
            conversation_context=conversation_context,
            understanding=understanding
        )

        yield response


# ============================================================
# DIRECT BRAIN TEST
# ============================================================

def main():

    print("=" * 64)
    print("VOXERA BRAIN — CONTEXT-AWARE VERSION")
    print("=" * 64)
    print()
    print("Server:", SERVER_URL)
    print()
    print("Type 'reset' to clear the conversation.")
    print("Type 'context' to inspect memory.")
    print("Type 'exit' to stop.")
    print()

    brain = VoxeraBrain()

    while True:

        try:
            user_message = input("You: ").strip()

        except (
            KeyboardInterrupt,
            EOFError
        ):
            print("\nGoodbye.")
            break

        if not user_message:
            continue

        command = user_message.lower()

        if command in {
            "exit",
            "quit",
            "bye"
        }:
            print("Goodbye.")
            break

        if command == "reset":
            brain.reset()
            print("Voxera memory reset.")
            print()
            continue

        if command == "context":
            print(
                json.dumps(
                    brain.get_context(),
                    indent=2,
                    ensure_ascii=False
                )
            )
            print()
            continue

        try:
            response, stats = brain.chat(
                user_message=user_message,
                safety_level="NORMAL",
                conversation_context="",
                understanding={}
            )

            print()
            print("Voxera:", response)
            print(
                f"Time: {stats['elapsed_seconds']:.2f}s"
            )
            print()

        except Exception as exc:
            print()
            print(
                "ERROR:",
                exc
            )
            print()


if __name__ == "__main__":
    main()
