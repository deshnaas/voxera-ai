# ============================================================
# VOXERA — HEALTHCARE BRAIN
# STEP 1: FAST SCOPE GATE
#
# IMPORTANT:
# The scope gate does NOT call Qwen.
# It is intentionally fast.
#
# Qwen3:4b should be used later for actual healthcare reasoning.
# ============================================================

import re
import time


# ============================================================
# HEALTHCARE KEYWORDS / PHRASES
# ============================================================

HEALTHCARE_PHRASES = {

    # General health
    "i feel sick",
    "i am sick",
    "im sick",
    "feeling sick",
    "feel unwell",
    "feeling unwell",
    "not feeling well",
    "don't feel well",
    "dont feel well",
    "something feels wrong",
    "something is wrong with me",
    "not well",

    # Pain / discomfort
    "pain",
    "hurts",
    "hurt",
    "ache",
    "aching",
    "sore",
    "burning",
    "burns",
    "cramp",
    "cramps",

    # Symptoms
    "fever",
    "cough",
    "cold",
    "flu",
    "headache",
    "migraine",
    "dizzy",
    "dizziness",
    "nauseous",
    "nausea",
    "vomiting",
    "vomit",
    "diarrhea",
    "constipation",
    "bleeding",
    "blood",
    "swelling",
    "rash",
    "itching",
    "itchy",
    "weak",
    "weakness",
    "shaky",
    "shaking",
    "tired",
    "tiredness",
    "fatigue",
    "exhausted",
    "breathing",
    "breathe",
    "breath",
    "suffocating",
    "choking",
    "faint",
    "fainted",
    "fainting",
    "unconscious",
    "numb",
    "numbness",
    "tingling",
    "palpitations",
    "heart racing",

    # Body parts
    "head",
    "throat",
    "neck",
    "chest",
    "stomach",
    "abdomen",
    "back",
    "shoulder",
    "arm",
    "elbow",
    "wrist",
    "hand",
    "finger",
    "leg",
    "knee",
    "ankle",
    "foot",
    "ear",
    "eye",
    "nose",
    "mouth",
    "tooth",
    "teeth",
    "skin",

    # Medical / care
    "doctor",
    "hospital",
    "clinic",
    "nurse",
    "medicine",
    "medication",
    "tablet",
    "pill",
    "treatment",
    "diagnosis",
    "disease",
    "infection",
    "medical",
    "health",
    "healthcare",
    "symptom",

    # First aid / injury
    "first aid",
    "injured",
    "injury",
    "wound",
    "cut",
    "bleeding",
    "burn",
    "burned",
    "burnt",
    "fracture",
    "broken bone",
    "sprain",
    "twisted ankle",
    "accident",

    # Emergency
    "emergency",
    "help me",
    "can't breathe",
    "cant breathe",
    "cannot breathe",
    "trouble breathing",
    "difficulty breathing",
    "chest pain",
    "severe bleeding",
    "unresponsive",
}


# ============================================================
# OBVIOUS OUT-OF-SCOPE PHRASES
# ============================================================

OUT_OF_SCOPE_PHRASES = {

    "chocolate cake",
    "cake recipe",
    "recipe",
    "python code",
    "write code",
    "coding",
    "programming",
    "cricket score",
    "cricket match",
    "football score",
    "movie recommendation",
    "recommend a movie",
    "tell me a joke",
    "joke",
    "weather",
    "stock price",
    "stock market",
    "news",
    "write an email",
    "write a poem",
    "poem",
    "song lyrics",
    "lyrics",
    "translate this",
    "translation",
    "homework",
    "math problem",
    "solve this equation",
    "capital of",
    "who won",
    "what time is it",
}


# ============================================================
# NORMALIZATION
# ============================================================

def normalize(text):

    text = text.lower().strip()

    # Handle common voice/STT variations
    replacements = {
        "i'm": "im",
        "i’ve": "ive",
        "i've": "ive",
        "can't": "cant",
        "cannot": "cant",
        "don't": "dont",
        "doesn't": "doesnt",
        "isn't": "isnt",
        "won't": "wont",
        "what's": "whats",
        "that's": "thats",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    # Remove punctuation
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # Collapse spaces
    text = re.sub(r"\s+", " ", text).strip()

    return text


# ============================================================
# FAST SCOPE CLASSIFIER
# ============================================================

def classify_scope(message):

    start = time.perf_counter()

    text = normalize(message)

    # --------------------------------------------------------
    # 1. Obvious healthcare phrases
    # --------------------------------------------------------

    for phrase in HEALTHCARE_PHRASES:

        if phrase in text:

            elapsed = time.perf_counter() - start

            return "HEALTHCARE", elapsed, f"matched: {phrase}"

    # --------------------------------------------------------
    # 2. Obvious non-healthcare phrases
    # --------------------------------------------------------

    for phrase in OUT_OF_SCOPE_PHRASES:

        if phrase in text:

            elapsed = time.perf_counter() - start

            return "OUT_OF_SCOPE", elapsed, f"matched: {phrase}"

    # --------------------------------------------------------
    # 3. Healthcare sentence patterns
    # --------------------------------------------------------

    healthcare_patterns = [

        r"\bi\s+(am|feel|feel like)\b",
        r"\bmy\s+\w+\s+(hurts|hurt|aches|is sore)\b",
        r"\bi\s+have\s+(a|an|some)?\s*\w+\b",
        r"\bi\s+cant\s+(breathe|see|hear|walk|move)\b",
        r"\bi\s+cannot\s+(breathe|see|hear|walk|move)\b",
        r"\bi\s+need\s+(a\s+)?doctor\b",
        r"\bshould\s+i\s+see\s+a\s+doctor\b",
        r"\bwhat\s+should\s+i\s+do\s+for\b",
        r"\bwhat\s+do\s+i\s+do\s+for\b",
        r"\bis\s+this\s+(normal|serious|dangerous)\b",
        r"\bdo\s+i\s+need\s+(a\s+)?doctor\b",
        r"\bdo\s+i\s+need\s+medical\b",
    ]

    for pattern in healthcare_patterns:

        if re.search(pattern, text):

            elapsed = time.perf_counter() - start

            return "HEALTHCARE", elapsed, "matched healthcare pattern"

    # --------------------------------------------------------
    # 4. Very common standalone health statements
    # --------------------------------------------------------

    health_words = {
        "tired",
        "shaky",
        "weak",
        "dizzy",
        "sick",
        "unwell",
        "nauseous",
        "exhausted",
        "faint",
        "breathless",
        "shortness",
        "pain",
        "fever",
    }

    words = set(text.split())

    if words.intersection(health_words):

        elapsed = time.perf_counter() - start

        return "HEALTHCARE", elapsed, "matched health word"

    # --------------------------------------------------------
    # 5. Default
    #
    # We do NOT call Qwen here.
    # This is what keeps the scope gate fast.
    # --------------------------------------------------------

    elapsed = time.perf_counter() - start

    return "OUT_OF_SCOPE", elapsed, "no healthcare indicators"


# ============================================================
# RESPONSE
# ============================================================

def out_of_scope_response():

    return (
        "I'm here specifically to help with health-related "
        "concerns. I can't help with that, but I'm happy "
        "to help if you have a health concern."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 60)
    print("VOXERA — HEALTHCARE BRAIN")
    print("STEP 1: FAST SCOPE GATE")
    print("=" * 60)

    print()
    print("⚡ Scope checking: LOCAL / INSTANT")
    print("🧠 Qwen3:4b: NOT USED FOR SCOPE CHECKING")
    print("🔒 Healthcare-only mode")
    print()
    print("Type 'exit' to stop.")
    print()

    while True:

        try:

            patient_message = input("PATIENT: ").strip()

        except KeyboardInterrupt:

            print()
            print()
            print("🛑 Voxera stopped.")
            break

        if not patient_message:
            continue

        if patient_message.lower() in {
            "exit",
            "quit",
            "q"
        }:

            print()
            print("🛑 Voxera stopped.")
            break

        print()
        print("⚡ Checking scope...")

        scope, elapsed, reason = classify_scope(
            patient_message
        )

        print()
        print(f"⏱️ Scope check: {elapsed:.4f}s")
        print(f"🔎 {reason}")
        print()
        print(f"📌 SCOPE: {scope}")

        # ----------------------------------------------------
        # HEALTHCARE
        # ----------------------------------------------------

        if scope == "HEALTHCARE":

            print()
            print("✅ HEALTHCARE REQUEST")
            print()
            print("➡️ Passed scope gate.")
            print("➡️ Send to Qwen3 healthcare brain.")
            print()

        # ----------------------------------------------------
        # OUT OF SCOPE
        # ----------------------------------------------------

        else:

            print()
            print("🚫 OUT OF SCOPE")
            print()
            print("VOXERA:")
            print(out_of_scope_response())
            print()

        print("-" * 60)
        print()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()