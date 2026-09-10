# ============================================================
# VOXERA — PATIENT INBOUND VOICE CALL  (demo entrypoint)
# ============================================================
#
#   python voxera.py
#
# A patient talks to Voxera over the local microphone/speaker
# as if it were a phone call.  Real-time loop:
#
#   LISTENING -> USER_SPEAKING -> PROCESSING -> VOXERA_SPEAKING -> ...
#                                   |
#                                   +-- EMERGENCY  (deterministic, immediate)
#
# What is real here (no fake claims):
#   * Whisper STT, Qwen3-1.7B reasoning, Priya/Kokoro voice — all local.
#   * Deterministic emergency layer -> real Supabase referral + event.
#   * Every spoken turn persisted to Supabase conversation_turns (async).
#   * Per-turn + end-of-call latency measured and printed.
#   * Within-call memory (name, symptoms, etc.) via rolling context.
#
# What is NOT wired: PSTN telephony (no telephony provider / creds in
# this environment).  The mic/speaker path is the stand-in for the call.
# ============================================================

import os
import time

import voxera_core as vx
import voxera_care as care
import voxera_summary as summary
from voxera_emergency import check_emergency, CANNED_RESPONSES

try:
    import voxera_supabase as db
    DB_OK = True
except Exception as e:
    print(f"[BOOT] Supabase layer unavailable ({e}); running without persistence.")
    DB_OK = False


# ============================================================
# PROMPT — written for the ear, not the page
# ============================================================

SYSTEM_PROMPT = """You are Voxera, a warm healthcare assistant for City Hospital, talking to a patient on a phone call.

How to talk:
- Sound like a real person on the phone. Short. 1 or 2 sentences.
- Ask at most ONE question per turn.
- Use plain words and contractions. No lists, no markdown, no headings.
- Brief acknowledgements are fine ("Okay.", "Got it.") but don't over-acknowledge.
- Never repeat something the patient already told you.
- Do not describe your own thinking. Just say what Voxera says out loud.

What you do:
- Gather the patient's health concern: what, how long, how bad, other symptoms.
- Give simple, low-risk self-care advice when it's clearly appropriate.
- Say clearly when they should see a doctor or get urgent care.
- You are not a doctor: never give a firm diagnosis, never give medication names or doses.
- If something sounds dangerous, tell them plainly to get emergency help now.

Always answer the patient's MOST RECENT message. Move the conversation
forward - don't re-ask something they've answered.

Answer only with the words Voxera should speak."""

GREETING = os.getenv(
    "VOXERA_GREETING",
    "Hi, this is Voxera from City Hospital. How can I help you today?",
)

FALLBACK_REPLY = "Sorry, I didn't catch that. Could you say it again?"


# ============================================================
# STATE
# ============================================================

class S:
    BOOT = "BOOT"
    GREETING = "GREETING"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "VOXERA_SPEAKING"
    EMERGENCY = "EMERGENCY"
    ENDING = "CALL_ENDING"


def log_state(old, new):
    print(f"[STATE] {old} -> {new}")


# ============================================================
# CALL
# ============================================================

class Call:
    def __init__(self):
        self.state = S.BOOT
        self.tracker = vx.LatencyTracker()
        self.mem = vx.Memory(max_turns=8)
        self.writer = vx.AsyncWriter("supabase") if DB_OK else None

        self.call_id = None
        self.patient = None
        self.patient_id = None
        self.emergency_count = 0
        self.ai_response_count = 0
        self.interruption_count = 0
        self.t_start = time.time()
        # collected for the end-of-call structured summary
        self.emergency_result = None
        self.care_events = []

    # -- state helper --------------------------------------------------
    def to(self, new):
        log_state(self.state, new)
        self.state = new

    # -- supabase bootstrap ----------------------------------------
    def open_supabase_call(self):
        if not DB_OK:
            print("[DB] disabled — using in-memory conversation only.")
            return
        try:
            phone = os.getenv("VOXERA_DEMO_PHONE", "9990001111").strip()
            name = os.getenv("VOXERA_DEMO_PATIENT_NAME", "Voxera Demo Patient").strip()
            lang = os.getenv("VOXERA_DEMO_LANGUAGE", "English").strip() or "English"
            self.patient = db.get_or_create_patient(name, phone, lang)
            self.patient_id = self.patient["id"]
            fac = None
            try:
                f = db.get_default_facility()
                fac = f["id"] if f else None
            except Exception:
                fac = None
            call = db.create_call(
                patient_id=self.patient_id,
                language=lang,
                call_type="initial_assessment",
                facility_id=fac,
            )
            self.call_id = call["id"]
            print(f"[DB] patient={self.patient_id}  call={self.call_id}  "
                  f"facility={fac}")
        except Exception as e:
            print(f"[DB] could not open call ({e}); continuing without persistence.")
            self.call_id = None

    def persist_turn(self, speaker, message):
        if self.writer and self.call_id and message:
            self.writer.submit(db.save_turn, self.call_id, speaker, message)

    def close_supabase_call(self, outcome="completed"):
        if not (DB_OK and self.call_id):
            return
        dur = int(time.time() - self.t_start)
        try:
            db.update_call_status(self.call_id, "completed", outcome=outcome)
            db.update_call_metrics(
                self.call_id,
                ai_response_count=self.ai_response_count,
                emergency_checks_count=self.emergency_count,
                interruption_count=self.interruption_count,
                call_duration_seconds=dur,
                call_success=(outcome in ("completed", "emergency_escalated")),
            )
            print(f"[DB] call closed: {outcome} ({dur}s)")
        except Exception as e:
            print(f"[DB] close failed: {e}")

    def write_call_summary(self, outcome):
        """Build the structured summary and store it (Supabase, schema-tolerant)."""
        try:
            turns = []
            patient = self.patient
            referral = appt = None
            if DB_OK and self.call_id:
                try:
                    turns = db.get_conversation(self.call_id)
                except Exception:
                    turns = []
                if self.patient_id:
                    try:
                        referral = db.get_latest_referral(self.patient_id)
                    except Exception:
                        referral = None
                    try:
                        appt = db.find_upcoming_appointment(
                            patient_id=self.patient_id)
                    except Exception:
                        appt = None

            s = summary.build_summary(
                patient=patient, facts=self.mem.facts,
                transcript=self.mem.transcript(), turns=turns,
                emergency=self.emergency_result, care_events=self.care_events,
                referral=referral, appointment=appt, outcome=outcome,
            )
            text = summary.render_text(s)
            print("\n" + text + "\n")
            if DB_OK and self.call_id:
                summary.persist_summary(db, self.call_id, self.patient_id, s, text)
        except Exception as e:
            print(f"[SUMMARY] failed: {e}")

    # -- the spoken turn --------------------------------------------
    def handle_emergency(self, patient_text, result, mic):
        self.to(S.EMERGENCY)
        self.emergency_count += 1
        self.emergency_result = result
        print(f"[SAFETY] EMERGENCY: {result.category}  trigger=\"{result.trigger}\"")
        print(f"[SAFETY] dept={result.recommended_department} severity={result.severity}")

        # 1. speak immediately — no LLM, no extra questions, no DB wait
        self.mem.add_assistant(result.spoken_response)
        self.persist_turn("ai", result.spoken_response)
        print(f"\nVOXERA: {result.spoken_response}\n")
        vx.speak(result.spoken_response, tracker=self.tracker, mic=mic)

        # 2. escalate + persist (async, never blocks the call)
        if self.writer and self.call_id:
            self.writer.submit(
                db.escalate_emergency,
                self.call_id,
                (self.patient or {}).get("full_name", "Voxera Demo Patient"),
                result.trigger,
                result.category,
                result.recommended_department,
                self.patient_id,
                self.mem.transcript(),
                result.spoken_response,          # immediate_action
            )
            print("[ESCALATION] ai_assessment + referral + referral_event + "
                  "emergency_case + realtime notification queued for the dashboard.")
        else:
            print("[ESCALATION] (no DB) would create referral: "
                  f"{result.category} / {result.trigger}")

    def handle_normal(self, patient_text, mic):
        self.to(S.PROCESSING)

        fl = vx.facts_line(self.mem.facts)
        if fl:
            print(f"[FACTS] {fl}")

        # --------------------------------------------------------
        # CONTROLLED HOME-CARE / OTC LAYER
        # --------------------------------------------------------
        # Only reached for utterances the FROZEN emergency detector did NOT
        # flag (run_turn short-circuits emergencies before this method).
        # The LLM here only re-words curated, approved guidance.
        reply = None
        given = self.mem.facts.setdefault("_care_given", [])
        cg = care.lookup_care(patient_text, self.mem.context_excluding_last_user())
        if cg and cg.care_id not in given:
            profile = care.build_profile(self.mem.facts)
            otc = care.suggest_otc(cg.care_id, profile)
            print(f"[CARE] topic={cg.care_id}"
                  + (f"  otc={otc.items or 'deferred' if otc else None}"
                     if otc else "  otc=none"))
            self.tracker.mark("ai_start")
            reply = care.render(
                cg, otc, patient_text,
                llm=lambda sysp, hist, u: vx.llm_respond(
                    sysp, hist, u, tracker=None, temperature=0.3,
                    max_tokens=120),
            )
            self.tracker.mark("ai_first_token")
            self.tracker.mark("ai_end")
            given.append(cg.care_id)
            self.care_events.append({
                "topic": cg.care_id,
                "label": cg.label,
                "steps": list(cg.steps),
                "escalation": list(cg.see_help_if),
                "otc": ({"deferred": otc.deferred, "items": list(otc.items),
                         "spoken": otc.spoken} if otc else None),
            })

        # --------------------------------------------------------
        # NORMAL CONVERSATIONAL PATH (unchanged)
        # --------------------------------------------------------
        if reply is None:
            # Injecting the compact state into the prompt confuses qwen3:1.7b
            # (it starts summarising). The rolling history already prevents
            # re-asking. Enable VOXERA_INJECT_FACTS=1 with a stronger model.
            system = SYSTEM_PROMPT
            if fl and os.getenv("VOXERA_INJECT_FACTS", "0") == "1":
                system += (f"\n\nKnown about this patient already: {fl}. "
                           "Do not ask about these again.")

            # history() already ends with this patient turn (added in run_turn);
            # llm_respond appends patient_text itself, so drop the last item here.
            reply = vx.llm_respond(
                system, self.mem.history()[:-1], patient_text,
                tracker=self.tracker,
            )
        if not reply:
            reply = FALLBACK_REPLY

        self.mem.add_assistant(reply)
        self.persist_turn("ai", reply)
        self.ai_response_count += 1

        print(f"\nVOXERA: {reply}\n")
        self.to(S.SPEAKING)
        res = vx.speak(reply, tracker=self.tracker, allow_barge_in=True, mic=mic)
        if res.get("interrupted"):
            self.interruption_count += 1
            print(f"[BARGE] interruption #{self.interruption_count} — "
                  "processing what the patient said.")
        return res

    def run_turn(self, mic, primed=None):
        self.tracker.start_turn()
        self.to(S.LISTENING)
        print("[AUDIO] listening ...")
        audio = mic.capture_utterance(prime_chunks=primed)
        self.tracker.mark("eos")
        print(f"[AUDIO] captured {len(audio)/vx.SAMPLE_RATE:.1f}s  "
              f"(rms={vx.rms(audio):.4f} peak={vx.peak(audio):.3f})")

        text = vx.transcribe(audio, tracker=self.tracker)
        if not text or len(text.strip()) < 2:
            print("[AUDIO] no reliable speech — listening again.")
            return None

        print("\n" + "-" * 62)
        print(f"PATIENT: {text}")
        print("-" * 62)

        context = self.mem.context_excluding_last_user()
        last_ai = self.mem.last_assistant()

        self.mem.add_user(text)
        self.persist_turn("patient", text)

        emg = check_emergency(text, context=context, last_assistant=last_ai)
        if emg:
            self.handle_emergency(text, emg, mic)
            res = {"ok": True, "interrupted": False, "pending_audio": None}
        else:
            print("[SAFETY] no emergency signal")
            res = self.handle_normal(text, mic)

        self.tracker.mark("turn_end")
        rec = self.tracker.end_turn(extra={"state": self.state})
        print("\n" + vx.LatencyTracker.fmt(rec))
        return res


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "=" * 62)
    print("VOXERA — PATIENT VOICE CALL")
    print("=" * 62)
    print(f"  barge-in mode : {vx.BARGE_MODE}  "
          f"({'true-duplex AEC' if vx.BARGE_MODE == 'aec' else 'strict turn-taking'})")
    print(f"  whisper       : {vx.WHISPER_MODEL}")
    print(f"  llm           : {vx.LLM_MODEL}")
    print("=" * 62 + "\n")

    call = Call()

    # -- boot models (once) --------------------------------------
    vx.load_stt()
    vx.load_tts()
    vx.prewarm_phrases([GREETING, FALLBACK_REPLY] + CANNED_RESPONSES)
    call.open_supabase_call()

    with vx.MicCapture() as mic:
        mic.calibrate()

        # -- greeting ------------------------------------------
        call.to(S.GREETING)
        call.mem.add_assistant(GREETING)
        call.persist_turn("ai", GREETING)
        print(f"\nVOXERA: {GREETING}\n")
        vx.speak(GREETING, tracker=call.tracker, mic=mic)

        primed = None
        outcome = "completed"
        try:
            while True:
                res = call.run_turn(mic, primed=primed)
                primed = None
                if res and res.get("interrupted") and res.get("pending_audio"):
                    # keep the first words of the interruption
                    primed = res["pending_audio"]
                    print("[BARGE] carrying interrupted speech into next turn.")
                if call.state == S.EMERGENCY:
                    outcome = "emergency_escalated"
                print("\n[CALL] your turn ...  (Ctrl+C to hang up)\n")
        except KeyboardInterrupt:
            print("\n[CALL] hangup.")
        except Exception as e:
            print(f"\n[CALL] error: {e!r}")
            outcome = "failed"
        finally:
            call.to(S.ENDING)
            # persist the compact structured facts as a system turn
            fl = vx.facts_line(call.mem.facts)
            if fl:
                print(f"[FACTS] final: {fl}")
                call.persist_turn("system", f"structured_facts: {fl}")
            if call.writer:
                call.writer.drain(timeout=8.0)   # let referral/turn writes land
            call.close_supabase_call(outcome)
            call.write_call_summary(outcome)
            print(call.tracker.summary())
            print("\n[CALL] ended.\n")


if __name__ == "__main__":
    main()
