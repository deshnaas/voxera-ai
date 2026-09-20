#   python tests/test_access_audit.py
import sys

from _pf_fixtures import HOME, OTHER, make_repo, make_svc, run_all
from voxera_patientfetch import access_policy as ap
from voxera_patientfetch.memory_repo import MemoryRepo
from voxera_patientfetch.repository import RepoError


def test_external_access_is_audited_with_who_where_why():
    svc = make_svc()
    d = ap.decide("doctor", OTHER, svc.repo.get_patient_facilities("p1"), reason="emergency_care")
    svc.audit_access(patient_uuid="p1", user_id="u-doc-other", facility_id=OTHER, role="doctor",
                     reason="emergency_care", decision=d, action="open_record", source="dashboard")
    row = svc.repo.access_logs[-1]
    assert row["patient_id"] == "p1" and row["accessed_by_user_id"] == "u-doc-other" and row["facility_id"] == OTHER
    assert row["access_reason"] == "emergency_care" and row["source"] == "dashboard" and row["granted"] is True
    assert row["relationship"] == "external" and "prescriptions" in row["sections"] and row["accessed_at"]


def test_denied_attempts_are_audited_too():
    svc = make_svc()
    d = ap.decide("doctor", OTHER, svc.repo.get_patient_facilities("p1"))       # no reason
    svc.audit_access(patient_uuid="p1", user_id="u", facility_id=OTHER, role="doctor", reason=None,
                     decision=d, action="denied")
    row = svc.repo.access_logs[-1]
    assert row["granted"] is False and row["sections"] == [] and row["action"] == "denied"


def test_audit_row_contains_no_clinical_content():
    svc = make_svc()
    d = ap.decide("doctor", HOME, svc.repo.get_patient_facilities("p1"))
    svc.audit_access(patient_uuid="p1", user_id="u", facility_id=HOME, role="doctor", reason="follow_up", decision=d,
                     meta={"q_len": 41})
    blob = str(svc.repo.access_logs[-1]).lower()
    for phi in ("budecort", "penicillin", "voxera demo", "asthma", "9990"):
        assert phi not in blob


def test_call_source_is_supported():
    svc = make_svc()
    svc.audit_access(patient_uuid="p1", user_id=None, facility_id=HOME, role=None, reason="patient_self",
                     source="call", call_id="c1", action="ask_question")
    row = svc.repo.access_logs[-1]
    assert row["source"] == "call" and row["call_id"] == "c1"


def test_free_text_reason_is_recorded_and_truncated():
    row = ap.build_audit_row(patient_uuid="p1", user_id="u", facility_id=OTHER, role="doctor", reason="other",
                             reason_text="x" * 500, source="dashboard", decision=None, action="open_record")
    assert row["access_reason"].startswith("other: ") and len(row["access_reason"]) <= 210


def test_audit_log_is_readable_newest_first_for_the_dashboard():
    svc = make_svc()
    for i in range(3):
        svc.audit_access(patient_uuid="p1", user_id=f"u{i}", facility_id=HOME, role="doctor", reason="follow_up", action="open_record")
    logs = svc.repo.get_access_logs("p1")
    assert [l["accessed_by_user_id"] for l in logs] == ["u2", "u1", "u0"]


def test_unauditable_access_fails_closed():
    class Broken(MemoryRepo):
        def create_record_access_log(self, row):
            raise RepoError("audit table missing")
    broken = Broken()
    svc = make_svc(broken)
    try:
        svc.audit_access(patient_uuid="p1", user_id="u", facility_id=OTHER, role="doctor", reason="emergency_care")
        raise AssertionError("audit failure was swallowed")
    except RepoError:
        pass


if __name__ == "__main__":
    sys.exit(1 if run_all(dict(globals())) else 0)
