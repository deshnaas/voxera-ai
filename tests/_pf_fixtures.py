"""Shared fixtures for the Patient Intelligence tests (no Supabase, no LLM)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voxera_patientfetch.memory_repo import MemoryRepo          # noqa: E402
from voxera_patientfetch.patient_ai import PatientIntelligenceAI  # noqa: E402

HOME = "fac-home"
OTHER = "fac-other"


def make_repo() -> MemoryRepo:
    r = MemoryRepo()
    r.facilities = {HOME: "CareSetu Hospital A", OTHER: "Hospital B"}
    r.patients = [
        {"id": "p1", "patient_id": "VX-000421", "full_name": "Voxera Demo Patient", "phone": "+91 99900 01111",
         "date_of_birth": "1984-03-10", "gender": "female", "allergies": "Penicillin", "chronic_conditions": "Asthma"},
        {"id": "p2", "patient_id": "VX-000422", "full_name": "Ravi Kumar", "phone": "9990002222",
         "date_of_birth": "1990-01-01", "gender": "male"},
        {"id": "p3", "patient_id": "VX-000423", "full_name": "Dup One", "phone": "1"},
        {"id": "p4", "patient_id": "VX-000423", "full_name": "Dup Two", "phone": "2"},
    ]
    # clinician prescription (v2 flat shape)
    r.prescriptions = [{
        "id": "rx1", "patient_id": "p1", "medication_name": "Budecort", "dosage": "0.5 mg", "route": "nebulization",
        "frequency": "twice daily", "duration": "5 days", "status": "active", "prescribed_by": "dr@hospital",
        "prescribed_at": "2026-09-18T10:00:00+00:00", "facility_id": HOME}]
    # OCR-staged candidate (pending) and a patient-reported medicine
    r.items = [{"id": "it1", "patient_id": "p1", "medicine_name": "Montelukast", "strength": "10 mg",
                "frequency": "once daily", "verification_status": "pending", "source": "ocr",
                "created_at": "2026-09-19T09:00:00+00:00", "document_id": "doc1", "confidence": 0.8}]
    r.medications = [{"id": "m1", "patient_id": "p1", "medicine_name": "Crocin", "source": "patient_reported",
                      "status": "active", "created_at": "2026-09-10T09:00:00+00:00"}]
    r.calls = [
        {"id": "c1", "patient_id": "p1", "facility_id": HOME, "created_at": "2026-09-12T09:00:00+00:00", "outcome": "completed"},
        {"id": "c2", "patient_id": "p1", "facility_id": HOME, "created_at": "2026-09-05T09:00:00+00:00", "outcome": "completed"},
    ]
    r.summaries = {
        "c1": {"chief_concern": "cough and mild fever", "symptoms": ["cough", "fever"],
               "care_given": [{"label": "Cough care"}], "otc_guidance": [{"items": ["paracetamol"], "deferred": False}],
               "patient_profile": {"allergies": ["dust"], "conditions": []},
               "emergency_status": {"detected": False}},
        "c2": {"chief_concern": "chest burning after dinner", "symptoms": ["burning"], "emergency_status": {"detected": False}},
    }
    r.assessments = [{"call_id": "c1", "ai_summary": "Likely a viral cough; monitor and see a doctor if it persists.", "risk_level": "low"}]
    r.referrals = [{"id": "ref1", "patient_id": "p1", "receiving_facility_id": HOME, "status": "accepted",
                    "recommended_department": "Pulmonology", "created_at": "2026-09-13T09:00:00+00:00"}]
    r.appointments = [{"id": "ap1", "patient_id": "p1", "facility_id": HOME, "appointment_date": "2026-09-25",
                       "doctor_name": "Dr. Rao", "status": "scheduled", "created_at": "2026-09-14T09:00:00+00:00"}]
    r.hospital_users = [
        {"user_id": "u-doc-home", "facility_id": HOME, "role": "doctor"},
        {"user_id": "u-nurse-home", "facility_id": HOME, "role": "nurse"},
        {"user_id": "u-recep-home", "facility_id": HOME, "role": "receptionist"},
        {"user_id": "u-admin-home", "facility_id": HOME, "role": "admin"},
        {"user_id": "u-doc-other", "facility_id": OTHER, "role": "doctor"},
        {"user_id": "u-nurse-other", "facility_id": OTHER, "role": "nurse"},
        {"user_id": "u-recep-other", "facility_id": OTHER, "role": "receptionist"},
    ]
    return r


def make_svc(repo=None) -> PatientIntelligenceAI:
    return PatientIntelligenceAI(repo or make_repo())


def run_all(namespace: dict) -> int:
    """Tiny runner so tests work as `python tests/test_x.py` AND under pytest."""
    fails = 0
    tests = [(k, v) for k, v in namespace.items() if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:                      # noqa: BLE001
            fails += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    return fails
