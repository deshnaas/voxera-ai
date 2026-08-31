import requests
import json
import time


# ============================================================
# VOXERA CONVERSATIONAL BRAIN
# ============================================================

SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"

SESSION = requests.Session()


# ============================================================
# SMALL, FAST SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are Voxera, a warm conversational healthcare assistant speaking
to a patient on a phone call.

Speak naturally, like a real human conversation.

RULES:
- Be warm, calm and attentive.
- Respond directly to what the patient just said.
- Keep the response very short: usually 1 sentence.
- Ask only ONE question at a time.
- Never ask unnecessary questions.
- Never repeat information the patient already gave.
- Do not sound scripted.
- Avoid phrases like "I understand" or "I'm sorry to hear that"
  unless they genuinely fit.
- Use natural contractions.
- No lists, headings or markdown.
- Never diagnose.
- Never prescribe medication.
- Never invent information.
- Do not mention being an AI unless asked.

For NORMAL situations:
Continue the conversation naturally and ask the most useful
next question when needed.

For URGENT situations:
Briefly recommend prompt medical evaluation and ask one useful
question only if necessary.

For EMERGENCY situations:
Be direct and tell the patient to seek emergency medical help
immediately. Do not waste time asking unnecessary questions.

Match the patient's language.
English, Hindi and Hinglish are supported.

Return ONLY what Voxera should say aloud.
"""


class VoxeraBrain:

    def __init__(self):

        self.history = []

        # Keep context extremely small for low latency.
        self.max_history_messages = 4

    # ========================================================
    # BUILD MESSAGES
    # ========================================================

    def _build_messages(
        self,
        user_message,
        safety_level="NORMAL",
        conversation_context="",
        understanding=None
    ):

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.strip()
            }
        ]

        # ----------------------------------------------------
        # RECENT CONVERSATION
        # ----------------------------------------------------

        if self.history:

            messages.extend(
                self.history[
                    -self.max_history_messages:
                ]
            )

        # ----------------------------------------------------
        # VERY COMPACT CONTEXT
        # ----------------------------------------------------

        context_parts = [
            f"Safety: {safety_level}",
            f"Latest patient message: {user_message}"
        ]

        # Only include useful structured information.
        if understanding:

            symptoms = understanding.get(
                "symptoms",
                []
            )

            associated = understanding.get(
                "associated_symptoms",
                []
            )

            duration = understanding.get(
                "duration"
            )

            severity = understanding.get(
                "severity"
            )

            if symptoms:
                context_parts.append(
                    f"Symptoms: {', '.join(symptoms)}"
                )

            if associated:
                context_parts.append(
                    f"Other symptoms: {', '.join(associated)}"
                )

            if duration:
                context_parts.append(
                    f"Duration: {duration}"
                )

            if severity:
                context_parts.append(
                    f"Severity: {severity}"
                )

        if conversation_context:

            # Limit state size.
            short_context = conversation_context[
                :1200
            ]

            context_parts.append(
                f"Conversation state: {short_context}"
            )

        context_parts.append(
            "Respond naturally and briefly."
        )

        messages.append(
            {
                "role": "user",
                "content": "\n".join(
                    context_parts
                )
            }
        )

        return messages

    # ========================================================
    # STREAMING CHAT
    # ========================================================

    def chat_stream(
        self,
        user_message,
        safety_level="NORMAL",
        conversation_context="",
        understanding=None
    ):

        messages = self._build_messages(
            user_message=user_message,
            safety_level=safety_level,
            conversation_context=conversation_context,
            understanding=understanding
        )

        payload = {
            "messages": messages,

            # Slightly lower temperature = faster/more predictable.
            "temperature": 0.45,

            "top_p": 0.9,

            # We only want short phone responses.
            "max_tokens": 32,

            # IMPORTANT:
            # llama.cpp starts sending tokens immediately.
            "stream": True
        }

        pieces = []

        try:

            response = SESSION.post(
                SERVER_URL,
                json=payload,
                timeout=15,
                stream=True
            )

            response.raise_for_status()

            for line in response.iter_lines(
                decode_unicode=True
            ):

                if not line:
                    continue

                if not line.startswith("data:"):
                    continue

                data_text = line[
                    len("data:"):
                ].strip()

                if data_text == "[DONE]":
                    break

                try:

                    data = json.loads(
                        data_text
                    )

                except json.JSONDecodeError:

                    continue

                choices = data.get(
                    "choices",
                    []
                )

                if not choices:
                    continue

                delta = choices[0].get(
                    "delta",
                    {}
                )

                token = delta.get(
                    "content",
                    ""
                )

                if not token:
                    continue

                pieces.append(token)

                # --------------------------------------------
                # THIS IS THE IMPORTANT PART
                #
                # The caller receives each generated piece
                # immediately instead of waiting for the
                # complete response.
                # --------------------------------------------

                yield token

        except requests.exceptions.RequestException as e:

            print(
                f"[Brain error: {e}]"
            )

            fallback = self._fallback(
                safety_level
            )

            pieces = [fallback]

            yield fallback

        # ====================================================
        # SAVE COMPLETED TURN
        # ====================================================

        assistant_message = "".join(
            pieces
        ).strip()

        if not assistant_message:

            assistant_message = self._fallback(
                safety_level
            )

        assistant_message = (
            assistant_message
            .replace("```", "")
            .strip()
        )

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

        if len(self.history) > self.max_history_messages:

            self.history = self.history[
                -self.max_history_messages:
            ]

    # ========================================================
    # NORMAL CHAT
    #
    # Keeps compatibility with the orchestrator.
    # ========================================================

    def chat(
        self,
        user_message,
        safety_level="NORMAL",
        conversation_context="",
        understanding=None,
        reasoning=None
    ):

        pieces = []

        for token in self.chat_stream(
            user_message=user_message,
            safety_level=safety_level,
            conversation_context=conversation_context,
            understanding=understanding
        ):

            pieces.append(token)

        return "".join(
            pieces
        ).strip()

    # ========================================================
    # FALLBACK
    # ========================================================

    def _fallback(
        self,
        safety_level
    ):

        if safety_level == "EMERGENCY":

            return (
                "This may be an emergency. "
                "Please get emergency medical help now."
            )

        if safety_level == "URGENT":

            return (
                "Please get medical care promptly."
            )

        return (
            "Okay. Tell me a little more about that."
        )


# ============================================================
# DIRECT TEST
# ============================================================

def main():

    print("=" * 60)
    print("VOXERA BRAIN")
    print("=" * 60)
    print()

    print(
        "Low-latency streaming conversational LLM"
    )

    print(
        "Server:",
        SERVER_URL
    )

    print()

    print(
        "Type 'exit' to stop."
    )

    print()

    brain = VoxeraBrain()

    while True:

        try:

            user_message = input(
                "You: "
            ).strip()

        except (
            KeyboardInterrupt,
            EOFError
        ):

            print("\nGoodbye.")
            break

        if not user_message:

            continue

        if user_message.lower() in {
            "exit",
            "quit",
            "bye"
        }:

            print("Goodbye.")
            break

        print(
            "Voxera: ",
            end="",
            flush=True
        )

        start = time.perf_counter()

        for token in brain.chat_stream(
            user_message=user_message,
            safety_level="NORMAL",
            conversation_context="",
            understanding={}
        ):

            print(
                token,
                end="",
                flush=True
            )

        elapsed = (
            time.perf_counter() - start
        )

        print()
        print()
        print(
            f"TIME TO COMPLETE: "
            f"{elapsed:.2f} seconds"
        )
        print()


if __name__ == "__main__":

    main()