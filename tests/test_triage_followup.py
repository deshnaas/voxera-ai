#   python tests/test_triage_followup.py
#   Uses the REAL frozen emergency detector. Triage may never override it.
import sys

from _pf_fixtures import run_all
from voxera_emergency import check_emergency
from voxera_patientfetch.models import Conclusion, VoiceSignal
from voxera_patientfetch.triage import TriagePlanner, detect_topic, parse_answer


def planner(**kw):
    p = TriagePlanner(check_emergency)
    p.set_patient(True)
    return p


def run(p, *utterances):
    """Feed patient utterances; return list of steps."""
    return [p.process(u) for u in utterances]


def test_chest_gas_is_not_jumped_to_heart_attack_or_to_gas():
    p = planner()
    s = p.process("My chest feels weird because of gas.")
    assert s.kind == "ask" and s.conclusion is None                      # clarifies first
    assert "gas" not in s.text.lower() or "acidity or gas" in s.text.lower()


def test_spec_example_call_burning_no_red_flags_ends_hedged_not_diagnosed():
    p = planner()
    steps = run(p, "Hi, I have chest pain but it's more like gas and burning.")
    assert steps[0].kind == "emergency" or steps[0].kind == "ask"       # detector decides; either is legal
    if steps[0].kind == "emergency":
        return                                                          # frozen detector owns this outcome
    steps += run(p, "Yes it's happening right now.", "Mostly burning.", "No.", "No.", "No.", "It happened once before.")
    final = [s for s in steps if s.kind == "conclude"]
    assert final, [s.kind for s in steps]
    t = final[0].text
    assert "can't determine the cause" in t and "seek urgent medical care" in t
    assert "it's just gas" not in t.lower() and "heart attack" not in t.lower()
    assert final[0].conclusion in (Conclusion.GENERAL_HOME_CARE.value, Conclusion.ROUTINE_CLINICAL_REVIEW.value,
                                   Conclusion.INSUFFICIENT_INFORMATION.value)


def test_red_flag_question_is_always_asked_for_chest_before_concluding():
    p = planner()
    keys = []
    s = p.process("My chest feels a bit weird.")
    for a in ("It's burning", "not right now", "since dinner, gradually", "no", "no"):
        if s.kind != "ask":
            break
        keys.append(s.key)
        s = p.process(a)
    assert "redflags_chest" in keys


def test_never_asks_more_than_the_topic_limit_and_not_mechanically_all_ten():
    p = planner()
    s = p.process("My chest feels a bit weird.")
    n = 0
    while s.kind == "ask" and n < 20:
        n += 1
        s = p.process("I'm not sure.")
    assert n <= 6 and s.kind == "conclude"


def test_already_answered_slots_are_not_asked_again():
    p = planner()
    s = p.process("For the past hour I've had a burning feeling in my chest, it came on gradually and it's still there.")
    if s.kind == "emergency":
        return
    assert s.kind == "ask" and s.key not in ("quality", "onset", "ongoing")


def test_positive_red_flag_with_chest_never_ends_as_reassurance():
    p = planner()
    s = p.process("My chest feels weird, sort of burning.")
    while s.kind == "ask" and s.key != "redflags_chest":
        s = p.process("mild, not sure")
    assert s.kind == "ask" and s.key == "redflags_chest"
    s = p.process("Yes, I'm sweating and feel dizzy.")
    assert s.kind in ("emergency", "conclude")
    if s.kind == "conclude":
        assert s.conclusion == Conclusion.URGENT_CLINICAL_REVIEW.value
        assert "flagging this to the hospital" in s.text


def test_bare_yes_to_red_flags_asks_which_once_then_treats_as_positive():
    p = planner()
    s = p.process("My chest feels tight-ish, more like discomfort.")
    if s.kind == "emergency":
        return
    while s.kind == "ask" and s.key != "redflags_chest":
        s = p.process("mild")
    s = p.process("Yes.")
    assert s.kind == "ask" and s.key == "redflags_which"
    s = p.process("Yes.")
    assert s.kind == "conclude" and s.conclusion == Conclusion.URGENT_CLINICAL_REVIEW.value


def test_emergency_words_in_a_follow_up_answer_hand_control_to_the_frozen_detector():
    p = planner()
    p.process("My chest feels weird, I think it's gas.")
    s = p.process("Actually now I can't breathe.")
    assert s.kind == "emergency" and s.emergency is not None and s.emergency.category
    assert s.conclusion == Conclusion.EMERGENCY_ESCALATION.value


def test_left_arm_weakness_after_a_calm_start_is_still_an_emergency():
    p = planner()
    p.process("I have some stomach discomfort.")
    s = p.process("Suddenly my left arm is numb and I can't speak.")
    assert s.kind == "emergency"


def test_triage_never_declares_emergency_without_the_detector():
    # A fake detector that never fires: triage may go URGENT_CLINICAL_REVIEW but must not say EMERGENCY.
    p = TriagePlanner(lambda *a, **k: None)
    s = p.process("My chest feels heavy and I'm sweating and dizzy.")
    for _ in range(8):
        if s.kind in ("conclude", "emergency"):
            break
        s = p.process("yes I'm sweating and dizzy")
    assert s.kind == "conclude" and s.conclusion == Conclusion.URGENT_CLINICAL_REVIEW.value
    assert p.state.conclusion != Conclusion.EMERGENCY_ESCALATION.value


def test_past_resolved_chest_symptom_gets_clarification_not_emergency():
    p = planner()
    s = p.process("My chest felt weird last week but it's gone now and I feel fine.")
    assert s.kind == "ask" and s.key in ("past_features", "recurrence")
    while s.kind == "ask":
        s = p.process("no, nothing else, it's completely gone")
    assert s.kind == "conclude" and s.conclusion == Conclusion.ROUTINE_CLINICAL_REVIEW.value
    assert "worth having a doctor look" in s.text


def test_spec_sentence_past_chest_discomfort_follows_the_frozen_detector():
    # NOTE: the frozen detector treats "chest discomfort last week ... normal now" as cardiac_chest
    # (it errs toward over-triage). The detector is the authority, so triage must defer to it.
    text = "I had chest discomfort last week and feel completely normal now."
    assert check_emergency(text) is not None
    assert planner().process(text).kind == "emergency"


def test_current_chest_pressure_and_sweating_is_the_emergency_path():
    p = planner()
    s = p.process("I have chest pressure right now and I'm sweating.")
    assert s.kind == "emergency"


def test_nervous_alone_is_not_an_emergency_and_not_a_triage_topic():
    for text in ("I feel nervous.", "I am extremely scared but my symptoms are mild."):
        assert check_emergency(text) is None
        assert planner().process(text).kind == "passthrough"


def test_voice_arousal_never_escalates_by_itself():
    p = planner()
    p.set_voice(VoiceSignal(available=True, arousal_level="high", emotion_signal="high_arousal", confidence=0.9))
    s = p.process("I have mild acidity in my stomach.")
    assert s.kind in ("ask", "passthrough") and s.kind != "emergency"
    assert p.state.emergency_status == "none" and p.state.conclusion is None


def test_calm_voice_does_not_suppress_an_emergency():
    calm = VoiceSignal(available=True, arousal_level="low", emotion_signal="calm", confidence=0.9)
    assert check_emergency("I sound calm but I can't breathe.") is not None      # detector alone decides
    p = planner()
    p.set_voice(calm)
    p.process("My chest feels a bit weird.")                                    # triage becomes active
    assert p.process("Actually I can't breathe now.").kind == "emergency"


def test_high_arousal_only_softens_tone_and_moves_safety_question_first():
    p = planner()
    p.set_voice(VoiceSignal(available=True, arousal_level="high", emotion_signal="high_arousal", confidence=0.8))
    s = p.process("My chest feels a bit weird.")
    if s.kind == "ask":
        assert s.key == "redflags_chest" and s.text.startswith("I'm here with you.")


def test_stomach_flow_is_conversational():
    p = planner()
    s = p.process("My stomach is hurting.")
    assert s.kind == "ask" and s.key in ("location", "onset", "severity")
    for reply in ("Upper stomach.", "After dinner.", "Burning.", "No vomiting or fainting."):
        assert s.kind in ("ask", "conclude")
        if s.kind == "conclude":
            break
        s = p.process(reply)
    assert "?" in (s.text if s.kind == "ask" else "?")


def test_questions_are_plain_language_not_form_filling():
    from voxera_patientfetch.triage import Q
    for text in Q.values():
        assert not text.lower().startswith(("question", "rate", "on a scale")) and len(text) < 160


def test_topic_detection_and_parsing():
    assert detect_topic("my chest hurts") == "chest" and detect_topic("bad headache") == "head"
    assert detect_topic("I had a chest x-ray report") is None
    a = parse_answer("no trouble breathing but I'm a bit sweaty")
    assert "sweating" in a["red_flags"] and "trouble breathing" in a["negated_red_flags"]
    assert parse_answer("it spreads to my left arm")["radiation"] == "arm"
    assert "radiation" not in parse_answer("it doesn't go to my arm")


def test_state_round_trips_through_dict():
    p = planner()
    p.process("I have some chest discomfort, burning, since dinner.")
    d = p.state.to_dict()
    from voxera_patientfetch.models import ConversationClinicalState
    again = ConversationClinicalState.from_dict(d)
    assert again.topic == "chest" and again.quality == "burning" and again.to_dict() == d


if __name__ == "__main__":
    sys.exit(1 if run_all(dict(globals())) else 0)
