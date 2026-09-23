# Everyday-symptom flow: help first, ask several questions, then conclude. No LLM.
import re

import voxera_care as care
from voxera_followup import FollowUp, detect_topics, fixed_texts, parse_duration_days, parse_severity, LABEL, Q


def advice(cid):
    cg = care._CARE[cid]
    otc = care.suggest_otc(cid, care.build_profile({}))
    text = " ".join(list(cg.steps[:1]) + ([care._first_sentence(otc.spoken)] if otc and otc.spoken else []))   # as the live call
    return text, cg


def run(cid, lang, first, answers):
    fu = FollowUp()
    text, cg = advice(cid)
    steps = [fu.start(cid, text, first, lang)]
    for a in answers:
        steps.append(fu.process(a, lang, cg.see_help_if[0] if lang == "en" else ""))
    return fu, steps


# ---------------------------------------------------------------------------- topics
def test_noisy_real_call_transcripts_still_find_the_symptoms():
    assert detect_topics("I have a fever and my head hurts very bad. My back is also feeling motivated.")[:2] == ["mild_fever", "mild_headache"]
    assert detect_topics("what do I do for my fever") == ["mild_fever"]
    assert detect_topics("I have a sore throat and a cough") == ["sore_throat", "mild_cough"]
    assert detect_topics("I have acidity after dinner") == ["mild_indigestion"]


def test_a_denied_symptom_is_not_a_symptom():
    assert detect_topics("I have no fever but a headache") == ["mild_headache"]
    assert detect_topics("I don't have a cough") == []


def test_severity_and_duration_parsing():
    assert parse_severity("it's really bad") == "severe" and parse_severity("just a little") == "mild"
    assert parse_severity("about a 8 out of 10") == "severe" and parse_severity("5") == "moderate"
    assert parse_duration_days("since yesterday") == 1 and parse_duration_days("for 2 days") == 2
    assert parse_duration_days("about a week") == 7 and parse_duration_days("three hours") == 3 / 24
    assert parse_duration_days("no idea") is None


# ---------------------------------------------------------------------------- the conversation shape
def test_help_comes_first_then_the_first_question_and_not_a_conclusion():
    fu, steps = run("mild_fever", "en", "I have a fever", [])
    s = steps[0]
    assert s.kind == "ask" and s.key == "flags_mild_fever"
    assert "Rest and drink plenty of fluids" in s.text                    # first aid / home remedy first
    assert "paracetamol" in s.text.lower()                                # then the over-the-counter option
    assert s.text.index("Rest and drink") < s.text.rstrip().rindex("?")    # advice before the question
    assert s.text.rstrip().endswith("?")


def test_at_least_three_questions_before_any_conclusion():
    fu, steps = run("mild_fever", "en", "I have a fever",
                    ["No, nothing like that.", "It's mild", "since yesterday", "no", "no"])
    kinds = [s.kind for s in steps]
    assert kinds[:4] == ["ask", "ask", "ask", "ask"] or kinds[:3] == ["ask", "ask", "ask"]
    conclude_at = kinds.index("conclude")
    assert conclude_at >= 3                                               # start + >= 2 answered questions before concluding
    asked = [s.key for s in steps if s.kind == "ask"]
    assert len(asked) >= 3 and asked[0].startswith("flags_")              # red flags are asked first


def test_known_answers_are_not_asked_again():
    fu, steps = run("mild_fever", "en", "I have a mild fever since yesterday", ["no", "no", "no"])
    keys = [s.key for s in steps if s.kind == "ask"]
    assert "severity" not in keys and "duration" not in keys


def test_manageable_case_concludes_at_home_with_help_if_and_days_rule():
    fu, steps = run("mild_fever", "en", "I have a fever", ["no", "mild", "since yesterday", "no", "no", "no"])
    c = next(s for s in steps if s.kind == "conclude")
    assert c.level == "self_care"
    assert "look after at home" in c.text and "if the fever goes above" in c.text.lower()
    assert "within 3 days" in c.text and "can't diagnose" in c.text and c.text.rstrip().endswith("?")


def test_a_red_flag_stops_the_questions_and_sends_them_to_a_doctor_today():
    fu, steps = run("mild_fever", "en", "I have a fever", ["yes, my neck is stiff"])
    c = steps[-1]
    assert c.kind == "conclude" and c.level == "urgent"
    assert "today" in c.text and "one zero eight" in c.text


def test_severe_or_long_lasting_means_see_a_doctor_soon():
    _, steps = run("mild_fever", "en", "I have a fever", ["no", "it's severe", "for 5 days", "no", "no"])
    assert next(s for s in steps if s.kind == "conclude").level == "see_soon"
    _, steps = run("mild_headache", "en", "I have a headache since morning", ["no", "very bad", "no", "no"])
    assert next(s for s in steps if s.kind == "conclude").level == "see_soon"


def test_unclear_answers_get_one_gentle_repeat_and_never_default_to_fine():
    fu, steps = run("mild_fever", "en", "I have a fever", ["hmm the weather is nice", "what?", "mild", "a day", "no", "no"])
    assert Q["repeat_yes_no"]["en"] in steps[1].text                       # asked again once
    c = next(s for s in steps if s.kind == "conclude")
    assert c.level == "see_soon" and "wasn't fully sure" in c.text          # still unsure -> safe side


def test_a_serious_extra_symptom_mentioned_in_passing_is_taken_seriously():
    _, steps = run("mild_cough", "en", "I have a cough", ["no", "mild", "two days", "yes, and I'm short of breath"])
    assert next(s for s in steps if s.kind == "conclude").level == "urgent"
    _, steps = run("mild_cough", "en", "I have a cough", ["no", "mild", "two days", "no, no trouble breathing"])
    assert next(s for s in steps if s.kind == "conclude").level != "urgent"


def test_two_symptoms_the_second_is_offered_after_the_first():
    fu = FollowUp()
    text, cg = advice("mild_fever")
    fu.start("mild_fever", text, "I have a fever and a headache", "en", others=["mild_headache"])
    out = None
    for a in ["no", "mild", "since yesterday", "no", "no"]:
        out = fu.process(a, "en", cg.see_help_if[0])
        if out.kind == "conclude":
            break
    assert out.kind == "conclude" and "headache" in out.text and out.text.rstrip().endswith("?")
    assert fu.active
    nxt = fu.process("yes please", "en")
    assert nxt.kind == "start_next" and nxt.care_id == "mild_headache"
    fu2 = FollowUp()
    fu2.start("mild_fever", text, "fever and headache", "en", others=["mild_headache"])
    for a in ["no", "mild", "since yesterday", "no", "no"]:
        o = fu2.process(a, "en", "x")
        if o.kind == "conclude":
            break
    assert fu2.process("no thanks", "en").kind == "conclude" and not fu2.active


def test_no_diagnosis_words_and_no_numeric_doses_are_ever_spoken():
    for cid in care._CARE:
        fu, steps = run(cid, "en", "I have it", ["no", "mild", "two days", "no", "no"])
        blob = " ".join(s.text for s in steps).lower()
        assert not re.search(r"\b\d+\s?(mg|ml|tablet|tablets)\b", blob), cid
        assert not re.search(r"you (have|probably have|might have) (a |an )?(heart attack|infection|virus|cancer|stroke)", blob), cid


# ---------------------------------------------------------------------------- languages
def test_hindi_and_marathi_run_the_same_flow_in_their_own_script():
    dev = re.compile(r"[ऀ-ॿ]")
    for lang in ("hi", "mr"):
        fu, steps = run("mild_fever", lang, "I have a fever", ["No.", "mild", "since yesterday", "No.", "No."])
        first, last = steps[0], next(s for s in steps if s.kind == "conclude")
        assert dev.search(first.text) and dev.search(last.text)
        assert first.text.rstrip().endswith("?") and "one zero" not in last.text
        assert not re.search(r"\b(Rest|Okay|fever)\b", last.text)


def test_every_question_and_line_exists_in_all_three_languages():
    for k, v in Q.items():
        assert set(v) == {"en", "hi", "mr"} and all(v.values()), k
    for lang in ("en", "hi", "mr"):
        assert len(fixed_texts(lang)) > 40
    assert set(LABEL) == set(care._CARE)


def test_very_high_fever_is_severe_and_no_nothing_else_is_an_answer_not_a_goodbye():
    from voxera_followup import parse_severity
    from voxera_patientfetch.closing import is_closing
    assert parse_severity("I have a very high fever") == "severe" and parse_severity("really high") == "severe"
    assert is_closing("No, nothing else.") and not is_closing("No, nothing else.", strict=True)   # a question is waiting
    assert is_closing("bye", strict=True) and is_closing("Goodbye, thanks", strict=True)
    fu, steps = run("mild_fever", "en", "I have a very high fever", [])
    assert "severity" not in [s.key for s in steps if s.kind == "ask"]                             # already told us
    assert len(steps[0].text.split()) < 75                                                       # phone-length first reply


def test_conclusion_sentences_are_properly_punctuated():
    _, steps = run("mild_fever", "en", "I have a fever", ["no", "mild", "since yesterday", "no", "no"])
    c = next(s for s in steps if s.kind == "conclude").text
    assert "days. " in c or "days." in c
    assert not re.search(r"[a-z] I can't diagnose", c)
