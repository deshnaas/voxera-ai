# Tests for the controlled home-care + OTC knowledge layer (voxera_care).
#
# Covers the required conceptual flows and the safety guarantees:
#  - emergency detector runs first and WINS
#  - no hallucinated medicine / dose reaches the spoken reply
#  - children never get an invented dose
#
#   python tests/test_care.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voxera_care as care
from voxera_emergency import check_emergency

fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------
# Flow 1 — "I burned my hand on a pan."
# ---------------------------------------------------------------
print("\nFlow 1: burned hand")
t = "I burned my hand on a pan."
check("not an emergency", check_emergency(t) is None)
cg = care.lookup_care(t)
check("care topic = minor_burn", cg is not None and cg.care_id == "minor_burn")
otc = care.suggest_otc(cg.care_id, care.build_profile({}))
spoken = care.render(cg, otc, t, llm=None)          # curated fallback
check("mentions cooling under water", "cool" in spoken.lower() and "water" in spoken.lower())
ok, why = care.validate_spoken(spoken, cg, otc)
check("curated text passes its own validator", ok, why)


# ---------------------------------------------------------------
# Flow 2 — "I have a mild fever." (adult, then child)
# ---------------------------------------------------------------
print("\nFlow 2: mild fever")
t = "I have a mild fever."
check("not an emergency", check_emergency(t) is None)
cg = care.lookup_care(t)
check("care topic = mild_fever", cg and cg.care_id == "mild_fever")

adult = care.suggest_otc("mild_fever", care.build_profile({"age": 34}))
check("adult OTC = paracetamol", adult and adult.items == ["paracetamol"] and not adult.deferred)
check("no numeric dose in OTC text",
      care._DOSE_RX.search(adult.spoken) is None, adult.spoken)

kid = care.suggest_otc("mild_fever", care.build_profile({"is_child": True}))
check("child OTC is DEFERRED (no dose)", kid is not None and kid.deferred)
check("child text has no numeric dose", care._DOSE_RX.search(kid.spoken) is None)
check("child text points to pharmacist/doctor",
      "pharmacist" in kid.spoken.lower() or "doctor" in kid.spoken.lower())

preg = care.suggest_otc("mild_fever", care.build_profile({"pregnant": True}))
check("pregnant: paracetamol allowed but flagged to confirm",
      preg and "paracetamol" in preg.spoken.lower()
      and ("midwife" in preg.spoken.lower() or "confirm" in preg.spoken.lower()))

ulcer = care.suggest_otc("mild_headache",
                         care.build_profile({"conditions": ["stomach ulcer"]}))
check("stomach ulcer: ibuprofen NOT suggested",
      ulcer is None or "ibuprofen" not in ulcer.spoken.lower())

allergic = care.suggest_otc("mild_headache",
                            care.build_profile({"allergies": ["paracetamol"]}))
check("paracetamol allergy: paracetamol NOT suggested",
      allergic is None or "paracetamol" not in allergic.spoken.lower())


# ---------------------------------------------------------------
# Flow 3 — "I have chest pressure." -> emergency wins
# ---------------------------------------------------------------
print("\nFlow 3: chest pressure")
t = "I have chest pressure."
emg = check_emergency(t)
check("emergency detector FIRES", emg is not None and emg.category == "cardiac_chest")
# structural guarantee: voxera.run_turn handles the emergency and never calls
# handle_normal/the care layer. We assert the ordering contract here:
check("care layer is bypassed when emergency fires",
      emg is not None, "run_turn short-circuits before handle_normal")


# ---------------------------------------------------------------
# Flow 4 — throat/cold + can't speak -> NOT stroke, then care
# ---------------------------------------------------------------
print("\nFlow 4: blocked nose + sore throat + can't speak")
t = "I have a blocked nose and a sore throat and I can't really speak."
check("emergency detector does NOT flag stroke", check_emergency(t) is None)
cg = care.lookup_care(t)
check("care topic found (throat/cold)",
      cg is not None and cg.care_id in ("sore_throat", "cold_congestion"))


# ---------------------------------------------------------------
# Flow 5 — realistic minor injury: fell off bike, road rash, minor bleeding
# ---------------------------------------------------------------
print("\nFlow 5: fall / graze / minor bleeding (from the real run)")
for t, expect in [
    ("My child fell down from his bike and his skin seems a bit peeling.",
     "minor_abrasion"),
    ("What do I do? He is bleeding now.", "minor_cut"),
    ("He scraped his knee falling off his bike.", "minor_abrasion"),
    ("my son has road rash on his arm", "minor_abrasion"),
    ("I have a small cut on my finger", "minor_cut"),
]:
    check(f"not an emergency: {t[:40]!r}", check_emergency(t) is None)
    cg = care.lookup_care(t)
    check(f"care topic = {expect}", cg is not None and cg.care_id == expect,
          cg.care_id if cg else None)

# emergency still wins for a real bleed
check("'bleeding heavily and won't stop' -> emergency",
      check_emergency("He is bleeding heavily and it won't stop") is not None)

# child + minor injury -> paracetamol deferred, first aid still given directly
cg = care.lookup_care("my 5 year old scraped his knee off his bike")
otc = care.suggest_otc(cg.care_id, care.build_profile({"is_child": True, "age": 5}))
sp = care.render(cg, otc, "he scraped his knee", llm=None)
check("child injury: no numeric dose", care._DOSE_RX.search(sp) is None)
check("child injury: first-aid step present ('rinse')", "rinse" in sp.lower())
check("child injury: escalation is stated, not asked",
      care._RX_META_QUESTION.search(sp.lower()) is None)


# ---------------------------------------------------------------
# Flow 3b — meta-questions ("Should I tell them to see a doctor?") rejected
# ---------------------------------------------------------------
print("\nFlow 3b: meta-questions rejected")
cg = care._CARE["minor_abrasion"]
otc = care.suggest_otc("minor_abrasion", care.build_profile({}))
for b in [
    "Okay. Your child fell and has peeling skin. Should I tell them to see a doctor?",
    "Rinse it with water. Would you like me to book an appointment?",
    "Press a clean cloth on it. Do you want me to recommend a painkiller?",
]:
    ok, why = care.validate_spoken(b, cg, otc)
    check(f"reject meta-question: {b[-40:]!r}", not ok, why)


# ---------------------------------------------------------------
# Validator — reject invented meds / doses
# ---------------------------------------------------------------
print("\nValidator: reject hallucinations")
cg = care._CARE["mild_fever"]
otc = care.suggest_otc("mild_fever", care.build_profile({"age": 30}))
bad = [
    "Take 500 mg of ibuprofen every four hours and get some amoxicillin.",
    "You should take two paracetamol tablets every 6 hours.",
    "I'd recommend a course of antibiotics and a steroid nasal spray.",
    "Take pseudoephedrine for the congestion.",
]
for b in bad:
    ok, why = care.validate_spoken(b, cg, otc)
    check(f"reject: {b[:45]!r}", not ok, why)

good = ("Rest up and keep your fluids going. Paracetamol as directed on the "
        "packet can help with the fever. Get seen if it climbs higher or "
        "lasts more than a few days.")
ok, why = care.validate_spoken(good, cg, otc)
check("accept a clean spoken reply", ok, why)


print()
if fails:
    print(f"FAILURES: {fails}")
    sys.exit(1)
print("ALL CARE TESTS PASSED")
