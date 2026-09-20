#   python tests/test_medication_provenance.py
import sys

from _pf_fixtures import make_repo, make_svc, run_all
from voxera_patientfetch.models import Source, is_clinician_authored


def _by(meds, name):
    return next(m for m in meds if m["medicine_name"] == name)


def test_sources_are_never_blurred():
    meds = make_svc().get_medications("p1")
    bud, mon, cro = _by(meds, "Budecort"), _by(meds, "Montelukast"), _by(meds, "Crocin")
    assert bud["source"] == "clinician" and bud["is_clinician_confirmed"] and bud["is_current"]
    assert mon["source"] == "ocr" and not mon["is_clinician_confirmed"] and not mon["is_current"]
    assert "pending verification" in mon["provenance_label"]
    assert cro["source"] == "patient_reported" and not cro["is_clinician_confirmed"] and not cro["is_current"]
    assert "not verified" in cro["provenance_label"]


def test_only_clinician_sources_count_as_authored():
    assert is_clinician_authored("clinician") and is_clinician_authored("ocr_verified")
    for s in ("ocr", "patient_reported", "imported", "ai_summary", None, ""):
        assert not is_clinician_authored(s)


def test_doctor_can_confirm_ocr_candidate_and_it_becomes_verified_not_original_clinician():
    svc = make_svc()
    med = svc.confirm_medication("it1", user_id="u-doc-home", role="doctor", facility_id="fac-home",
                                 edits={"strength": "5 mg"})
    assert med["source"] == "ocr_verified" and med["status"] == "active" and med["verified_by"] == "u-doc-home"
    assert med["strength"] == "5 mg" and med["source_item_id"] == "it1"
    meds = svc.get_medications("p1")
    mon = [m for m in meds if m["medicine_name"] == "Montelukast"]
    assert len(mon) == 1 and mon[0]["is_current"] and "verified by clinician" in mon[0]["provenance_label"]
    assert svc.repo.get_prescription_item("it1")["verification_status"] == "confirmed"


def test_only_a_doctor_may_confirm():
    svc = make_svc()
    for role in ("nurse", "receptionist", "admin", "operator", None):
        try:
            svc.confirm_medication("it1", user_id="u", role=role, facility_id="f")
        except PermissionError:
            continue
        raise AssertionError(f"{role} confirmed a medication")
    assert svc.repo.get_prescription_item("it1")["verification_status"] == "pending"


def test_cannot_confirm_twice_or_after_reject():
    svc = make_svc()
    svc.reject_medication("it1", user_id="u-nurse-home", role="nurse")
    try:
        svc.confirm_medication("it1", user_id="u-doc-home", role="doctor", facility_id="f")
        raise AssertionError("confirmed a rejected item")
    except ValueError:
        pass
    assert all(m["status"] != "active" or m["medicine_name"] != "Montelukast" for m in svc.get_medications("p1"))


def test_confirm_requires_a_name():
    svc = make_svc()
    svc.repo.items[0]["medicine_name"] = ""
    try:
        svc.confirm_medication("it1", user_id="u", role="doctor", facility_id="f")
        raise AssertionError("confirmed nameless medicine")
    except ValueError:
        pass


def test_rejected_items_are_not_active_or_verified():
    svc = make_svc()
    svc.reject_medication("it1", user_id="u-doc-home", role="doctor")
    mon = _by(svc.get_medications("p1"), "Montelukast")
    assert mon["status"] == "rejected" and not mon["is_current"] and "rejected" in mon["provenance_label"]


def test_old_prescription_is_not_current_unless_status_says_so():
    repo = make_repo()
    repo.prescriptions[0]["status"] = "completed"
    bud = _by(make_svc(repo).get_medications("p1"), "Budecort")
    assert bud["is_clinician_confirmed"] and not bud["is_current"]


def test_patient_reported_medication_is_recorded_with_source():
    svc = make_svc()
    row = svc.record_patient_reported("p2", "Dolo 650")
    assert row["source"] == "patient_reported"
    m = svc.get_medications("p2")[0]
    assert not m["is_clinician_confirmed"] and not m["is_current"]


if __name__ == "__main__":
    sys.exit(1 if run_all(dict(globals())) else 0)
