# Integration test for the Voxera Supabase layer against the SHARED
# hospital-dashboard project.
# Exercises: connection, patient/call (patient_id link), conversation turns,
# full emergency escalation (ai_assessments + referrals + referral_events +
# emergency_cases + referral_notifications), appointment create/confirm,
# and end-of-call summary persistence. Cleans up everything it creates.
#
#   python tests/test_supabase_layer.py

import os
import sys
import uuid
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voxera_supabase as db
import voxera_summary as summary

created = {"turns_call": None, "call": None, "patient": None,
           "referral": None, "appointment": None, "assessment": None,
           "emergency_case": None, "notification": None, "summary": None}


def main():
    print("=" * 62)
    print("VOXERA <-> DASHBOARD SUPABASE LAYER TEST")
    print("=" * 62)

    assert db.test_connection(), "connection failed"
    print("ok  connection")

    phone = "9" + str(uuid.uuid4().int)[-9:]
    patient = db.create_patient("Voxera Layer Test", phone, "English")
    created["patient"] = patient["id"]
    print(f"ok  patient {patient['id']}")

    call = db.create_call(patient_id=patient["id"], language="English",
                          call_type="initial_assessment")
    created["call"] = call["id"]
    created["turns_call"] = call["id"]
    assert call["patient_id"] == patient["id"], call
    print(f"ok  call {call['id']}  (patient_id linked)")

    db.save_turn(call["id"], "patient", "My chest feels really tight and heavy.")
    db.save_turn(call["id"], "ai", "That could be an emergency. Please get help now.")
    turns = db.get_recent_conversation(call["id"], limit=8)
    assert len(turns) == 2 and turns[0]["speaker"] == "patient", turns
    print(f"ok  conversation turns persisted ({len(turns)})")

    # --- full emergency escalation --------------------------------
    referral = db.escalate_emergency(
        call_id=call["id"], patient_name="Voxera Layer Test",
        trigger_text="chest feels really tight and heavy",
        category="cardiac_chest", recommended_department="Cardiology",
        patient_id=patient["id"],
        conversation_summary="Test escalation from test_supabase_layer.py",
        immediate_action="Call emergency services now.",
    )
    assert referral and referral.get("id"), referral
    created["referral"] = referral["id"]
    # dashboard convention: lowercase
    assert referral["status"] == "pending", referral["status"]
    assert referral["urgency"] in ("high", "emergency"), referral["urgency"]
    assert referral.get("ai_assessment_id"), "referral not linked to ai_assessment"
    created["assessment"] = referral["ai_assessment_id"]

    ev = (db.supabase.table("referral_events").select("*")
          .eq("referral_id", referral["id"]).execute().data)
    assert ev, "no referral_event"

    ecase = (db.supabase.table("emergency_cases").select("*")
             .eq("referral_id", referral["id"]).execute().data)
    assert ecase, "no emergency_case row for the Emergency dashboard page"
    created["emergency_case"] = ecase[0]["id"]
    assert ecase[0]["status"] == "active"

    note = (db.supabase.table("referral_notifications").select("*")
            .eq("referral_id", referral["id"]).execute().data)
    assert note, "no referral_notification (dashboard realtime popup)"
    created["notification"] = note[0]["id"]

    asr = (db.supabase.table("ai_assessments").select("*")
           .eq("id", referral["ai_assessment_id"]).execute().data)
    assert asr and asr[0]["call_id"] == call["id"], asr
    print(f"ok  escalation -> ai_assessment + referral(pending) + {len(ev)} event(s) "
          f"+ emergency_case(active) + referral_notification")

    # --- appointment (dashboard lowercase statuses) --------------
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    appt = db.create_appointment(
        patient_name="Voxera Layer Test", appointment_date=tomorrow,
        appointment_time="10:00:00", department="General Medicine",
        doctor_name="Dr. Test", reason="layer test", patient_id=patient["id"],
        status="scheduled",
    )
    created["appointment"] = appt["id"]
    db.update_appointment(appt["id"], status="confirmed")
    got = db.get_appointment(appt["id"])
    assert got["status"] == "confirmed", got
    print(f"ok  appointment {appt['id']} scheduled -> confirmed")

    db.update_call_metrics(call["id"], ai_response_count=1,
                           emergency_checks_count=1, call_success=True)
    print("ok  call metrics update")

    # --- end-of-call structured summary --------------------------
    turns2 = db.get_conversation(call["id"])
    s = summary.build_summary(
        patient=patient,
        facts={"symptoms": ["chest tightness"], "age": 55},
        transcript="PATIENT: my chest feels really tight and heavy",
        turns=turns2,
        emergency=type("E", (), {"category": "cardiac_chest",
                                 "severity": "emergency",
                                 "trigger": "chest feels really tight and heavy",
                                 "recommended_department": "Cardiology"})(),
        referral=referral, outcome="emergency_escalated",
    )
    assert s["emergency_status"]["detected"] is True
    assert s["chief_concern"].lower().startswith("my chest feels")
    text = summary.render_text(s)
    assert "OTC only" in text and "CALL SUMMARY" in text
    summary.persist_summary(db, call["id"], patient["id"], s, text)
    # it landed either in call_summaries or as a system turn
    cs = []
    try:
        cs = (db.supabase.table("call_summaries").select("id")
              .eq("call_id", call["id"]).execute().data)
        if cs:
            created["summary"] = cs[0]["id"]
    except Exception:
        pass
    sys_turn = [t for t in db.get_conversation(call["id"])
                if t["speaker"] == "system" and t["message"].startswith("CALL_SUMMARY")]
    assert cs or sys_turn, "summary was not persisted anywhere"
    print("ok  call summary persisted "
          + ("(call_summaries table)" if cs else "(system conversation_turn fallback)"))

    print("\nALL CHECKS PASSED")


def cleanup():
    sb = db.supabase
    try:
        if created["summary"]:
            sb.table("call_summaries").delete().eq("id", created["summary"]).execute()
        if created["notification"]:
            sb.table("referral_notifications").delete().eq(
                "id", created["notification"]).execute()
        if created["emergency_case"]:
            sb.table("emergency_cases").delete().eq(
                "id", created["emergency_case"]).execute()
        if created["referral"]:
            sb.table("referral_events").delete().eq(
                "referral_id", created["referral"]).execute()
            sb.table("referrals").delete().eq("id", created["referral"]).execute()
        if created["assessment"]:
            sb.table("ai_assessments").delete().eq(
                "id", created["assessment"]).execute()
        if created["appointment"]:
            sb.table("appointments").delete().eq(
                "id", created["appointment"]).execute()
        if created["turns_call"]:
            sb.table("conversation_turns").delete().eq(
                "call_id", created["turns_call"]).execute()
        if created["call"]:
            sb.table("calls").delete().eq("id", created["call"]).execute()
        if created["patient"]:
            sb.table("patients").delete().eq("id", created["patient"]).execute()
        print("cleanup: ok")
    except Exception as e:
        print(f"cleanup warning: {e}")


if __name__ == "__main__":
    try:
        main()
        code = 0
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        code = 1
    except Exception as e:
        print(f"\nERROR: {e!r}")
        code = 1
    finally:
        cleanup()
    sys.exit(code)
