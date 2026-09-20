#   python tests/test_cross_facility_access.py
import sys

from _pf_fixtures import HOME, OTHER, make_repo, run_all
from voxera_patientfetch import access_policy as ap


def fac(repo, pid="p1"):
    return repo.get_patient_facilities(pid)


def test_home_facility_doctor_gets_full_record_no_reason_needed():
    d = ap.decide("doctor", HOME, fac(make_repo()))
    assert d.allowed and d.relationship == "home" and not d.requires_reason
    assert {ap.TRANSCRIPTS, ap.AUDIT, ap.NOTES, ap.CALLS} <= d.sections


def test_other_facility_is_external_and_needs_a_reason():
    d = ap.decide("doctor", OTHER, fac(make_repo()))
    assert not d.allowed and d.relationship == "external" and d.requires_reason and d.deny_reason == "reason_required"
    assert "External patient record" in d.label


def test_external_doctor_with_reason_gets_essentials_only():
    d = ap.decide("doctor", OTHER, fac(make_repo()), reason="emergency_care")
    assert d.allowed and d.relationship == "external"
    assert d.sections == {ap.IDENTITY, ap.ALLERGIES, ap.MEDICATIONS, ap.PRESCRIPTIONS, ap.CONSULTATIONS, ap.REFERRALS}
    for withheld in (ap.TRANSCRIPTS, ap.CALLS, ap.NOTES, ap.AUDIT, ap.APPOINTMENTS, ap.DOCUMENTS):
        assert withheld not in d.sections            # no conversations / internal notes / admin data


def test_reason_other_requires_free_text_and_unknown_reason_is_rejected():
    r = fac(make_repo())
    assert not ap.decide("doctor", OTHER, r, reason="other").allowed
    assert ap.decide("doctor", OTHER, r, reason="other", reason_text="patient collapsed at our gate").allowed
    assert not ap.decide("doctor", OTHER, r, reason="curiosity").allowed


def test_patient_id_alone_is_not_authorisation():
    # no role / no facility membership -> denied regardless of any reason
    r = fac(make_repo())
    for role, facility in ((None, OTHER), ("doctor", None), ("patient", OTHER), ("", HOME), ("hacker", HOME)):
        d = ap.decide(role, facility, r, reason="emergency_care")
        assert not d.allowed and d.deny_reason == "not_staff", (role, facility)


def test_role_matrix_home():
    r = fac(make_repo())
    nurse = ap.decide("nurse", HOME, r).sections
    assert ap.MEDICATIONS in nurse and ap.TRANSCRIPTS not in nurse and ap.AUDIT not in nurse
    recep = ap.decide("receptionist", HOME, r).sections
    assert recep == {ap.IDENTITY, ap.APPOINTMENTS}
    assert ap.decide("operator", HOME, r).sections == {ap.IDENTITY, ap.APPOINTMENTS}
    admin = ap.decide("admin", HOME, r).sections
    assert ap.AUDIT in admin and ap.MEDICATIONS not in admin and ap.PRESCRIPTIONS not in admin and ap.TRANSCRIPTS not in admin


def test_role_matrix_external_is_never_broader_than_home():
    r = fac(make_repo())
    for role in ap.ROLES:
        ext = ap.decide(role, OTHER, r, reason="follow_up").sections
        home = ap.decide(role, HOME, r).sections
        assert ext <= home | {ap.IDENTITY}, role
    assert ap.decide("receptionist", OTHER, r, reason="follow_up").sections == {ap.IDENTITY}
    assert ap.decide("nurse", OTHER, r, reason="follow_up").sections == {ap.IDENTITY, ap.ALLERGIES, ap.MEDICATIONS}


def test_external_identity_hides_phone_and_locality():
    p = dict(make_repo().patients[0], age=42, district="D")
    ext = ap.redact_identity(p, "external")
    home = ap.redact_identity(p, "home")
    assert "phone" not in ext and "district" not in ext and ext["patient_id"] == "VX-000421"
    assert "phone" in home


def test_search_projection_is_identity_level_for_non_clinical_roles():
    p = {"id": "p1", "patient_id": "VX-000421", "full_name": "X", "age": 42, "gender": "f", "phone": "999"}
    summ = {"last_consultation": "2026-09-12", "active_medications": 1, "allergies": 2}
    assert "active_medications" not in ap.search_projection(p, "receptionist", summ)
    assert "phone" not in ap.search_projection(p, "doctor", summ)
    assert ap.search_projection(p, "doctor", summ)["active_medications"] == 1


def test_patient_with_no_facility_history_is_external_for_everyone():
    d = ap.decide("doctor", HOME, set(), reason="emergency_care")
    assert d.relationship == "external"


if __name__ == "__main__":
    sys.exit(1 if run_all(dict(globals())) else 0)
