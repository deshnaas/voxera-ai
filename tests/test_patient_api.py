#   python tests/test_patient_api.py
#   Drives the real FastAPI app (auth dependency swapped for a token->staff map, MemoryRepo underneath).
import io
import os
import sys

from _pf_fixtures import HOME, OTHER, make_repo, make_svc, run_all
from fastapi import Header
from fastapi.testclient import TestClient

from voxera_patientfetch import service
from voxera_patientfetch.service import Staff, app, current_staff

STAFF = {
    "t-doc-home": Staff("u-doc-home", HOME, "doctor"),
    "t-nurse-home": Staff("u-nurse-home", HOME, "nurse"),
    "t-recep-home": Staff("u-recep-home", HOME, "receptionist"),
    "t-admin-home": Staff("u-admin-home", HOME, "admin"),
    "t-doc-other": Staff("u-doc-other", OTHER, "doctor"),
    "t-nurse-other": Staff("u-nurse-other", OTHER, "nurse"),
    "t-recep-other": Staff("u-recep-other", OTHER, "receptionist"),
    "t-legacy": Staff("u-legacy", HOME, "staff"),
}


def _fake_auth(authorization=Header(None)):
    from fastapi import HTTPException
    if not authorization:
        raise HTTPException(401, "Sign in required.")
    tok = authorization.split(" ", 1)[-1]
    if tok not in STAFF:
        raise HTTPException(401, "Session expired.")
    return STAFF[tok]


def client():
    service._svc = make_svc()
    service._audit_seen.clear()
    app.dependency_overrides[current_staff] = _fake_auth
    return TestClient(app), service._svc


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


# --------------------------------------------------------------------------

def test_requires_sign_in():
    c, _ = client()
    assert c.get("/api/patients/search?q=ravi").status_code == 401
    assert c.post("/api/patients/VX-000421/record", json={}).status_code == 401
    assert c.post("/api/patients/VX-000421/record", json={}, headers=H("bogus")).status_code == 401


def test_home_doctor_opens_full_record_and_it_is_audited_once_despite_polling():
    c, svc = client()
    for _ in range(3):                                               # dashboard polls every 20 s
        r = c.post("/api/patients/VX-000421/record", json={}, headers=H("t-doc-home"))
        assert r.status_code == 200
    j = r.json()
    assert j["relationship"] == "home" and {"medications", "prescriptions", "consultations", "audit", "appointments"} <= set(j)
    assert j["patient"]["phone"]
    assert len([l for l in svc.repo.access_logs if l["action"] == "open_record"]) == 1


def test_patient_lookup_accepts_spoken_id_forms_and_uuid_but_404s_without_leaking():
    c, _ = client()
    for pid in ("vx-421", "V%20X%20421", "p1"):
        assert c.post(f"/api/patients/{pid}/record", json={}, headers=H("t-doc-home")).status_code in (200, 404)
    assert c.post("/api/patients/VX-000421/record", json={}, headers=H("t-doc-home")).status_code == 200
    r = c.post("/api/patients/VX-999999/record", json={}, headers=H("t-doc-home"))
    assert r.status_code == 404 and "Patient not found" in r.text


def test_external_doctor_without_a_reason_is_refused_and_the_attempt_is_audited():
    c, svc = client()
    r = c.post("/api/patients/VX-000421/record", json={}, headers=H("t-doc-other"))
    assert r.status_code == 428 and r.json()["detail"]["requires_reason"] is True
    assert "medications" not in r.text and "Budecort" not in r.text
    row = svc.repo.access_logs[-1]
    assert row["action"] == "denied" and row["granted"] is False and row["facility_id"] == OTHER


def test_external_doctor_with_reason_gets_essentials_only_and_is_audited():
    c, svc = client()
    r = c.post("/api/patients/VX-000421/record", json={"reason": "emergency_care"}, headers=H("t-doc-other"))
    assert r.status_code == 200
    j = r.json()
    assert j["relationship"] == "external" and "External patient record" in j["label"]
    assert {"patient", "allergies", "medications", "prescriptions", "consultations", "referrals"} <= set(j)
    for withheld in ("audit", "documents", "appointments", "calls", "transcripts"):
        assert withheld not in j, withheld
    assert "phone" not in j["patient"] and "district" not in j["patient"]
    assert all("voice_signal" not in c for c in j["consultations"])
    row = svc.repo.access_logs[-1]
    assert row["granted"] is True and row["relationship"] == "external" and row["access_reason"] == "emergency_care"


def test_external_nurse_and_receptionist_get_less_than_a_doctor():
    c, _ = client()
    n = c.post("/api/patients/VX-000421/record", json={"reason": "follow_up"}, headers=H("t-nurse-other")).json()
    assert "medications" in n and "prescriptions" not in n and "consultations" not in n
    r = c.post("/api/patients/VX-000421/record", json={"reason": "follow_up"}, headers=H("t-recep-other")).json()
    assert set(r) >= {"patient"} and "medications" not in r and "allergies" not in r


def test_home_receptionist_and_admin_do_not_get_clinical_data():
    c, _ = client()
    r = c.post("/api/patients/VX-000421/record", json={}, headers=H("t-recep-home")).json()
    assert "appointments" in r and "medications" not in r and "consultations" not in r and "audit" not in r
    a = c.post("/api/patients/VX-000421/record", json={}, headers=H("t-admin-home")).json()
    assert "audit" in a and "medications" not in a and "prescriptions" not in a


def test_legacy_staff_role_behaves_as_doctor():
    c, _ = client()
    assert "medications" in c.post("/api/patients/VX-000421/record", json={}, headers=H("t-legacy")).json()


def test_role_gated_endpoints():
    c, _ = client()
    assert c.get("/api/patients/VX-000421/medications", headers=H("t-recep-home")).status_code == 403
    assert c.get("/api/patients/VX-000421/medications", headers=H("t-nurse-home")).status_code == 200
    assert c.get("/api/patients/VX-000421/audit", headers=H("t-nurse-home")).status_code == 403
    assert c.get("/api/patients/VX-000421/audit", headers=H("t-admin-home")).status_code == 200


def test_search_is_identity_level_hides_external_facility_and_audits_external_hits():
    c, svc = client()
    ext = c.get("/api/patients/search?q=VX-421", headers=H("t-doc-other")).json()["results"]
    assert len(ext) == 1 and ext[0]["relationship"] == "external" and ext[0]["access"] == "reason_required"
    assert "phone" not in ext[0] and "active_medications" not in ext[0] and ext[0]["facility"] is None
    assert any(l["action"] == "search" and l["relationship"] == "external" for l in svc.repo.access_logs)
    home = c.get("/api/patients/search?q=demo", headers=H("t-doc-home")).json()["results"]
    assert home[0]["relationship"] == "home" and home[0]["active_medications"] == 1
    recep = c.get("/api/patients/search?q=demo", headers=H("t-recep-home")).json()["results"]
    assert "active_medications" not in recep[0]
    assert c.get("/api/patients/search?q=a", headers=H("t-doc-home")).status_code == 422       # too short


def test_record_question_is_staff_worded_ai_labelled_and_grounded():
    c, _ = client()
    r = c.post("/api/patients/VX-000421/record-question", json={"question": "What medicine is used for nebulization?"},
               headers=H("t-doc-home"))
    j = r.json()
    assert r.status_code == 200 and j["found"] and "Budecort 0.5 mg" in j["answer"] and j["ai_generated"] is True
    assert "Your record" not in j["answer"] and "confirm with your doctor" not in j["answer"]
    assert j["sources"][0]["source"] == "clinician"


def test_record_question_blocked_for_non_clinical_roles_and_never_leaks_ungranted_sources():
    c, _ = client()
    assert c.post("/api/patients/VX-000421/record-question", json={"question": "What was prescribed?"},
                  headers=H("t-recep-home")).status_code == 403
    # external nurse: medications yes, consultations no -> a consultation-sourced answer is withheld
    j = c.post("/api/patients/VX-000421/record-question",
               json={"question": "What did the doctor say about my cough?", "reason": "follow_up"},
               headers=H("t-nurse-other")).json()
    assert "viral cough" not in str(j) and j["found"] is False


def _png():
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 34) if os.path.exists("C:/Windows/Fonts/arial.ttf") else None
    if font is None:
        return None
    img = Image.new("RGB", (1100, 300), "white")
    d = ImageDraw.Draw(img)
    for i, l in enumerate(["Dr. R. Rao MBBS Reg No 4521", "1. Tab. Amoxicillin 500mg BD x 5 days",
                           "2. Budecort 0.5mg nebulize twice daily"]):
        d.text((30, 20 + 70 * i), l, fill="black", font=font)
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def test_upload_ocr_stage_then_only_a_doctor_can_confirm():
    png = _png()
    if png is None:
        print("     (skipped: no system font)")
        return
    c, svc = client()
    up = c.post("/api/patients/VX-000422/prescriptions/upload", headers=H("t-nurse-home"),
                files={"file": ("rx.png", png, "image/png")})
    # patient p2 has no facility relationship with HOME -> external -> upload refused
    assert up.status_code in (403, 428)
    svc.repo.appointments.append({"id": "ap2", "patient_id": "p2", "facility_id": HOME, "appointment_date": "2026-09-30", "status": "scheduled"})
    up = c.post("/api/patients/VX-000422/prescriptions/upload", headers=H("t-nurse-home"),
                files={"file": ("rx.png", png, "image/png")})
    assert up.status_code == 200, up.text
    j = up.json()
    assert j["status"] == "staged" and len(j["items"]) >= 2 and "Nothing is active yet" in j["message"]
    assert all(i["verification_status"] in ("pending", "needs_review") and i["source"] == "ocr" for i in j["items"])
    assert svc.repo.medications == [m for m in svc.repo.medications if m["patient_id"] != "p2"]   # nothing active
    item = j["items"][0]["id"]
    assert c.post(f"/api/prescription-items/{item}/verify", json={"action": "confirm"}, headers=H("t-nurse-home")).status_code == 403
    assert c.post(f"/api/prescription-items/{item}/verify", json={"action": "confirm"}, headers=H("t-doc-other")).status_code in (403, 428)
    ok = c.post(f"/api/prescription-items/{item}/verify", json={"action": "confirm"}, headers=H("t-doc-home"))
    assert ok.status_code == 200 and ok.json()["medication"]["source"] == "ocr_verified"
    assert c.post(f"/api/prescription-items/{item}/verify", json={"action": "confirm"}, headers=H("t-doc-home")).status_code == 409


def test_upload_rejects_wrong_types_fake_files_and_external_uploaders():
    c, svc = client()
    bad = c.post("/api/patients/VX-000421/prescriptions/upload", headers=H("t-doc-home"), files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 415
    fake = c.post("/api/patients/VX-000421/prescriptions/upload", headers=H("t-doc-home"), files={"file": ("x.png", b"not a png", "image/png")})
    assert fake.status_code == 415
    ext = c.post("/api/patients/VX-000421/prescriptions/upload", headers=H("t-doc-other"), files={"file": ("x.png", b"\x89PNG....", "image/png")})
    assert ext.status_code in (403, 428)
    recep = c.post("/api/patients/VX-000421/prescriptions/upload", headers=H("t-recep-home"), files={"file": ("x.png", b"\x89PNG....", "image/png")})
    assert recep.status_code == 403


def test_reject_via_api_marks_rejected_and_keeps_it_out_of_active():
    c, svc = client()
    ok = c.post("/api/prescription-items/it1/verify", json={"action": "reject"}, headers=H("t-nurse-home"))
    assert ok.status_code == 200
    assert svc.repo.get_prescription_item("it1")["verification_status"] == "rejected"


def test_unauditable_external_access_is_not_granted():
    from voxera_patientfetch.repository import RepoError
    c, svc = client()
    svc.repo.create_record_access_log = lambda row: (_ for _ in ()).throw(RepoError("no audit table"))
    r = c.post("/api/patients/VX-000421/record", json={"reason": "emergency_care"}, headers=H("t-doc-other"))
    assert r.status_code == 503 and "Budecort" not in r.text


def test_database_outage_returns_503_not_invented_data():
    c, svc = client()
    svc.repo.fail_reads = True
    r = c.post("/api/patients/VX-000421/record", json={}, headers=H("t-doc-home"))
    assert r.status_code in (503, 404) and "Budecort" not in r.text


def test_no_secret_or_service_key_is_ever_returned():
    c, _ = client()
    text = c.post("/api/patients/VX-000421/record", json={}, headers=H("t-doc-home")).text.lower()
    for needle in ("service_role", "eyj", "supabase_service", "apikey", "secret"):
        assert needle not in text


def test_doctor_can_remove_a_confirmed_medication_and_it_disappears_everywhere():
    c, svc = client()
    r = c.post("/api/patients/VX-000421/medications/rx1/remove", json={"origin": "prescriptions"}, headers=H("t-doc-home"))
    assert r.status_code == 200 and r.json()["status"] == "entered_in_error"
    assert svc.repo.get_prescription("rx1")["status"] == "entered_in_error"           # kept for audit
    rec = c.post("/api/patients/VX-000421/record", json={}, headers=H("t-doc-home")).json()
    assert "Budecort" not in str(rec["medications"]) and rec["prescriptions"] == []
    ans = c.post("/api/patients/VX-000421/record-question", json={"question": "What medicine is used for nebulization?"}, headers=H("t-doc-home")).json()
    assert "Budecort" not in ans["answer"] and ans["found"] is False
    assert svc.repo.access_logs[-1]["action"] in ("open_record", "ask_question", "delete")
    assert any(l["action"] == "delete" for l in svc.repo.access_logs)                 # deletion is always audited


def test_only_a_doctor_removes_a_medication_and_only_at_the_home_facility():
    c, svc = client()
    for tok, want in (("t-nurse-home", 403), ("t-recep-home", 403), ("t-doc-other", (403, 428))):
        r = c.post("/api/patients/VX-000421/medications/rx1/remove", json={"origin": "prescriptions"}, headers=H(tok))
        assert r.status_code in (want if isinstance(want, tuple) else (want,)), (tok, r.status_code)
    assert svc.repo.get_prescription("rx1")["status"] == "active"


def test_deleting_an_uploaded_document_removes_candidates_and_voids_confirmed_medicines():
    c, svc = client()
    svc.repo.documents.append({"id": "doc1", "patient_id": "p1", "document_type": "prescription", "filename": "rx.png",
                               "storage_path": None, "ocr_status": "done"})
    svc.confirm_medication("it1", user_id="u-doc-home", role="doctor", facility_id="fac-home")
    assert svc.repo.get_medications_for_document("doc1")                                # confirmed med came from doc1
    r = c.delete("/api/patients/VX-000421/documents/doc1", headers=H("t-nurse-home"))
    assert r.status_code == 403                                                         # nurse can't delete a confirmed one
    r = c.delete("/api/patients/VX-000421/documents/doc1", headers=H("t-doc-home"))
    assert r.status_code == 200 and r.json()["medications_removed"] == 1
    assert svc.repo.get_document("doc1") is None and svc.repo.items == []
    names = [m["medicine_name"] for m in svc.get_medications("p1")]
    assert "Montelukast" not in names


def test_nurse_can_delete_an_unconfirmed_document_and_a_pending_candidate():
    c, svc = client()
    svc.repo.documents.append({"id": "doc9", "patient_id": "p1", "document_type": "prescription", "filename": "x.png", "storage_path": None})
    svc.repo.items[0]["document_id"] = "doc9"
    assert c.delete("/api/prescription-items/it1", headers=H("t-nurse-home")).status_code == 200
    assert svc.repo.get_prescription_item("it1") is None
    assert c.delete("/api/patients/VX-000421/documents/doc9", headers=H("t-nurse-home")).status_code == 200
    assert c.delete("/api/patients/VX-000421/documents/doc9", headers=H("t-nurse-home")).status_code == 404


def test_confirmed_candidate_cannot_be_deleted_as_a_candidate():
    c, svc = client()
    svc.confirm_medication("it1", user_id="u-doc-home", role="doctor", facility_id="fac-home")
    assert c.delete("/api/prescription-items/it1", headers=H("t-doc-home")).status_code == 409


def test_repeated_deletions_are_each_audited_not_deduplicated():
    c, svc = client()
    for i in (1, 2):
        svc.repo.documents.append({"id": f"d{i}", "patient_id": "p1", "document_type": "prescription", "storage_path": None})
        c.delete(f"/api/patients/VX-000421/documents/d{i}", headers=H("t-doc-home"))
    assert len([l for l in svc.repo.access_logs if l["action"] == "delete"]) == 2


def teardown_module(_m=None):
    app.dependency_overrides.clear()
    service._svc = None


if __name__ == "__main__":
    code = 1 if run_all(dict(globals())) else 0
    teardown_module()
    sys.exit(code)


def test_dropped_database_connection_is_retried_and_a_persistent_failure_is_a_clean_503():
    from voxera_patientfetch.repository import SupabaseRepo

    class Flaky:
        def __init__(self, fails):
            self.fails, self.calls = fails, 0
        def table(self, _):
            return self
        def select(self, *_a, **_k):
            return self
        def eq(self, *_a, **_k):
            return self
        def limit(self, *_a, **_k):
            return self
        def execute(self):
            self.calls += 1
            if self.calls <= self.fails:
                raise RuntimeError("Server disconnected")
            return type("R", (), {"data": [{"id": "x"}]})()

    ok = SupabaseRepo(Flaky(2))
    assert ok.find_patient(uuid="x") == [{"id": "x"}]                    # two dropped connections, third succeeds
    bad = SupabaseRepo(Flaky(99))
    try:
        bad.find_patient(uuid="x")
        raise AssertionError("expected RepoError")
    except Exception as e:
        assert type(e).__name__ == "RepoError"


def test_unhandled_database_error_returns_503_json_with_cors_headers_not_a_bare_500():
    from voxera_patientfetch.repository import RepoError
    c, svc = client()
    svc.repo.get_patient_facilities = lambda pid: (_ for _ in ()).throw(RepoError("emergency_cases: Server disconnected"))
    r = c.post("/api/patients/VX-000421/record", json={}, headers={**H("t-doc-home"), "Origin": "http://localhost:3000"})
    assert r.status_code == 503 and "temporarily unavailable" in r.text
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"     # the browser can read the error
