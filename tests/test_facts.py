# Unit tests for deterministic within-call structured-fact extraction.
#   python tests/test_facts.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voxera_core import extract_facts, facts_line


def run():
    f = {}
    for line in [
        "Hi, my name is Ravi and I've had a fever since yesterday.",
        "It's about 102 and I feel really tired.",
        "I took some paracetamol this morning.",
        "No chest pain though.",
        "Can I book an appointment to see a doctor?",
    ]:
        extract_facts(line, f)

    checks = [
        ("patient_name", f.get("patient_name") == "Ravi"),
        ("temperature_f", f.get("temperature_f") == "102"),
        ("duration", f.get("duration") == "yesterday"),
        ("fever symptom", "fever" in f.get("symptoms", [])),
        ("tired symptom", "tired" in f.get("symptoms", [])),
        ("no false chest pain", "chest pain" not in f.get("symptoms", [])),
        ("medication", "paracetamol" in f.get("medications", [])),
        ("appointment_requested", f.get("appointment_requested") is True),
    ]

    # negative: negated / absent
    g = {}
    extract_facts("I don't have a cough and no headache.", g)
    checks.append(("negation drops cough", "cough" not in g.get("symptoms", [])))
    checks.append(("negation drops headache", "headache" not in g.get("symptoms", [])))

    failed = 0
    for name, ok in checks:
        if not ok:
            failed += 1
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n  facts_line: {facts_line(f)}")
    print(f"\n{len(checks) - failed}/{len(checks)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
