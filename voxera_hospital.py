# ============================================================
# VOXERA — HOSPITAL OUTBOUND APPOINTMENT CALL  (demo entrypoint)
# ============================================================
#
#   python voxera_hospital.py --seed            # create a demo appointment,
#                                               # then call the patient
#   python voxera_hospital.py --appointment <id>
#
# Flow:
#   hospital dashboard schedules an appointment  (Supabase: appointments)
#        -> Voxera calls the patient
#        -> "We have you down for <dept> on <date> at <time>. Does that work?"
#        -> patient confirms  -> appointments.status = Confirmed
#           patient asks a new day -> status = Rescheduled + new date
#           patient declines -> status = Cancelled
#        -> Supabase updated -> dashboard reflects the change
#
# Intent detection here is DETERMINISTIC (keyword) so the demo is
# predictable; Priya's lines are templated for the same reason.
# STT + Priya voice are the shared real components from voxera_core.
# ============================================================

import os
import sys
import re
import time
import signal
import datetime as dt

import voxera_core as vx

try:
    import voxera_supabase as db
    DB_OK = True
except Exception as e:
    print(f"[BOOT] Supabase layer unavailable ({e}).")
    DB_OK = False


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday"]

CONFIRM_WORDS = [
    "yes", "yeah", "yep", "yup", "sure", "that works", "works for me",
    "sounds good", "okay", "ok", "fine", "confirm", "confirmed",
    "that's fine", "perfect", "great", "no problem", "i can do that",
    "see you then",
]
DECLINE_WORDS = [
    "cancel", "can't make it", "cannot make it", "not able to come",
    "don't want", "do not want", "won't be able", "will not be able",
    "no longer need", "skip it",
]
RESCHEDULE_WORDS = [
    "reschedule", "another day", "different day", "different time",
    "another time", "move it", "change it", "can i do", "what about",
    "instead", "later", "earlier", "not that day", "no,",
]


def next_weekday(target_name, from_date=None):
    from_date = from_date or dt.date.today()
    target = WEEKDAYS.index(target_name)
    delta = (target - from_date.weekday()) % 7
    delta = delta or 7
    return from_date + dt.timedelta(days=delta)


def parse_intent(text):
    """Return ('confirm'|'decline'|'reschedule'|'unclear', weekday_or_None)."""
    t = " " + re.sub(r"\s+", " ", text.lower()).strip() + " "

    day = None
    for w in WEEKDAYS:
        if w in t:
            day = w
            break
    if "tomorrow" in t:
        day = WEEKDAYS[(dt.date.today() + dt.timedelta(days=1)).weekday()]

    if any(w in t for w in DECLINE_WORDS):
        return "decline", None
    if any(w in t for w in RESCHEDULE_WORDS) or (day and "yes" not in t):
        return "reschedule", day
    if any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in CONFIRM_WORDS):
        return "confirm", None
    if day:
        return "reschedule", day
    # bare "no" / "that doesn't work" -> they want a different slot
    if re.search(r"\b(no|nope|nah|negative)\b", t) or "doesn't work" in t \
            or "does not work" in t or "can't do that" in t:
        return "reschedule", None
    return "unclear", None


def fmt_when(date_str, time_str):
    try:
        d = dt.date.fromisoformat(str(date_str))
        day_txt = d.strftime("%A, %B %-d") if os.name != "nt" else d.strftime("%A, %B %d")
    except Exception:
        day_txt = str(date_str)
    try:
        parts = str(time_str).split(":")
        h, m = int(parts[0]), int(parts[1])
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        t_txt = f"{h12}:{m:02d} {ampm}"
    except Exception:
        t_txt = str(time_str)
    return day_txt, t_txt


# ============================================================
# APPOINTMENT SETUP
# ============================================================

def seed_appointment():
    name = os.getenv("VOXERA_DEMO_PATIENT_NAME", "Voxera Demo Patient").strip()
    phone = os.getenv("VOXERA_DEMO_PHONE", "9990001111").strip()
    patient = db.get_or_create_patient(name, phone, "English")
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    appt = db.create_appointment(
        patient_name=name,
        appointment_date=tomorrow,
        appointment_time="10:00:00",
        department="General Medicine",
        doctor_name="Dr. Rao",
        reason="Follow-up after phone assessment",
        patient_id=patient["id"],
        status="scheduled",
    )
    print(f"[SEED] appointment {appt['id']}  {name}  {tomorrow} 10:00  (Scheduled)")
    return appt


def load_appointment(appt_id):
    if appt_id:
        appt = db.get_appointment(appt_id)
        if not appt:
            print(f"[ERR] appointment {appt_id} not found.")
            sys.exit(1)
        return appt
    name = os.getenv("VOXERA_DEMO_PATIENT_NAME", "Voxera Demo Patient").strip()
    appt = db.find_upcoming_appointment(patient_name=name)
    if not appt:
        print("[ERR] no upcoming appointment found. Run with --seed first.")
        sys.exit(1)
    return appt


# ============================================================
# MAIN
# ============================================================

def main():
    args = sys.argv[1:]
    do_seed = "--seed" in args
    appt_id = None
    if "--appointment" in args:
        appt_id = args[args.index("--appointment") + 1]

    if not DB_OK:
        print("[ERR] hospital flow needs Supabase.")
        sys.exit(1)

    print("\n" + "=" * 62)
    print("VOXERA — HOSPITAL OUTBOUND APPOINTMENT CALL")
    print("=" * 62 + "\n")

    appt = seed_appointment() if do_seed else load_appointment(appt_id)
    day_txt, time_txt = fmt_when(appt["appointment_date"], appt["appointment_time"])
    dept = appt.get("department") or "the clinic"
    patient_name = appt.get("patient_name") or "there"
    first_name = patient_name.split()[0]

    # calls.patient_id is NOT NULL - resolve the patient for this appointment.
    patient_id = appt.get("patient_id")
    if not patient_id:
        phone = os.getenv("VOXERA_DEMO_PHONE", "9990001111").strip()
        patient_id = db.get_or_create_patient(patient_name, phone, "English")["id"]

    vx.load_stt()
    vx.load_tts()

    tracker = vx.LatencyTracker()
    mem = vx.Memory()
    writer = vx.AsyncWriter("supabase")

    call = db.create_call(
        patient_id=patient_id,
        language="English",
        call_type="appointment_confirmation",
    )
    call_id = call["id"]
    print(f"[DB] outbound call {call_id} for appointment {appt['id']}")

    def persist(sp, msg):
        if msg:
            writer.submit(db.save_turn, call_id, sp, msg)

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda s, f: stop.update(flag=True))

    opening = (
        f"Hi, is this {first_name}? This is Voxera calling from City Hospital "
        f"about your appointment. We have you booked with {dept} on {day_txt} "
        f"at {time_txt}. Does that time still work for you?"
    )

    outcome = "completed"
    with vx.MicCapture() as mic:
        mic.calibrate()
        mem.add_assistant(opening)
        persist("ai", opening)
        print(f"\nVOXERA: {opening}\n")
        vx.speak(opening, tracker=tracker, mic=mic)

        resolved = False
        attempts = 0
        try:
            while not stop["flag"] and not resolved and attempts < 6:
                attempts += 1
                tracker.start_turn()
                print("[AUDIO] listening ...")
                audio = mic.capture_utterance()
                tracker.mark("eos")
                text = vx.transcribe(audio, tracker=tracker)
                if not text or len(text.strip()) < 2:
                    print("[AUDIO] no speech, retrying.")
                    continue

                print("\n" + "-" * 62)
                print(f"PATIENT: {text}")
                print("-" * 62)
                mem.add_user(text)
                persist("patient", text)

                intent, day = parse_intent(text)
                print(f"[INTENT] {intent}" + (f"  day={day}" if day else ""))

                if intent == "confirm":
                    db.update_appointment(appt["id"], status="confirmed")
                    reply = (
                        f"Perfect. You're confirmed for {day_txt} at {time_txt} "
                        f"with {dept}. We'll see you then. Take care."
                    )
                    outcome = "appointment_confirmed"
                    resolved = True

                elif intent == "reschedule" and day:
                    new_date = next_weekday(day)
                    db.update_appointment(
                        appt["id"], status="rescheduled",
                        appointment_date=new_date.isoformat(),
                    )
                    nd_txt, _ = fmt_when(new_date.isoformat(), appt["appointment_time"])
                    reply = (
                        f"No problem. I've moved your appointment to {nd_txt} "
                        f"at {time_txt}. You'll get a reminder before then."
                    )
                    day_txt = nd_txt
                    outcome = "appointment_rescheduled"
                    resolved = True

                elif intent == "reschedule":
                    reply = ("Sure, we can move it. What day works better for you?")

                elif intent == "decline":
                    db.update_appointment(appt["id"], status="cancelled")
                    reply = ("Okay, I've cancelled that appointment. Call us back "
                             "anytime to rebook. Take care.")
                    outcome = "appointment_cancelled"
                    resolved = True

                else:
                    reply = ("Sorry, I didn't quite get that. Does "
                             f"{day_txt} at {time_txt} work, yes or no?")

                mem.add_assistant(reply)
                persist("ai", reply)
                print(f"\nVOXERA: {reply}\n")
                vx.speak(reply, tracker=tracker, mic=mic)

                tracker.mark("turn_end")
                rec = tracker.end_turn()
                print(vx.LatencyTracker.fmt(rec))

        except Exception as e:
            print(f"[CALL] error: {e!r}")
            outcome = "failed"
        finally:
            writer.drain(6.0)
            try:
                db.update_call_status(call_id, "completed", outcome=outcome)
            except Exception:
                pass
            final = db.get_appointment(appt["id"])
            print("\n" + "=" * 62)
            print(f"RESULT: appointment {appt['id']}  ->  status = {final['status']}"
                  f"  ({final['appointment_date']} {final['appointment_time']})")
            print("=" * 62)
            print(tracker.summary())


if __name__ == "__main__":
    main()
