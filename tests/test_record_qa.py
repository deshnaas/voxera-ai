#   python tests/test_record_qa.py
import sys

from _pf_fixtures import make_repo, make_svc, run_all
from voxera_patientfetch import record_qa as qa

NOT_FOUND = "I couldn't find that information in your available records."


def ask(q, svc=None, pid="p1", **kw):
    return qa.answer_patient_history_question(svc or make_svc(), pid, q, **kw)


def test_nebulizer_medicine_comes_from_the_record_with_hedge():
    a = ask("What medicine did the doctor give me for my nebulizer?")
    assert a.found and a.intent == "medication_by_route"
    assert "Budecort 0.5 mg" in a.answer and "nebulization" in a.answer and "18 September" in a.answer
    assert "twice daily" in a.answer and "can't confirm that it is appropriate" in a.answer
    assert a.sources and a.sources[0]["type"] == "medication" and a.sources[0]["source"] == "clinician"
    assert a.confidence >= 0.85


def test_prescription_question_describes_it_as_previous_not_advice():
    a = ask("What was my previous prescription?")
    assert a.found and "was prescribed" in a.answer
    assert "you should take" not in a.answer.lower()


def test_should_i_take_it_now_gets_the_fixed_hedge_and_no_decision():
    a = ask("Should I take the nebulizer medicine now?")
    assert a.found and qa.OLD_RX_NOW in a.answer and "should_i_hedge" in a.notes
    assert "yes" not in a.answer.lower().split()[:3]


def test_doctor_said_about_cough_retrieves_consultation():
    a = ask("What did the doctor say about my cough?")
    assert a.found and a.intent == "doctor_said"
    assert "viral cough" in a.answer and "12 September" in a.answer
    assert a.sources[0]["type"] == "consultation"


def test_medicine_for_fever_is_labelled_general_guidance_not_a_prescription():
    a = ask("What medicine was prescribed for fever?")
    assert a.found and "not a prescription" in a.answer and "paracetamol" in a.answer
    assert a.sources[0]["source"] == "ai_summary"


def test_diabetes_absent_says_not_found_never_no():
    a = ask("Do I have diabetes?")
    assert not a.found and a.answer == NOT_FOUND
    assert "no" not in a.answer.lower().split()[:2]


def test_diabetes_present_is_answered_from_record():
    repo = make_repo()
    repo.patients[0]["chronic_conditions"] = "Asthma, Type 2 Diabetes"
    a = ask("Do I have diabetes?", make_svc(repo))
    assert a.found and "Diabetes" in a.answer and a.sources[0]["type"] == "patient"


def test_asthma_is_found_in_conditions():
    a = ask("Do I have asthma?")
    assert a.found and "Asthma" in a.answer


def test_last_consultation_date():
    a = ask("When was my last consultation?")
    assert a.found and "12 September" in a.answer


def test_referral_appointment_facility_allergy_questions():
    assert "Pulmonology" in ask("Was I previously referred?").answer
    assert "25 September" in ask("When is my next appointment?").answer and "Dr. Rao" in ask("When is my next appointment?").answer
    assert "CareSetu Hospital A" in ask("Which hospital did I visit?").answer
    assert "Penicillin" in ask("What am I allergic to?").answer


def test_current_medications_separates_active_from_unverified():
    a = ask("What medications are currently in my record?")
    assert a.found and "Budecort" in a.answer and "active" in a.answer
    assert "hasn't verified" in a.answer
    assert "Montelukast" not in a.answer.split("active:")[1].split(".")[0]      # pending item is NOT listed as active


def test_ocr_pending_medicine_is_never_called_prescribed():
    repo = make_repo()
    repo.prescriptions = []                                # only the OCR candidate + patient-reported remain
    a = ask("What is my Montelukast prescription?", make_svc(repo))
    # falls to previous_prescription: must describe it as an unverified upload
    assert "hasn't verified" in a.answer and "was prescribed" not in a.answer


def test_patient_reported_medicine_is_worded_as_reported():
    repo = make_repo()
    repo.prescriptions, repo.items = [], []
    a = ask("What medicine did I use last time?", make_svc(repo))
    assert "You told us" in a.answer and "hasn't verified" in a.answer


def test_conflicting_current_prescriptions_are_flagged_not_resolved():
    repo = make_repo()
    repo.prescriptions.append({"id": "rx2", "patient_id": "p1", "medication_name": "Budecort", "dosage": "1 mg",
                               "route": "nebulization", "frequency": "once daily", "status": "active",
                               "prescribed_at": "2026-09-19T10:00:00+00:00"})
    a = ask("What medicine do I use for my nebulizer?", make_svc(repo))
    assert a.conflict and a.answer == qa.CONFLICT and len(a.sources) >= 2


def test_nothing_in_record_says_not_found():
    a = ask("What medicine was prescribed for my nebulizer?", pid="p2")
    assert not a.found and a.answer == NOT_FOUND


def test_non_record_question_is_not_answered_by_records():
    a = ask("I have a headache and feel dizzy")
    assert a.intent == "not_record" and a.answer == "" and not a.found


def test_database_outage_never_guesses():
    repo = make_repo()
    svc = make_svc(repo)
    repo.fail_reads = True
    a = ask("What medicine do I use for my nebulizer?", svc)
    assert a.answer == qa.DB_DOWN and not a.found


def test_had_before_needs_topic_then_uses_hint():
    a = ask("Did I have this problem before?")
    assert not a.found and "Which symptom" in a.answer
    b = ask("Did I have this problem before?", topic_hint="cough")
    assert b.found and "12 September" in b.answer


def test_llm_polish_is_validated_against_the_grounded_answer():
    svc = make_svc()
    base = ask("What medicine did the doctor give me for my nebulizer?", svc)
    good = lambda s, u: "Your record shows Budecort 0.5 mg was prescribed for nebulization on 18 September, twice daily."
    bad_dose = lambda s, u: "Take Budecort 1 mg three times a day."
    bad_new_drug = lambda s, u: "Your record shows Budecort 0.5 mg and also Azithromycin on 18 September."
    assert ask("What medicine did the doctor give me for my nebulizer?", svc, use_llm=True, llm=good).used_llm
    for bad in (bad_dose, bad_new_drug, lambda s, u: (_ for _ in ()).throw(RuntimeError("llm down"))):
        r = ask("What medicine did the doctor give me for my nebulizer?", svc, use_llm=True, llm=bad)
        assert not r.used_llm and r.answer == base.answer


def test_validator_rejects_a_reword_that_drops_a_date_or_the_unverified_caveat():
    base = "Your record shows that Budecort 0.5 mg was prescribed for nebulization on 18 September. It's marked as active in your record."
    assert not qa.validate_llm_output("Your record shows Budecort 0.5 mg was prescribed for nebulization, and it's marked as active.", base)   # date dropped
    assert not qa.validate_llm_output("Your record shows Budecort was prescribed for nebulization on 18 September, it's marked as active.", base)  # strength dropped
    assert qa.validate_llm_output("Your record shows Budecort 0.5 mg was prescribed for nebulization on 18 September, and it's marked as active.", base)
    unv = "A prescription document uploaded on 19 September lists Montelukast 10 mg, but a clinician hasn't verified it yet."
    assert not qa.validate_llm_output("A prescription document uploaded on 19 September lists Montelukast 10 mg.", unv)                          # caveat dropped


def test_what_was_prescribed_at_the_last_consultation_is_a_medication_question_not_a_date_answer():
    a = ask("What was prescribed during the last consultation?")
    assert a.intent == "consultation_prescription" and a.found
    assert "Budecort 0.5 mg" in a.answer and "aren't linked to a specific consultation" in a.answer
    assert a.sources[0]["type"] == "medication" and "last consultation on record" not in a.answer


def test_no_prescription_on_record_says_so_and_labels_otc_guidance_as_not_a_prescription():
    repo = make_repo()
    repo.prescriptions, repo.items, repo.medications = [], [], []
    a = ask("What was prescribed during the last consultation?", make_svc(repo))
    assert "No prescription is recorded" in a.answer and "not a prescription from a doctor" in a.answer
    assert a.sources[0]["source"] == "ai_summary"
    repo.summaries["c1"]["otc_guidance"] = []
    b = ask("What was prescribed during the last consultation?", make_svc(repo))
    assert b.answer == "No prescription is recorded for the last consultation." and not b.found


def test_consultation_topic_is_short_not_the_callers_whole_sentence():
    repo = make_repo()
    repo.summaries["c1"] = {"chief_concern": "So I have chest pains right now, my left hand is hurting and my upper back is also hurting. I feel a bit too suffocated..",
                            "symptoms": []}
    a = ask("When was my last consultation?", make_svc(repo))
    assert "It was about: I have chest pains right now." in a.answer and "suffocated" not in a.answer
    assert qa._topic({"symptoms": ["cough", "fever", "fatigue", "rash"]}) == "cough, fever, fatigue"


def test_deterministic_answers_work_without_any_llm():
    a = ask("What medicine did the doctor give me for my nebulizer?", use_llm=True, llm=None)
    assert a.found and not a.used_llm


if __name__ == "__main__":
    sys.exit(1 if run_all(dict(globals())) else 0)
