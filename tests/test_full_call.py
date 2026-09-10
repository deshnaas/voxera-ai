# End-to-end local call test (no microphone, no speakers).
#
# Drives voxera.Call through a scripted conversation:
#   * STT is stubbed to replay a transcript list
#   * playback is stubbed to silent no-ops
#   * LLM + emergency layer + Supabase writes are REAL
#
# Verifies: within-call memory, conversation_turns persisted, emergency
# escalation creates a referral + referral_event, call closed as
# emergency_escalated, latency recorded.  Cleans up all rows it creates.
#
#   python tests/test_full_call.py

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import voxera_core as vx
import voxera_supabase as db
import voxera


SCRIPT = [
    "Hi, my name is Ravi and I've had a fever since yesterday.",
    "It's about a hundred and two and I feel really tired.",
    "No, I'm not having any trouble breathing.",
    "Wait, actually my chest is really tight and heavy now.",   # -> EMERGENCY
]


class FakeMic:
    def __init__(self, n):
        self.calls = 0
        self.n = n

    def calibrate(self):
        return 0.006, 0.004

    def capture_utterance(self, prime_chunks=None):
        self.calls += 1
        return np.zeros(int(vx.SAMPLE_RATE * 1.2), dtype=np.float32)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main():
    vx.load_stt()
    vx.load_tts()
    vx.prewarm_phrases(voxera.CANNED_RESPONSES + [voxera.GREETING])

    # --- stubs ---------------------------------------------------------
    script = list(SCRIPT)

    def fake_transcribe(audio, tracker=None):
        if tracker:
            tracker.mark("stt_start")
            tracker.mark("stt_end")
        return script.pop(0) if script else ""

    real_speak = vx.speak

    def fake_speak(text, tracker=None, allow_barge_in=False, mic=None):
        # still synthesize (proves TTS works) but don't touch the speakers
        a = vx.synthesize(text, tracker=tracker)
        if tracker:
            tracker.mark("tts_end")
            tracker.mark("tts_first_audio")
        return {"ok": a is not None, "interrupted": False, "pending_audio": None}

    vx.transcribe = fake_transcribe
    voxera.vx.transcribe = fake_transcribe
    vx.speak = fake_speak
    voxera.vx.speak = fake_speak

    # --- drive the call ---------------------------------------------
    call = voxera.Call()
    call.open_supabase_call()
    assert call.call_id, "no Supabase call created"

    mic = FakeMic(len(SCRIPT))
    call.mem.add_assistant(voxera.GREETING)
    call.persist_turn("ai", voxera.GREETING)

    for _ in range(len(SCRIPT)):
        call.run_turn(mic)
        if call.state == voxera.S.EMERGENCY:
            break

    call.writer.drain(8.0)

    # --- assertions ----------------------------------------------
    tx = call.mem.transcript().lower()
    assert "ravi" in tx, "lost patient name across turns"
    assert call.state == voxera.S.EMERGENCY, f"expected EMERGENCY, got {call.state}"
    assert call.emergency_count == 1

    turns = db.get_conversation(call.call_id)
    speakers = [t["speaker"] for t in turns]
    assert speakers.count("patient") >= 4, speakers
    assert speakers.count("ai") >= 4, speakers
    print(f"ok  {len(turns)} conversation_turns persisted")

    time.sleep(1.5)   # allow async escalation to land
    refs = (db.supabase.table("referrals").select("*")
            .ilike("notes", f"%{call.call_id}%").execute().data)
    assert refs, "no referral created by escalation"
    ref = refs[0]
    # dashboard convention: lowercase
    assert ref["status"] == "pending", ref["status"]
    assert ref["urgency"] in ("high", "emergency"), ref["urgency"]
    evs = (db.supabase.table("referral_events").select("*")
           .eq("referral_id", ref["id"]).execute().data)
    assert evs, "no referral_event"
    ecase = (db.supabase.table("emergency_cases").select("id")
             .eq("referral_id", ref["id"]).execute().data)
    note = (db.supabase.table("referral_notifications").select("id")
            .eq("referral_id", ref["id"]).execute().data)
    assert ecase, "no emergency_case for the dashboard Emergency page"
    assert note, "no referral_notification for the dashboard realtime popup"
    print(f"ok  escalation -> referral(pending) {ref['id']} + {len(evs)} event(s) "
          f"+ emergency_case + realtime notification")

    call.close_supabase_call("emergency_escalated")
    final = (db.supabase.table("calls").select("status,outcome")
             .eq("id", call.call_id).execute().data[0])
    assert final["outcome"] == "emergency_escalated", final
    print(f"ok  call closed: {final}")

    call.write_call_summary("emergency_escalated")
    st = [t for t in db.get_conversation(call.call_id)
          if t["speaker"] == "system" and t["message"].startswith("CALL_SUMMARY")]
    cs = []
    try:
        cs = (db.supabase.table("call_summaries").select("id")
              .eq("call_id", call.call_id).execute().data)
    except Exception:
        pass
    assert st or cs, "no call summary persisted"
    print("ok  call summary persisted")

    rec = call.tracker.turns[-1]
    assert rec["turn_total"] and rec["turn_total"] > 0
    print(f"ok  latency recorded ({len(call.tracker.turns)} turns)")
    print(call.tracker.summary())

    print("\nFULL CALL TEST: PASS")
    return call.call_id, ref["id"], (cs[0]["id"] if cs else None)


def cleanup(call_id, ref_id, summary_id):
    sb = db.supabase
    try:
        if summary_id:
            sb.table("call_summaries").delete().eq("id", summary_id).execute()
        if ref_id:
            for tbl in ("referral_notifications", "emergency_cases",
                        "referral_events"):
                sb.table(tbl).delete().eq("referral_id", ref_id).execute()
            r = sb.table("referrals").select("ai_assessment_id").eq(
                "id", ref_id).execute().data
            aid = r[0]["ai_assessment_id"] if r else None
            sb.table("referrals").delete().eq("id", ref_id).execute()
            if aid:
                sb.table("ai_assessments").delete().eq("id", aid).execute()
        if call_id:
            sb.table("ai_assessments").delete().eq("call_id", call_id).execute()
            sb.table("conversation_turns").delete().eq("call_id", call_id).execute()
            sb.table("calls").delete().eq("id", call_id).execute()
        print("cleanup: ok")
    except Exception as e:
        print(f"cleanup warning: {e}")


if __name__ == "__main__":
    cid = rid = sid = None
    try:
        cid, rid, sid = main()
        code = 0
    except Exception as e:
        print(f"\nFAILED: {e!r}")
        code = 1
    finally:
        cleanup(cid, rid, sid)
    sys.exit(code)
