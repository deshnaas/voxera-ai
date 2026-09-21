#   python tests/test_call_integration.py
#   Drives the REAL voxera.Call turn loop with scripted STT, silent TTS, a stubbed LLM and an in-memory
#   record store. No microphone, speakers, Supabase or Ollama needed. The frozen emergency detector is real.
import os
import sys


import numpy as np

from _pf_fixtures import make_repo, run_all
import voxera_core as vx
import voxera
from voxera_patientfetch import identity as ident
from voxera_patientfetch.call_integration import CallAssistant, NO_RECORD_QUESTION, NUDGE


_ORIG = (vx.transcribe, vx.speak, vx.llm_respond)
_ORIG_ENV = os.environ.get("VOXERA_VOICE_SIGNAL")


def teardown_module(_m=None):
    """Undo the global monkeypatches/env so other test modules in the same process are unaffected."""
    vx.transcribe, vx.speak, vx.llm_respond = _ORIG
    voxera.vx.transcribe, voxera.vx.speak, voxera.vx.llm_respond = _ORIG
    if _ORIG_ENV is None:
        os.environ.pop("VOXERA_VOICE_SIGNAL", None)
    else:
        os.environ["VOXERA_VOICE_SIGNAL"] = _ORIG_ENV


class FakeMic:
    def capture_utterance(self, prime_chunks=None):
        return np.zeros(int(vx.SAMPLE_RATE * 1.2), dtype=np.float32)


class Harness:
    def __init__(self, script, caller_phone="9990001111", repo=None, ask_first=True):
        os.environ["VOXERA_VOICE_SIGNAL"] = "0"          # no model worker in these unit tests (restored in teardown)
        self.spoken, self.llm_calls = [], []
        self.script = list(script)
        vx.transcribe = lambda audio, tracker=None: (self.script.pop(0) if self.script else "")
        voxera.vx.transcribe = vx.transcribe

        def speak(text, tracker=None, allow_barge_in=False, mic=None):
            self.spoken.append(text)
            return {"ok": True, "interrupted": False, "pending_audio": None}
        vx.speak = speak
        voxera.vx.speak = speak

        def llm(sysp, hist, u, tracker=None, temperature=0.3, max_tokens=64):
            self.llm_calls.append(u)
            return "Okay."
        vx.llm_respond = llm
        voxera.vx.llm_respond = llm

        self.call = voxera.Call()
        self.call.writer = None
        self.call.call_id = None
        self.call.patient = {"id": "placeholder", "full_name": "Placeholder"}
        self.call.patient_id = "placeholder"
        self.repo = repo or make_repo()
        self.call.pf = CallAssistant(db=None, caller_phone=caller_phone, repo=self.repo, ask_first=ask_first)
        self.call.pf.rebind_call = lambda call_id: None if not self.call.pf.patient else self.call.pf.patient["id"]
        self.mic = FakeMic()

    def turn(self):
        self.call.run_turn(self.mic)
        return self.spoken[-1] if self.spoken else None

    def run(self, n):
        return [self.turn() for _ in range(n)]


def test_greeting_asks_for_the_patient_id():
    h = Harness([])
    assert h.call.pf.greeting() == ident.ASK_ID and "patient ID" in ident.ASK_ID


def test_verification_then_open_question_and_the_spoken_id_is_not_stored():
    h = Harness(["VX 421"])
    out = h.turn()
    assert "I have your record" in out and h.call.pf.verified and h.call.patient_id == "p1"
    assert not h.call.pf.awaiting_id
    assert "421" not in h.call.mem.transcript() and "[patient ID provided]" in h.call.mem.transcript()
    assert "asthma" not in out.lower() and "penicillin" not in out.lower()          # no unprompted record read-out


def test_invalid_ids_three_times_then_limited_mode_without_record_access():
    h = Harness(["VX 900", "VX 901", "VX 902", "What medicine do I use for my nebulizer?"])
    outs = h.run(4)
    assert outs[0] == ident.RETRY_ID and outs[2] == ident.GIVE_UP
    assert not h.call.pf.verified
    assert outs[3] == NO_RECORD_QUESTION and "Budecort" not in outs[3]                # never exposes a record
    assert "Budecort" not in " ".join(h.spoken)


def test_emergency_before_verification_is_escalated_immediately():
    h = Harness(["I have chest pressure right now and I'm sweating."])
    out = h.turn()
    assert h.call.state == voxera.S.EMERGENCY and h.call.emergency_count == 1
    assert "emergency" in out.lower() and not h.call.pf.verified and h.call.pf.awaiting_id
    assert h.llm_calls == []


def test_chest_gas_goes_through_clarification_not_a_diagnosis():
    h = Harness(["VX 421", "My chest feels weird because of gas.", "It's mostly burning.", "No, none of those.",
                 "Not right now, it was after dinner.", "No it doesn't spread anywhere.", "First time.", "ok"])
    outs = h.run(7)
    assert "I have your record" in outs[0]
    assert outs[1].endswith("?") and "heart attack" not in outs[1].lower()             # clarifies, no diagnosis
    assert h.llm_calls == []                                                            # triage turns are LLM-free
    final = " ".join(outs[2:])
    assert "can't determine the cause" in final and "seek urgent medical care" in final
    assert "it's just gas" not in final.lower()
    assert h.call.pf.triage.state.conclusion in ("GENERAL_HOME_CARE", "ROUTINE_CLINICAL_REVIEW", "INSUFFICIENT_INFORMATION")
    assert h.call.state != voxera.S.EMERGENCY


def test_red_flag_answer_mid_triage_hands_control_to_the_frozen_detector():
    h = Harness(["VX 421", "My chest feels weird because of gas.", "Actually now I can't breathe."])
    h.run(3)
    assert h.call.state == voxera.S.EMERGENCY and h.call.emergency_count >= 1


def test_record_question_is_answered_from_the_record_without_the_llm():
    h = Harness(["VX 421", "What medicine do I use for my nebulizer?", "What did the doctor say about my cough?",
                 "Do I have diabetes?"])
    outs = h.run(4)
    assert "Budecort 0.5 mg" in outs[1] and "nebulization" in outs[1]
    assert "viral cough" in outs[2]
    assert outs[3] == "I couldn't find that information in your available records."
    assert h.llm_calls == []


def test_phone_mismatch_lowers_assurance_and_blocks_record_readout():
    h = Harness(["VX 422", "What was my previous prescription?", "My stomach is hurting."], caller_phone="9990001111")
    outs = h.run(3)
    assert h.call.pf.verified and h.call.pf.assurance == "low"
    assert outs[1] == ident.LOW_ASSURANCE
    assert outs[2].endswith("?")                                                      # triage still helps


def test_complaint_before_id_is_kept_and_not_repeated():
    h = Harness(["Hi, I have a bad headache since this morning.", "VX 421"])
    first = h.turn()
    assert first == NUDGE
    n_before = len(h.spoken)
    h.turn()
    spoken_now = h.spoken[n_before:]
    assert any("I have your record" in s for s in spoken_now)
    assert len(spoken_now) >= 2 and spoken_now[-1].endswith("?")                      # went straight on to clarifying


def test_asked_three_times_for_an_id_and_declined_continues_without_record():
    h = Harness(["I do not remember any number right now honestly", "I really do not know it at all please",
                 "Still no idea about that number sorry"])
    h.run(3)
    assert not h.call.pf.verified and not h.call.pf.awaiting_id and h.call.pf.nudges == 3


def test_verified_allergies_feed_the_otc_safety_checks():
    h = Harness(["VX 421", "hello there"])
    h.turn()
    h.call.pf._ctx_ready.wait(3)
    h.call.pf.seed_facts(h.call.mem.facts)
    assert "Penicillin" in h.call.mem.facts["allergies"] and "Asthma" in h.call.mem.facts["conditions"]


def test_summary_extras_carry_record_and_clinical_context_but_no_raw_audio_or_diagnosis():
    h = Harness(["VX 421", "My chest feels weird because of gas.", "burning"])
    h.run(3)
    extra = h.call.pf.summary_extras()
    assert extra["record_context"]["patient_verified"] is True
    assert "clinical_context" in extra and extra["clinical_context"]["chief_complaint"] == "chest discomfort"
    blob = str(extra).lower()
    for bad in ("panic", "anxiety", "diagnos", "audio"):
        assert bad not in blob


def test_pf_off_means_the_classic_greeting_and_flow():
    h = Harness(["Thanks, how are you doing today?"])
    h.call.pf = None
    out = h.turn()
    assert out == "Okay." and h.llm_calls == ["Thanks, how are you doing today?"]
    assert h.call.state != voxera.S.EMERGENCY


def test_layer_is_skipped_when_the_migration_has_not_been_run():
    class BrokenDB:
        class supabase:                                         # noqa: N801
            @staticmethod
            def table(_):
                raise RuntimeError("column patients.patient_id does not exist (42703)")
    assert CallAssistant(BrokenDB).enabled is False


if __name__ == "__main__":
    code = 1 if run_all(dict(globals())) else 0
    teardown_module()
    sys.exit(code)


# ---------------------------------------------------------------- the ID is asked only when the record is needed
def test_default_greeting_does_not_ask_for_an_id_and_general_help_needs_none():
    h = Harness(["My stomach is hurting."], ask_first=False)
    assert h.call.pf.greeting() == ident.GREETING_NO_ID and not h.call.pf.awaiting_id
    h.turn()
    assert not h.call.pf.awaiting_id and not h.call.pf.verified
    assert ident.ASK_ID_FOR_RECORD not in h.spoken


def test_a_record_question_asks_for_the_id_then_answers_that_question():
    h = Harness(["What medicine do I use for my nebulizer?", "VX 421"], ask_first=False)
    outs = h.run(2)
    assert outs[0] == ident.ASK_ID_FOR_RECORD
    assert h.call.pf.verified and not h.call.pf.awaiting_id
    assert any("Budecort 0.5 mg" in s for s in h.spoken[1:])


def test_declining_the_id_does_not_loop_the_request():
    h = Harness(["Which scans did the doctor order for me?", "I would rather not say anything about that at all",
                 "I really don't want to give it to you now", "no no no I will not tell you that thing"], ask_first=False)
    h.run(4)
    asked = [s for s in h.spoken if s == ident.ASK_ID_FOR_RECORD]
    assert len(asked) == 1 and h.call.pf.id_declined and not h.call.pf.verified


# ---------------------------------------------------------------- the ID is asked at the END and the call is filed
def test_goodbye_asks_for_the_id_files_the_call_under_that_patient_and_hangs_up():
    h = Harness(["My stomach is hurting.", "No, that's all. Thank you.", "VX 421"], ask_first=False)
    outs = h.run(3)
    assert outs[1] == ident.ASK_ID_END and h.call.closing
    assert outs[2] == ident.SAVED_GOODBYE and h.call.finished and h.call.pf.verified


def test_declining_the_id_at_the_end_just_says_goodbye():
    h = Harness(["I have a headache.", "Bye.", "No."], ask_first=False)
    outs = h.run(3)
    assert outs[1] == ident.ASK_ID_END and outs[2] == ident.GOODBYE and h.call.finished and not h.call.pf.verified


def test_a_verified_caller_is_not_asked_again_at_the_end():
    h = Harness(["What medicine do I use for my nebulizer?", "VX 421", "That's all, thanks."], ask_first=False)
    h.run(3)
    assert ident.ASK_ID_END not in h.spoken and h.spoken[-1] == ident.SAVED_GOODBYE and h.call.finished
