#   python tests/test_prescription_extraction.py
import io
import os
import sys

from _pf_fixtures import make_svc, run_all
from voxera_patientfetch import prescription_ai as rx


def one(line, conf=1.0):
    out = rx.parse_medications(line, conf)
    assert len(out) == 1, (line, out)
    return out[0]


def test_budecort_example_from_spec():
    c = one("Budecort 0.5mg nebulize twice daily")
    assert c["medicine_name"] == "Budecort"
    assert c["strength"] == "0.5 mg" and c["route"] == "nebulization" and c["frequency"] == "twice daily"
    assert c["duration"] is None                      # NOT hallucinated
    assert c["source"] == "ocr" and c["verification_status"] in ("pending", "needs_review")


def test_frequency_phrases_do_not_shadow_each_other():
    cases = {"Tab Foo 10mg twice daily": "twice daily", "Tab Foo 10mg twice a day": "twice daily",
             "Tab Foo 10mg once daily": "once daily", "Tab Foo 10mg once a day": "once daily",
             "Tab Foo 10mg thrice daily": "three times daily", "Tab Foo 10mg three times a day": "three times daily",
             "Tab Foo 10mg four times daily": "four times daily", "Tab Foo 10mg daily": "once daily",
             "Tab Foo 10mg every 8 hours": "every 8 hours", "Tab Foo 10mg at bedtime": "at bedtime",
             "Tab Foo 10mg SOS": "as needed", "Tab Foo 10mg OD": "once daily", "Tab Foo 10mg TDS": "three times daily"}
    for line, want in cases.items():
        got = rx.parse_medications(line)[0]["frequency"]
        assert got == want, (line, got, want)


def test_typical_indian_prescription_lines():
    c = one("1. Tab. Amoxicillin 500mg BD x 5 days after food")
    assert (c["medicine_name"], c["strength"], c["frequency"], c["duration"], c["form"]) == \
           ("Amoxicillin", "500 mg", "twice daily", "5 days", "tablet")
    assert "after food" in (c["instructions"] or "")
    c = one("Cap Omeprazole 20 mg OD before breakfast")
    assert c["frequency"] == "once daily" and "before breakfast" in c["instructions"]
    c = one("Syp. Paracetamol 250mg/5ml TDS x 3 days")
    assert c["form"] == "syrup" and c["frequency"] == "three times daily" and c["strength"].startswith("250 mg")
    c = one("Inj. Ceftriaxone 1 g IV BD")
    assert c["route"] == "intravenous" and c["strength"] == "1 g"
    c = one("Tab Metformin 500 mg 1-0-1 x 30 days")
    assert c["frequency"] == "twice daily" and c["duration"] == "30 days"


def test_missing_strength_and_frequency_are_left_null_and_flagged():
    c = one("Tab. Cetirizine")   # known drug, nothing else
    assert c["strength"] is None and c["frequency"] is None
    assert "missing_strength" in c["warnings"] and "missing_frequency" in c["warnings"]
    assert c["verification_status"] == "needs_review"


def test_header_lines_are_not_medicines():
    text = "Dr. R. Rao MBBS\nReg No 12345\nDate: 18/09/2026\nName: Ravi Kumar Age 42\nDiagnosis: Bronchitis\nBudecort 0.5mg nebulize BD"
    out = rx.parse_medications(text)
    assert [c["medicine_name"] for c in out] == ["Budecort"]


def test_typo_correction_is_recorded_not_silent():
    c = one("Tab Amoxicilin 500mg BD")
    assert c["medicine_name"].lower() == "amoxicillin"
    assert any(w.startswith("name_corrected:Amoxicilin") for w in c["warnings"])


def test_low_ocr_confidence_forces_review():
    c = rx.parse_medications("Tab Amoxicillin 500mg BD", doc_confidence=0.5, line_confidences=[0.5])[0]
    assert c["confidence_label"] in ("low", "medium") and c["verification_status"] == "needs_review"


def test_duplicate_medicine_in_document_is_merged_and_flagged():
    out = rx.parse_medications("Tab Amoxicillin 500mg BD\nTab Amoxicillin 500mg BD x 5 days")
    assert len(out) == 1 and "duplicate_in_document" in out[0]["warnings"] and out[0]["duration"] == "5 days"


def test_already_in_record_flag():
    out = rx.parse_medications("Budecort 0.5mg nebulize BD", existing_names=["Budecort"])
    assert "already_in_record" in out[0]["warnings"]


# Text exactly as RapidOCR read a real two-label pharmacy prescription (run-together words, full-width brackets,
# directions printed ABOVE the drug name, dose written ".5 mg").
PHARMACY_OCR = """Rite Aid Pharmacy Rx:9732-0091
234 High St Wadsvorth, OH
(330)334-6798
Doe,John
Take1 tablet by mouth,daily.
Apixaban(Eliquis).5mg（Cty30) Fill date:1/31/2024
Pink oval804 Refills0
Dr. Frank Hemorhage
Discard after: 1/31/2025
Rite Aid Pharm aey Rx:0901-276
Doe,John
Take1 tablet by mouth,twice a day before m eals
Pantoprazole20mg（Oty90） Fill date:2/28/2025
White circle 40 Refills:1
Dr.Carl Jr.Habanero
Discard after:2/28/2026"""


def test_two_medicine_pharmacy_label_yields_both_with_their_own_directions():
    meds = rx.parse_medications(PHARMACY_OCR, 0.95)
    by = {m["medicine_name"]: m for m in meds}
    assert set(by) == {"Apixaban", "Pantoprazole"}, list(by)
    a, p = by["Apixaban"], by["Pantoprazole"]
    assert (a["strength"], a["frequency"], a["route"], a["generic_name"]) == ("0.5 mg", "once daily", "oral", "Eliquis")
    assert (p["strength"], p["frequency"], p["route"]) == ("20 mg", "twice daily", "oral")
    assert "before meals" in p["instructions"]
    assert "directions_from_adjacent_line" in a["warnings"] and "directions_from_adjacent_line" in p["warnings"]
    assert all(m["verification_status"] in ("pending", "needs_review") for m in meds)


def test_directions_are_never_borrowed_twice_or_from_a_far_line():
    text = "Take 1 tablet by mouth, daily.\nApixaban 5 mg\nWarfarin 2 mg"
    meds = {m["medicine_name"]: m for m in rx.parse_medications(text)}
    assert meds["Apixaban"]["frequency"] == "once daily"
    assert meds["Warfarin"]["frequency"] is None                     # one directions line, one medicine


def test_dose_without_leading_zero_is_normalised_not_misread():
    assert one("Apixaban .5 mg BD")["strength"] == "0.5 mg"
    assert one("Tab Foo 0.5mg BD")["strength"] == "0.5 mg"


def test_route_is_not_inferred_from_drug_or_form():
    c = one("Tab. Amoxicillin 500mg BD")
    assert c["route"] is None                          # 'oral' is never guessed


def test_staging_never_creates_an_active_medication():
    svc = make_svc()
    res = rx.process_document(svc, "p2", b"", "rx.txt", plain_text="Tab Amoxicillin 500mg BD x 5 days\nBudecort 0.5mg nebulize BD")
    assert res["status"] == "staged" and len(res["items"]) == 2
    assert all(i["verification_status"] in ("pending", "needs_review") and i["source"] == "ocr" for i in res["items"])
    assert svc.repo.medications == [m for m in svc.repo.medications if m["patient_id"] != "p2"]   # nothing promoted
    meds = svc.get_medications("p2")
    assert meds and all(not m["is_clinician_confirmed"] and not m["is_current"] for m in meds)
    assert all("pending verification" in m["provenance_label"] for m in meds)


def test_unsupported_and_empty_inputs_are_reported_unreadable():
    svc = make_svc()
    assert rx.process_document(svc, "p2", b"x", "rx.exe")["status"] == "unreadable"
    assert rx.process_document(svc, "p2", b"", "rx.png")["status"] == "unreadable"
    assert rx.UNREADABLE == "Unable to reliably read this prescription."


def _make_prescription_png() -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for path in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/calibri.ttf"):
        if os.path.exists(path):
            font = ImageFont.truetype(path, 34)
            break
    if font is None:
        return b""
    img = Image.new("RGB", (1100, 420), "white")
    d = ImageDraw.Draw(img)
    lines = ["Dr. R. Rao  MBBS   Reg No 4521", "Date: 18/09/2026",
             "1. Tab. Amoxicillin 500mg BD x 5 days",
             "2. Budecort 0.5mg nebulize twice daily",
             "3. Syp. Paracetamol 250mg/5ml TDS"]
    y = 20
    for l in lines:
        d.text((30, y), l, fill="black", font=font)
        y += 70
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_real_ocr_on_generated_image():
    png = _make_prescription_png()
    if not png:
        print("     (skipped: no system font)")
        return
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        print("     (skipped: rapidocr not installed)")
        return
    ext = rx.extract_text(png, "rx.png")
    assert not ext.error and ext.method == "ocr" and ext.confidence > 0.6, (ext.error, ext.confidence)
    names = [c["medicine_name"].lower() for c in rx.parse_medications(ext.text, ext.confidence, ext.line_confidences)]
    assert "amoxicillin" in names and "budecort" in names and "paracetamol" in names, names


if __name__ == "__main__":
    sys.exit(1 if run_all(dict(globals())) else 0)
