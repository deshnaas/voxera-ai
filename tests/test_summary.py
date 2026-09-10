# Offline tests for the deterministic call-summary builder (voxera_summary).
#   python tests/test_summary.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voxera_summary as summary

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


class E:
    def __init__(self, **k):
        self.__dict__.update(k)


# ---- non-emergency care call --------------------------------------
s = summary.build_summary(
    patient={"full_name": "Ravi Kumar", "phone": "999", "preferred_language": "English"},
    facts={"symptoms": ["fever", "tired"], "duration": "yesterday",
           "temperature_f": "102", "age": 34},
    transcript="PATIENT: I've had a fever since yesterday\nVOXERA: how high",
    care_events=[{
        "topic": "mild_fever", "label": "a mild fever",
        "steps": ["Rest and drink fluids.", "Keep the room cool.",
                  "Check your temperature again."],
        "escalation": ["the fever goes above 39.5 or lasts more than 3 days"],
        "otc": {"deferred": False, "items": ["paracetamol"],
                "spoken": "Paracetamol as directed on the packet."},
    }],
    outcome="completed",
)
check("chief_concern from first patient line",
      s["chief_concern"].lower().startswith("i've had a fever"))
check("symptoms carried", s["symptoms"] == ["fever", "tired"])
check("no emergency", s["emergency_status"]["detected"] is False)
check("care_given has the topic", s["care_given"][0]["label"] == "a mild fever")
check("otc labelled as guidance not prescription",
      s["otc_guidance"][0]["type"].startswith("OTC guidance"))
check("otc has no numeric dose", "mg" not in s["otc_guidance"][0]["spoken"].lower()
      and "500" not in s["otc_guidance"][0]["spoken"])
txt = summary.render_text(s)
check("render mentions 'NOT a prescription'", "NOT a prescription" in txt)
check("render has paracetamol", "paracetamol" in txt.lower())
check("render never prints a mg dose",
      not any(x in txt.lower() for x in ("mg", "milligram", " tablets", " ml ")))

# ---- child -> deferred OTC ---------------------------------------
s2 = summary.build_summary(
    patient={"full_name": "Child P"},
    facts={"symptoms": ["skin abrasion", "bleeding"], "is_child": True, "age": 6},
    transcript="PATIENT: my child fell off his bike and is bleeding",
    care_events=[{
        "topic": "minor_abrasion", "label": "a graze from a fall",
        "steps": ["Rinse under running water.", "Firm pressure for bleeding."],
        "escalation": ["a child was hit by a vehicle or hit their head"],
        "otc": {"deferred": True, "items": [],
                "spoken": "For a child, check with a pharmacist or doctor."},
    }],
    outcome="completed",
)
check("child summary marks profile is_child", s2["patient_profile"]["is_child"] is True)
check("child OTC deferred", s2["otc_guidance"][0]["deferred"] is True)
check("child render says deferred to pharmacist/clinician",
      "pharmacist" in summary.render_text(s2).lower())

# ---- emergency call --------------------------------------------
s3 = summary.build_summary(
    patient={"full_name": "Chest P"},
    facts={"symptoms": ["chest tightness"]},
    transcript="PATIENT: my chest is really tight and heavy",
    emergency=E(category="cardiac_chest", severity="emergency",
                trigger="chest is really tight and heavy",
                recommended_department="Cardiology"),
    referral={"id": "r1", "status": "pending", "urgency": "high",
              "recommended_department": "Cardiology"},
    outcome="emergency_escalated",
)
check("emergency detected", s3["emergency_status"]["detected"] is True)
check("emergency category", s3["emergency_status"]["category"] == "cardiac_chest")
check("referral block present", s3["referral"]["status"] == "pending")
check("follow_up is escalation",
      s3["follow_up"]["type"] == "escalation")
check("emergency render has DETECTED", "DETECTED" in summary.render_text(s3))

# ---- json-serialisable -----------------------------------------
import json
try:
    json.dumps(s); json.dumps(s2); json.dumps(s3)
    check("summary is JSON-serialisable", True)
except Exception:
    check("summary is JSON-serialisable", False)

print()
if fails:
    print(f"FAILURES: {fails}")
    sys.exit(1)
print("ALL SUMMARY TESTS PASSED")
