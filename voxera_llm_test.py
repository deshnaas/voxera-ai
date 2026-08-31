import time
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

MODEL = "Qwen/Qwen2.5-3B-Instruct"

print("Loading Voxera brain...")

tokenizer = AutoTokenizer.from_pretrained(MODEL)

model = AutoModelForCausalLM.from_pretrained(
    MODEL,
    torch_dtype=torch.float32,
    device_map="cpu"
)

model.eval()

messages = [
    {
        "role": "system",
        "content": """You are Voxera, a warm and calm healthcare conversational assistant.

Your job is to help users understand their health concerns and guide them toward appropriate care.

You are NOT a doctor and must never claim to diagnose a medical condition.

Speak naturally and conversationally.
Keep responses short because you are designed for real-time phone conversations.
Ask one useful follow-up question at a time.
If the user describes a potentially life-threatening emergency, advise them to seek emergency medical help immediately.
"""
    },
    {
        "role": "user",
        "content": "I've been feeling dizzy since this morning."
    }
]

prompt = tokenizer.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True
)

inputs = tokenizer(prompt, return_tensors="pt")

print("Generating response...")
start = time.time()

with torch.no_grad():
    output = model.generate(
        **inputs,
        max_new_tokens=120,
        do_sample=True,
        temperature=0.7,
        top_p=0.9
    )

elapsed = time.time() - start

new_tokens = output[0][inputs["input_ids"].shape[1]:]

response = tokenizer.decode(
    new_tokens,
    skip_special_tokens=True
)

print("\n" + "=" * 50)
print("VOXERA:")
print(response)
print("=" * 50)
print(f"Generation time: {elapsed:.2f}s")
