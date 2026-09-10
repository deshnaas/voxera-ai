# Unit tests for the deterministic emergency layer.
#   python tests/test_emergency.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voxera_emergency import check_emergency


# (text, context, last_assistant, expected_category or None)
CASES = [
    # ---- chest (current) -> emergency -------------------------------
    ("My chest feels really tight.", "", "", "cardiac_chest"),
    ("There's a lot of pressure on my chest.", "", "", "cardiac_chest"),
    ("My chest feels heavy and my jaw hurts.", "", "", "cardiac_chest"),
    ("It feels like something is squeezing my chest.", "", "", "cardiac_chest"),
    ("I have a crushing feeling in my chest.", "", "", "cardiac_chest"),
    ("My chest is really tight and heavy right now.", "", "", "cardiac_chest"),
    ("I have really bad chest pain.", "", "", "cardiac_chest"),

    # ---- breathing -------------------------------------------------
    ("I can't breathe.", "", "", "respiratory"),
    ("I'm having a lot of trouble breathing.", "", "", "respiratory"),
    ("I feel like I'm choking.", "", "", "respiratory"),

    # ---- stroke (strong sign, or combo, or sudden) --------------
    ("My face is drooping and one side went weak.", "", "", "stroke"),
    ("My speech suddenly became slurred.", "", "", "stroke"),
    ("I suddenly can't speak properly and my face feels droopy.", "", "", "stroke"),
    ("My face is drooping and my left arm is weak.", "", "", "stroke"),
    ("Suddenly my left arm is numb and I can't speak.", "", "", "stroke"),

    # ---- bleeding / consciousness / seizure --------------------
    ("I'm bleeding heavily and it won't stop.", "", "", "hemorrhage"),
    ("I coughed up blood.", "", "", "hemorrhage"),
    ("My father just passed out and won't wake up.", "", "", "unconscious"),
    ("She's having a seizure right now.", "", "", "seizure"),

    # ---- fever + red flag --------------------------------------
    ("I've had a fever of 104 and I'm very weak and confused.", "", "", "febrile_illness"),
    # any emergency category is acceptable here (fainting is caught first)
    ("My temperature is 105 and I fainted twice.", "", "", "ANY"),

    # ---- self harm -------------------------------------------
    ("Honestly I want to die.", "", "", "self_harm"),
    ("I've been thinking about killing myself.", "", "", "self_harm"),

    # ================= must NOT trigger =====================
    ("I've had a runny nose and a mild cough since yesterday.", "", "", None),
    ("My fever is around 100 and I feel a bit tired.", "", "", None),
    ("I twisted my ankle playing football, it's a little sore.", "", "", None),
    ("I need to refill my blood pressure prescription.", "", "", None),
    ("The chest freezer in my garage stopped working.", "", "", None),
    ("My throat is a little scratchy.", "", "", None),
    ("Can I book a routine check-up?", "", "", None),

    # negation
    ("No, no trouble breathing. What should I do?", "", "", None),
    ("I don't have any chest pain.", "", "", None),
    ("No chest pain, no shortness of breath, just a cough.", "", "", None),
    ("I'm not bleeding.", "", "", None),
    ("I don't want to die, I just feel run down.", "", "", None),

    # resolved / past
    ("The chest tightness went away after I rested.", "", "", None),
    ("I had chest pain yesterday but it's completely gone now.", "", "", None),
    ("I couldn't breathe yesterday but I'm fine now.", "", "", None),

    # cold / throat context explains 'can't speak' -> NOT stroke
    ("I have a blocked nose and a sore throat and my voice is gone.", "", "", None),
    ("I have a cold and I can't really speak because my throat hurts.", "", "", None),
    ("My voice is hoarse and I can't speak well today.", "", "", None),
    ("I can't speak, my throat is on fire and I have a fever and a blocked nose.",
     "", "", None),

    # fever alone / fever + minor symptom -> NOT emergency
    ("I have a fever of 104 and a blocked nose.", "", "", None),
    ("My fever hit 104 last night, now it's 101 and I feel okay.", "", "", None),

    # ECHO CONTAMINATION: Voxera's own words bleed back via the mic
    ("you can't speak or stand or sit. But what do I do though?",
     "PATIENT: my throat is burning and my nose is very blocked",
     "Okay, you seem really unwell. Your throat is burning, your nose is "
     "blocked and you can't speak or stand or sit. You should see a doctor.",
     None),
    ("These can be signs of a stroke and every minute matters. okay okay okay okay",
     "", "These can be signs of a stroke and every minute matters. "
     "Please call emergency services right now.", None),
]


def run():
    failed = 0
    for text, ctx, la, expect in CASES:
        r = check_emergency(text, ctx, la)
        got = r.category if r else None
        if expect is None:
            ok = r is None
        elif expect == "ANY":
            ok = r is not None
        else:
            ok = got == expect
        mark = "ok  " if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  {mark} expect={str(expect):14} got={str(got):14} {text[:56]!r}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
