import ollama


class ConversationEngine:
    def __init__(self):
        self.history = []

        self.system_prompt = """
You are Voxera, a conversational AI healthcare assistant.

You communicate naturally and warmly with patients over a phone call.

Your job is to:
- Understand what the patient is saying.
- Ask relevant follow-up questions.
- Give clear and simple information.
- Keep responses conversational and concise.
- Never pretend to be a doctor.
- Never make a diagnosis.
- If something sounds urgent or dangerous, advise the patient to seek immediate medical care.

Speak naturally, like a helpful human on a phone call.
Do not use complicated medical terminology unless necessary.
"""

    def add_user_message(self, message):
        self.history.append({
            "role": "user",
            "content": message
        })

    def add_assistant_message(self, message):
        self.history.append({
            "role": "assistant",
            "content": message
        })

    def get_history(self):
        return self.history

    def generate_response(self, message):
        self.add_user_message(message)

        messages = [
            {
                "role": "system",
                "content": self.system_prompt
            }
        ]

        messages.extend(self.history)

        response = ollama.chat(
            model="qwen3:4b",
            messages=messages
        )

        assistant_message = response["message"]["content"]

        self.add_assistant_message(assistant_message)

        return assistant_message