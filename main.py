from core.conversation.engine import ConversationEngine


conversation = ConversationEngine()

conversation.add_user_message(
    "I have been coughing since yesterday."
)

conversation.add_assistant_message(
    "Do you have a fever?"
)

conversation.add_user_message(
    "Yes, I have a mild fever."
)

print("\n--- VOXERA CONVERSATION ---\n")

for message in conversation.get_history():
    print(f"{message['role'].upper()}: {message['content']}")