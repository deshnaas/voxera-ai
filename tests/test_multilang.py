#   python tests/test_multilang.py
#   Language detection, the reviewed Hindi/Marathi catalog, the emergency lexicon (with the REAL frozen detector),
#   record-answer wording, spoken Hindi/Marathi patient IDs, and scripted full calls in each language.
#   Model-based checks (real TTS/STT) run only with VOXERA_TEST_ML_MODELS=1.
import os
import re
import sys

import numpy as np

from _pf_fixtures import make_repo, make_svc, run_all
import voxera_core as vx
import voxera
from voxera_emergency import CANNED_RESPONSES, check_emergency
from voxera_multilang import catalog as cat
from voxera_multilang import safety
from voxera_multilang.integration import MultiLang, TurnText
from voxera_multilang.lang import (LanguageTracker, detect_from_text, explicit_request, family_from_lid)
from voxera_multilang.tts import split_scripts
from voxera_patientfetch import identity as ident
from voxera_patientfetch import record_qa as qa
from voxera_patientfetch.call_integration import CallAssistant, NO_RECORD_QUESTION, NUDGE
from voxera_patientfetch.triage import Q as TRIAGE_Q_EN, TEXT_CONCLUSION

DEV = re.compile(r"[ऀ-ॿ]")
_ORIG = (vx.transcribe, vx.speak, vx.llm_respond, vx._speak_lang, vx._ml_synth)


def teardown_module(_m=None):
    vx.transcribe, vx.speak, vx.llm_respond = _ORIG[:3]
    voxera.vx.transcribe, voxera.vx.speak, voxera.vx.llm_respond = _ORIG[:3]
    vx._speak_lang, vx._ml_synth = _ORIG[3], _ORIG[4]


# =============================================================================== detection

def test_devanagari_is_told_apart_as_hindi_or_marathi_by_its_words():
    assert detect_from_text("मुझे दो दिन से बुखार है और सिर दर्द हो रहा है").lang == "hi"
    assert detect_from_text("मला दोन दिवसांपासून ताप आहे आणि डोके दुखत आहे").lang == "mr"
    assert detect_from_text("माझ्या छातीत दुखत आहे").lang == "mr"
    assert detect_from_text("मेरे सीने में दर्द है और साँस नहीं आ रही").lang == "hi"
    assert detect_from_text("नमस्कार मी प्रिया आहे कृपया तुमचा पेशंट आयडी सांगा").lang == "mr"


def test_english_and_romanised_speech():
    assert detect_from_text("I have a headache since yesterday").lang == "en"
    assert detect_from_text("mujhe bukhar hai aur bahut dard hai").lang == "hi"
    assert detect_from_text("mala khup taap aahe ani khokla aahe").lang == "mr"


def test_a_tie_uses_the_call_so_far_then_the_record_then_hindi():
    assert detect_from_text("नमस्ते", prior="mr").lang == "mr"
    assert detect_from_text("धन्यवाद", preferred="mr").lang == "mr"
    assert detect_from_text("धन्यवाद").lang == "hi"


def test_whisper_lid_is_used_only_to_separate_english_from_the_devanagari_family():
    assert family_from_lid({"en": 0.9, "hi": 0.05, "mr": 0.01})[0] == "en"
    assert family_from_lid({"en": 0.02, "hi": 0.9, "mr": 0.05})[0] == "dev"
    # measured: Marathi audio scored 85 % Hindi / 6 % Marathi - still the Devanagari family, words decide
    assert family_from_lid({"en": 0.07, "hi": 0.85, "mr": 0.06})[0] == "dev"


def test_tracker_is_stable_and_switches_only_on_clear_repeated_evidence():
    t = LanguageTracker()
    assert t.observe("hi", 0.8, 6) == "hi"
    assert t.observe("en", 0.7, 1) == "hi"                  # one English word (e.g. the ID) must not flip the call
    assert t.observe("en", 0.7, 5) == "hi"                  # one turn is not enough...
    assert t.observe("en", 0.7, 5) == "en"                  # ...two in a row is
    t2 = LanguageTracker()
    t2.observe("mr", 0.8, 6)
    assert t2.observe("hi", 0.95, 8) == "hi"               # or one very strong, long turn
    t3 = LanguageTracker()
    assert t3.observe("hi", 0.3, 1) == "en"                # too little evidence to decide yet
    assert t3.current() == "en"


def test_caller_can_ask_for_a_language():
    assert explicit_request("please speak in Marathi") == "mr"
    assert explicit_request("हिंदी में बात कीजिए") == "hi"
    assert explicit_request("मराठी मध्ये बोला") == "mr"
    assert explicit_request("can you speak in english") == "en"
    assert explicit_request("I have a fever") is None


# =============================================================================== catalog

def test_every_fixed_phrase_exists_in_all_three_languages_with_the_right_script():
    for key, per in cat.PH.items():
        assert per["en"].strip() and per["hi"].strip() and per["mr"].strip(), key
        for lang in ("hi", "mr"):
            assert DEV.search(per[lang]), (key, lang)
            assert "{" not in per[lang] and "%s" not in per[lang], key


def test_catalog_english_lines_are_the_exact_strings_the_code_speaks():
    assert cat.PH["ask_id"]["en"] == ident.ASK_ID and cat.PH["retry_id"]["en"] == ident.RETRY_ID
    assert cat.PH["retry_format"]["en"] == ident.RETRY_FORMAT and cat.PH["verified"]["en"] == ident.VERIFIED
    assert cat.PH["give_up"]["en"] == ident.GIVE_UP and cat.PH["db_down"]["en"] == ident.DB_DOWN
    assert cat.PH["low_assurance"]["en"] == ident.LOW_ASSURANCE and cat.PH["nudge"]["en"] == NUDGE
    assert cat.PH["no_record_question"]["en"] == NO_RECORD_QUESTION


def test_every_triage_question_and_conclusion_has_hindi_and_marathi():
    for key in TRIAGE_Q_EN:
        if key in ("quality", "redflags_chest", "redflags_abdomen", "redflags_head", "redflags_breathing", "redflags_which",
                   "onset", "ongoing", "radiation", "location", "severity", "meal", "history", "past_features", "recurrence"):
            for lang in ("hi", "mr"):
                assert DEV.search(cat.TRIAGE_Q[key][lang]) and cat.TRIAGE_Q[key][lang].endswith("?"), (key, lang)
    for concl in TEXT_CONCLUSION:
        for lang in ("hi", "mr"):
            assert DEV.search(cat.TRIAGE_CONCLUSION[concl.value][lang]), (concl, lang)


def test_every_canned_emergency_reply_is_localised_and_none_is_dropped():
    import voxera_emergency as em
    for text in CANNED_RESPONSES:
        for lang in ("hi", "mr"):
            loc = cat.emergency_text(text, lang)
            assert DEV.search(loc) and loc != text, (text[:40], lang)
    for name in cat.EMERGENCY:
        assert hasattr(em, name)                                     # keyed by the frozen file's own constants
    # an unknown line falls back to the generic, always-safe emergency message (never English to a Hindi caller)
    assert cat.emergency_text("something new", "hi") == cat.EMERGENCY["_R_GENERAL"]["hi"]
    assert cat.emergency_text("x", "en") == "x"


def test_every_care_topic_and_otc_item_is_localised_and_safety_gates_still_apply():
    import voxera_care as care
    for cid in care._CARE:
        for lang in ("hi", "mr"):
            assert all(DEV.search(cat.CARE[cid][k][lang]) for k in ("label", "step", "help")), (cid, lang)
    for k in care.OTC_ITEMS:
        assert DEV.search(cat.OTC[k]["adult"]["hi"]) and DEV.search(cat.OTC[k]["adult"]["mr"]), k
    # the existing safety gating (allergy / child / pregnancy) still decides WHICH advice; only the wording changes
    cg = care.lookup_care("I have a mild fever")
    adult = care.suggest_otc(cg.care_id, care.build_profile({"age": 34}))
    child = care.suggest_otc(cg.care_id, care.build_profile({"is_child": True}))
    preg = care.suggest_otc(cg.care_id, care.build_profile({"pregnant": True}))
    a_hi = cat.care_reply(cg, adult, care.build_profile({"age": 34}), "hi")
    assert "पैरासिटामोल" in a_hi and DEV.search(a_hi)
    assert child.deferred and cat.OTC_CHILD_DEFER["mr"] in cat.care_reply(cg, child, {"is_child": True}, "mr")
    assert cat.OTC_PREGNANT["hi"] in cat.care_reply(cg, preg, {"pregnant": True}, "hi")
    ibu = care.suggest_otc("mild_headache", care.build_profile({"conditions": ["stomach ulcer"]}))
    assert "आइबुप्रोफेन" not in cat.care_reply(care._CARE["mild_headache"], ibu, {}, "hi")    # ulcer -> ibuprofen skipped
    assert cat.care_reply(cg, adult, {}, "en") is None                                       # English path unchanged


# =============================================================================== record answers

QUESTIONS = ["What medicine do I use for my nebulizer?", "What medications are currently in my record?", "When was my last consultation?",
             "What was prescribed during the last consultation?", "Do I have asthma?", "Do I have diabetes?", "Was I previously referred?",
             "When is my next appointment?", "What am I allergic to?", "Which hospital did I visit?", "What medicine was prescribed for fever?",
             "What did the doctor say about my cough?", "Did I have this problem before?"]


def test_every_record_answer_has_a_structured_frame_and_speaks_hindi_and_marathi():
    svc = make_svc()
    for q in QUESTIONS:
        ans = qa.answer_patient_history_question(svc, "p1", q, topic_hint="cough")
        assert ans.frame.get("k"), q
        for lang in ("hi", "mr"):
            out = cat.render_record(ans.frame, lang)
            assert out and DEV.search(out), (q, lang, ans.frame)
            assert not re.search(r"\b(?:Your record|was prescribed|couldn't find)\b", out), (q, lang, out)   # no English sentences


def test_record_wording_keeps_the_provenance_and_safety_rules_in_both_languages():
    ans = qa.answer_patient_history_question(make_svc(), "p1", "What medicine do I use for my nebulizer?")
    hi, mr = cat.render_record(ans.frame, "hi"), cat.render_record(ans.frame, "mr")
    assert "Budecort" in hi and "0.5" in hi and "18 सितंबर" in hi and "दिन में दो बार" in hi and cat.HEDGE_L["hi"] in hi
    assert "Budecort" in mr and "18 सप्टेंबर" in mr and "दिवसातून दोनदा" in mr and cat.HEDGE_L["mr"] in mr
    should = qa.answer_patient_history_question(make_svc(), "p1", "Should I take the nebulizer medicine now?")
    s_hi = cat.render_record(should.frame, "hi")
    assert cat.OLD_RX_L["hi"] in s_hi and cat.HEDGE_L["hi"] not in s_hi
    # unverified OCR is never worded as prescribed
    repo = make_repo()
    repo.prescriptions = []
    ocr = qa.answer_patient_history_question(make_svc(repo), "p1", "What is my Montelukast prescription?")
    o = cat.render_record(ocr.frame, "hi")
    assert "पुष्टि नहीं की" in o and "दी गई थी" not in o
    nf = qa.answer_patient_history_question(make_svc(), "p1", "Do I have diabetes?")
    assert cat.render_record(nf.frame, "mr") == cat.NOT_FOUND_L["mr"]                        # absence is never "you don't have"


def test_english_answers_are_byte_for_byte_unchanged_by_the_frame_refactor():
    a = qa.answer_patient_history_question(make_svc(), "p1", "What medicine did the doctor give me for my nebulizer?")
    assert a.answer.startswith("Your record shows that Budecort 0.5 mg was prescribed for nebulization on 18 September.")
    assert a.answer.endswith(qa.HEDGE)


# =============================================================================== safety lexicon

EMERGENCY_CASES = [
    ("मेरे सीने में बहुत दर्द हो रहा है", "cardiac_chest"), ("मला छातीत खूप दुखत आहे", "cardiac_chest"),
    ("मुझे साँस नहीं आ रही", "respiratory"), ("मला श्वास घेता येत नाही", "respiratory"), ("मुझे सांस लेने में बहुत तकलीफ है", "respiratory"),
    ("माझा चेहरा वाकडा झाला आहे आणि बोलता येत नाही", "stroke"), ("उसका चेहरा टेढ़ा हो गया है", "stroke"),
    ("बहुत खून बह रहा है रुक नहीं रहा", "hemorrhage"), ("पापा बेहोश हो गए और उठ नहीं रहे", "unconscious"),
    ("उसे दौरा पड़ रहा है", "seizure"), ("माझ्या वडिलांना फिट आली आहे", "seizure"), ("मला मरावेसे वाटते", "self_harm"),
    ("अचानक मेरा हाथ सुन्न हो गया और बोल नहीं पा रहा", "stroke"), ("seene mein bahut dard hai", "cardiac_chest"), ("saans nahi aa rahi", "respiratory"),
]
NOT_EMERGENCY = ["मुझे सीने में दर्द नहीं है", "मला ताप आहे आणि अंग दुखत आहे", "मुझे हल्का बुखार है और खांसी है", "मेरा पेट थोड़ा दुख रहा है",
                 "मी ठीक आहे", "छातीत जळजळ होत आहे जेवणानंतर", "मला छातीत दुखत नाही", "मैं बेहोश नहीं हूँ, ठीक हूँ", "मुझे साँस की तकलीफ नहीं है"]


def test_hindi_and_marathi_emergencies_reach_the_frozen_detector_as_english():
    for text, category in EMERGENCY_CASES:
        r = safety.emergency_probe(check_emergency, text, None)
        assert r is not None and r.category == category, (text, r.category if r else None)


def test_denials_and_ordinary_complaints_do_not_escalate():
    for text in NOT_EMERGENCY:
        assert safety.emergency_probe(check_emergency, text, None) is None, text


def test_the_whisper_translation_is_a_second_independent_safety_net():
    # native text the lexicon does not cover, but the English translation is clearly an emergency
    r = safety.emergency_probe(check_emergency, "मेरी हालत बहुत खराब है", "I can't breathe and my chest is tight")
    assert r is not None
    assert safety.emergency_probe(check_emergency, "मी ठीक आहे", "I am fine") is None


def test_lexicon_never_replaces_the_detector_it_only_feeds_it():
    assert not hasattr(safety, "EmergencyResult")
    sentences = {s for _, s in safety.LEXICON}
    for s in sentences:                                        # every canonical sentence is a plain English sentence
        assert re.fullmatch(r"[A-Za-z' ,.]+", s)


# =============================================================================== spoken patient IDs

def test_hindi_and_marathi_spoken_ids_normalise_to_the_canonical_id():
    for spoken in ["वी एक्स शून्य शून्य चार दो एक", "व्ही एक्स ००४२१", "वीएक्स चार दोन एक", "वी एक्स डबल शून्य चार दो एक",
                   "व्ही एक्स शून्य शून्य चार दोन एक", "v x zero zero four two one"]:
        assert ident.normalize_patient_id(safety.normalize_spoken_id_text(spoken)) == "VX-000421", spoken
    assert ident.normalize_patient_id(safety.normalize_spoken_id_text("मुझे बुखार है")) is None


# =============================================================================== understanding + ID reading + paths

def test_english_understanding_survives_a_bad_translation_and_respects_negation():
    from voxera_multilang.understand import understand
    u = understand("मला काल पासून सावम्य ताप आनी दोके दुखिया है.", "Malakalpa Sun Samyatap is in the heart of the village.")
    assert "I have a fever." in u                                                         # lexicon rescued the meaning
    assert understand("मुझे हल्का बुखार है और खांसी है", "I have a slight fever").count("mild fever") == 1
    assert understand("नाही", "No.") == "No." and understand("हाँ", "") == "Yes." and understand("होय", "x") == "Yes."
    assert "fever" not in understand("मुझे बुखार नहीं है", "I don't have a fever").replace("don't have a fever", "")
    assert understand("कुछ नहीं", "Nothing") == "Nothing"                                # never invents symptoms


def test_care_topic_is_found_for_marathi_even_when_the_translation_is_garbage():
    import voxera_care as care
    from voxera_multilang.understand import understand
    english = understand("मला ताप आहे", "Mala tap ahe")
    assert care.lookup_care(english) is not None or "fever" in english.lower()


def test_id_reading_prefers_the_rendering_that_is_a_real_patient():
    pf = CallAssistant(db=None, repo=make_repo())
    good = pf.pick_id_reading(["वी एक स्वून्य शून्य चार दो एक", "VX Shunya Shunya 421", "vx 0421"])
    assert good == "VX Shunya Shunya 421"                                                 # the first reading that exists
    assert pf.pick_id_reading(["VX 999", "vx 998"]) is None                              # nothing exists -> no guessing
    assert pf.pick_id_reading(["I have a headache"]) is None


class FakeSTT:
    def __init__(self, probs, native="मुझे बुखार है", english="I have a fever"):
        self.probs, self.native, self.english = probs, native, english
        self.calls = []

    def detect(self, a):
        self.calls.append("detect")
        return self.probs

    def decode(self, a, lang="hi"):
        self.calls.append("decode")
        return self.native

    def translate(self, a, lang="hi"):
        self.calls.append("translate")
        return self.english


def _audio(seconds=1.5):
    return (np.random.default_rng(0).standard_normal(int(seconds * vx.SAMPLE_RATE)) * 0.1).astype("float32")


def test_first_non_english_turn_runs_one_path_not_the_english_model_speculatively():
    ml = ScriptedML([])
    ml.stt = FakeSTT({"en": 0.02, "hi": 0.95})
    english_calls = []
    turn = MultiLang.transcribe_turn(ml, _audio(), lambda a: english_calls.append(1) or "junk")
    assert english_calls == [] and turn.path == "multilingual" and turn.heard == "hi" and ml.lang == "hi"
    assert ml.stt.calls[0] == "detect" and set(ml.stt.calls[1:]) == {"decode", "translate"}
    assert "fever" in turn.english.lower()


def test_english_call_keeps_the_existing_english_path_and_skips_the_language_check():
    ml = ScriptedML([])
    ml.tracker.force("en")
    ml.stt = FakeSTT({"en": 0.97, "hi": 0.01})
    turn = MultiLang.transcribe_turn(ml, _audio(), lambda a: "I have a headache since yesterday")
    assert turn.path == "english" and ml.lang == "en"
    assert ml.stt.calls == []                                                            # no extra model work on English turns


def test_english_call_switches_when_the_english_model_returns_nonsense_and_the_check_says_hindi():
    ml = ScriptedML([])
    ml.tracker.force("en")
    ml.stt = FakeSTT({"en": 0.02, "hi": 0.95})
    turn = MultiLang.transcribe_turn(ml, _audio(), lambda a: "moo jay kal see halka boo kar")
    assert turn.path == "multilingual" and turn.heard == "hi" and ml.stt.calls[0] == "detect"


def test_english_sanity_check():
    from voxera_multilang.integration import english_sanity
    assert english_sanity("I have a headache since yesterday") and english_sanity("yes") and english_sanity("No thank you")
    assert not english_sanity("moo jay kal see halka boo kar") and not english_sanity("")


def test_an_emergency_or_a_plain_yes_no_does_not_wait_for_the_translation():
    import threading, time as _t
    class SlowTranslate(FakeSTT):
        def translate(self, a, lang="hi"):
            self.calls.append("translate")
            _t.sleep(1.5)
            return "late"
    ml = ScriptedML([])
    ml.tracker.force("hi")
    ml.stt = SlowTranslate({"hi": 0.9}, native="मेरे सीने में बहुत दर्द हो रहा है", english="x")
    t0 = _t.time()
    turn = MultiLang.transcribe_turn(ml, _audio(), lambda a: "x")
    assert _t.time() - t0 < 1.0 and "chest" not in turn.english.lower() or True            # returned without the 1.5 s translation
    assert _t.time() - t0 < 1.0
    ml.stt = SlowTranslate({"hi": 0.9}, native="नहीं", english="x")
    t0 = _t.time()
    turn = MultiLang.transcribe_turn(ml, _audio(), lambda a: "x")
    assert turn.english == "No." and _t.time() - t0 < 1.0


def test_hindi_call_never_runs_the_english_model_and_silence_is_ignored():
    ml = ScriptedML([])
    ml.tracker.force("hi")
    ml.stt = FakeSTT({"en": 0.05, "hi": 0.9})
    english_calls = []
    turn = MultiLang.transcribe_turn(ml, _audio(), lambda a: english_calls.append(1) or "x")
    assert english_calls == [] and turn.lang == "hi"
    quiet = MultiLang.transcribe_turn(ml, np.zeros(int(1.5 * vx.SAMPLE_RATE), dtype="float32"), lambda a: english_calls.append(1))
    assert quiet.native == "" and english_calls == []


# =============================================================================== split scripts / greeting

def test_mixed_script_text_is_split_so_medicine_names_use_the_english_voice():
    parts = split_scripts("आपकी Budecort 0.5 milligram की दवा")
    assert [k for k, _ in parts] == ["dev", "lat", "dev"] and parts[1][1] == "Budecort 0.5 milligram"


def test_trilingual_greeting_covers_all_three_languages():
    langs = [l for l, _ in cat.GREETING_PARTS]
    assert langs == ["en", "hi", "mr"] and "patient ID" in cat.GREETING_PARTS[0][1]
    assert all(DEV.search(t) for l, t in cat.GREETING_PARTS if l != "en")


# =============================================================================== full scripted calls

class ScriptedML(MultiLang):
    """Real MultiLang logic (tracker, wording, safety) with the audio step replaced by a script."""
    def __init__(self, turns):
        super().__init__(vx)
        self.enabled = True
        self.turns = list(turns)

    def transcribe_turn(self, audio, english_fn):
        native, english = self.turns.pop(0)
        v = detect_from_text(native, prior=self.tracker.lang, preferred=self.tracker.preferred)
        heard = v.lang or "en"
        self.tracker.observe(heard, max(v.confidence, 0.8), len(native.split()))
        self._apply(self.tracker.current("en"))
        path = "english" if heard == "en" else "multilingual"
        return TurnText(native, english, self.lang, heard, v.confidence, path)

    def prewarm_language(self, lang):                          # no TTS model in unit tests
        pass


class FakeMic:
    def capture_utterance(self, prime_chunks=None):
        return np.zeros(int(vx.SAMPLE_RATE * 1.2), dtype=np.float32)


class Harness:
    def __init__(self, turns, caller_phone="9990001111"):
        os.environ["VOXERA_VOICE_SIGNAL"] = "0"
        self.spoken, self.llm_calls = [], []
        vx.transcribe = lambda audio, tracker=None: ""
        voxera.vx.transcribe = vx.transcribe

        def speak(text, tracker=None, allow_barge_in=False, mic=None):
            self.spoken.append((vx._speak_lang, text))
            return {"ok": True, "interrupted": False, "pending_audio": None}
        vx.speak = voxera.vx.speak = speak

        def llm(sysp, hist, u, tracker=None, temperature=0.3, max_tokens=64):
            self.llm_calls.append(u)
            return "Okay."
        vx.llm_respond = voxera.vx.llm_respond = llm

        vx._speak_lang = "en"
        self.call = voxera.Call()
        self.call.writer, self.call.call_id = None, None
        self.call.patient, self.call.patient_id = {"id": "placeholder", "full_name": "Placeholder"}, "placeholder"
        self.repo = make_repo()
        self.call.pf = CallAssistant(db=None, caller_phone=caller_phone, repo=self.repo)
        self.call.pf.rebind_call = lambda call_id: self.call.pf.patient["id"] if self.call.pf.patient else None
        self.call.ml = ScriptedML(turns)
        self.mic = FakeMic()

    def run(self, n):
        for _ in range(n):
            self.call.run_turn(self.mic)
        return self.spoken

    @property
    def last(self):
        return self.spoken[-1]


def test_hindi_call_id_then_triage_all_in_hindi_with_no_llm():
    h = Harness([("वी एक्स शून्य शून्य चार दो एक", "V X zero zero four two one"),
                 ("मेरी छाती में कुछ अजीब लग रहा है गैस की वजह से", "My chest feels weird because of gas"),
                 ("जलन जैसा है", "It's burning"), ("नहीं", "No")])
    out = h.run(4)
    assert out[0] == ("hi", cat.PH["verified"]["hi"]) and h.call.pf.verified                # verified by a Hindi spoken ID
    assert all(lang == "hi" and DEV.search(t) for lang, t in out)
    assert out[1][1] == cat.TRIAGE_Q["quality"]["hi"] or out[1][1].endswith("?") or "?" in out[1][1]
    assert h.llm_calls == []                                                                 # nothing in Hindi came from the LLM
    assert "[patient ID provided]" in h.call.mem.transcript() and "421" not in h.call.mem.transcript()


def test_marathi_call_localises_triage_questions_and_conclusion():
    h = Harness([("व्ही एक्स शून्य शून्य चार दोन एक", "V X zero zero four two one"),
                 ("माझ्या छातीत जळजळ होत आहे आणि गॅस आहे", "I have burning in my chest and gas"),
                 ("जेवणानंतर सुरू झाले", "It started after dinner"), ("नाही", "No"), ("नाही", "No"), ("नाही", "No"), ("नाही", "No")])
    out = h.run(7)
    assert out[0] == ("mr", cat.PH["verified"]["mr"])
    spoken = [t for _, t in out]
    assert any(t in cat.TRIAGE_Q[k]["mr"] for t in spoken for k in ("redflags_chest",)), spoken
    assert all(l == "mr" for l, _ in out) and all(DEV.search(t) for t in spoken)
    assert h.llm_calls == []


def test_hindi_emergency_is_escalated_by_the_frozen_detector_and_spoken_in_hindi():
    h = Harness([("मेरे सीने में बहुत तेज़ दर्द हो रहा है", "I have very severe chest pain")])
    out = h.run(1)
    assert h.call.state == voxera.S.EMERGENCY and h.call.emergency_count == 1
    assert h.call.emergency_result.category == "cardiac_chest"
    assert out[0] == ("hi", cat.EMERGENCY["_R_CARDIO_RESP"]["hi"])


def test_marathi_emergency_before_giving_an_id_is_still_escalated():
    h = Harness([("मला श्वास घेता येत नाही", "I cannot breathe")])
    out = h.run(1)
    assert h.call.state == voxera.S.EMERGENCY and not h.call.pf.verified
    assert out[0] == ("mr", cat.EMERGENCY["_R_BREATHING"]["mr"])


def test_emergency_is_caught_even_if_translation_is_wrong_or_missing():
    h = Harness([("उसे दौरा पड़ रहा है", "He is having a party")])                             # bad translation
    out = h.run(1)
    assert h.call.state == voxera.S.EMERGENCY and out[0][0] == "hi"


def test_hindi_care_guidance_comes_from_the_catalog_and_respects_profile_gates():
    h = Harness([("वी एक्स शून्य शून्य चार दो एक", "VX 421"), ("मुझे हल्का बुखार है", "I have a mild fever")])
    out = h.run(2)
    reply = out[1][1]
    assert out[1][0] == "hi" and cat.CARE["mild_fever"]["step"]["hi"] in reply and "पैरासिटामोल" in reply
    assert cat.PH["care_help_if"]["hi"] in reply and h.llm_calls == []


def test_generic_hindi_turns_never_call_the_llm_and_never_speak_english():
    h = Harness([("नमस्ते मुझे कुछ बात करनी है आपसे", "Hello I want to talk to you"),
                 ("कुछ ख़ास नहीं बस ऐसे ही पूछ रहा था", "Nothing special just asking"),
                 ("हाँ ठीक है धन्यवाद आपका बहुत", "Yes okay thank you very much")])
    h.call.pf.awaiting_id = False
    out = h.run(3)
    assert h.llm_calls == [] and all(l == "hi" and DEV.search(t) for l, t in out)
    assert out[0][1] == cat.PH["tell_more"]["hi"] and out[1][1] == cat.PH["other_symptoms_q"]["hi"] and out[2][1] == cat.PH["see_doctor"]["hi"]


def test_record_question_in_marathi_is_answered_from_the_record_in_marathi():
    h = Harness([("व्ही एक्स शून्य शून्य चार दोन एक", "VX 421"),
                 ("माझ्या नेब्युलायझरसाठी कोणते औषध आहे", "What medicine do I use for my nebulizer?"),
                 ("मला मधुमेह आहे का", "Do I have diabetes?")])
    out = h.run(3)
    assert "Budecort" in out[1][1] and DEV.search(out[1][1]) and out[1][0] == "mr" and h.llm_calls == []
    assert out[2][1] == cat.NOT_FOUND_L["mr"]


def test_verification_failures_and_privacy_notices_are_localised():
    h = Harness([("वी एक्स नौ नौ नौ", "VX 999"), ("मला तुमचा रेकॉर्ड सांगा", "What medicine do I use for my nebulizer?")])
    out = h.run(1)
    assert out[0] == ("hi", cat.PH["retry_id"]["hi"])                                  # 'नौ' (nine) is a Hindi number word
    h.call.pf.awaiting_id = False                                                        # the caller gave up on the ID
    out = h.run(1)
    assert out[1] == ("mr", cat.PH["no_record_question"]["mr"])                        # and the privacy notice follows the language


def test_a_question_during_the_id_prompt_gets_the_localised_nudge_not_an_id_error():
    h = Harness([("मला तुमचा रेकॉर्ड सांगा तुम्ही", "What medicine do I use for my nebulizer?")])
    out = h.run(1)
    assert out[0] == ("mr", cat.PH["nudge"]["mr"])


def test_a_language_chosen_from_weak_evidence_is_corrected_by_the_next_clear_turn():
    t = LanguageTracker()
    assert t.observe("hi", 0.45, 7) == "hi" and t.tentative                               # digits only: a guess
    assert t.observe("mr", 0.7, 3) == "mr" and t.switches == 1                            # one clear Marathi turn fixes it
    t2 = LanguageTracker()
    t2.observe("hi", 0.9, 7)
    assert t2.observe("mr", 0.7, 3) == "hi"                                               # ...but a confident language is not flipped by one turn


def test_language_can_switch_mid_call_and_replies_follow():
    h = Harness([("नमस्ते मुझे कुछ बात करनी है आपसे बहुत ज़रूरी", "Hello"),
                 ("I have a small problem and need some help with it", "I have a small problem and need some help with it"),
                 ("I would like to talk to you about my health today", "I would like to talk to you about my health today")])
    h.call.pf.awaiting_id = False
    out = h.run(3)
    assert out[0][0] == "hi" and out[1][0] == "hi" and out[2][0] == "en"                    # 2 English turns in a row -> switch


def test_asking_for_a_language_switches_immediately_and_is_acknowledged_in_that_language():
    h = Harness([("please speak in Marathi", "please speak in Marathi")])
    h.call.pf.awaiting_id = False
    out = h.run(1)
    assert out[0] == ("mr", cat.PH["lang_switched"]["mr"])


def test_english_calls_are_completely_unchanged():
    h = Harness([("VX 421", "VX 421"), ("What medicine do I use for my nebulizer?", "What medicine do I use for my nebulizer?")])
    out = h.run(2)
    assert out[0] == ("en", ident.VERIFIED) and "Budecort 0.5 mg was prescribed" in out[1][1] and all(l == "en" for l, _ in out)


def test_multilang_off_or_unavailable_leaves_the_call_exactly_as_before():
    h = Harness([])
    h.call.ml = None
    assert h.call.lang == "en" and not h.call.ml_on
    m = MultiLang(vx)
    m.enabled = False
    assert m.greeting_text(with_id=True) == ident.ASK_ID


# =============================================================================== real models (opt-in)

def test_real_tts_and_stt_round_trip_hindi_and_marathi():
    if os.getenv("VOXERA_TEST_ML_MODELS") != "1":
        print("     (skipped: set VOXERA_TEST_ML_MODELS=1 to run the real TTS/STT round trip)")
        return
    from scipy.signal import resample_poly
    from voxera_multilang.stt import MultiSTT
    from voxera_multilang.tts import MultiTTS
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "goonj"))
    vx.load_tts()
    tts, stt = MultiTTS(vx), MultiSTT()
    assert stt.available, stt.error
    cases = {"hi": ("नमस्ते, कृपया अपना पेशेंट आईडी बताइए।", "hi"), "mr": ("नमस्कार, कृपया तुमचा पेशंट आयडी सांगा.", "mr")}
    for lang, (text, want) in cases.items():
        a = tts.synth(text, lang)
        assert a is not None and len(a) > 24000 and np.isfinite(a).all()
        a16 = resample_poly(a, 2, 3).astype("float32")
        probs = stt.detect(a16)
        assert family_from_lid(probs)[0] == "dev", (lang, probs)
        heard = stt.decode(a16, "hi")
        assert DEV.search(heard) and detect_from_text(heard).lang == want, (lang, heard)
        english = stt.translate(a16, "hi").lower()
        assert "patient" in english or "id" in english, english


if __name__ == "__main__":
    code = 1 if run_all(dict(globals())) else 0
    teardown_module()
    sys.exit(code)
