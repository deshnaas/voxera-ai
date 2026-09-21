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
from voxera_patientfetch.closing import is_closing, declines_id
from voxera_patientfetch import identity as _ident
from voxera_emergency import check_emergency, CANNED_RESPONSES

try:
    import voxera_supabase as db
    DB_OK = True
except Exception as e:
    print(f"[BOOT] Supabase layer unavailable ({e}); running without persistence.")
    DB_OK = False

# Multilingual voice (Hindi / Marathi / English). Optional: VOXERA_MULTILANG=0, a missing model or package
# falls back to English-only, exactly as before.
ML_OK = False
if os.getenv("VOXERA_MULTILANG", "1").strip() != "0":
    try:
        from voxera_multilang.integration import MultiLang
        from voxera_multilang import safety as ml_safety
        ML_OK = True
    except Exception as e:
        print(f"[BOOT] multilingual layer unavailable ({type(e).__name__}); English only.")

# Patient Intelligence (patient-ID verification, record Q&A, adaptive triage, voice signal).
# Optional: VOXERA_PATIENTFETCH=0 or a missing migration falls back to the classic flow.
PF_OK = False
PF_PHRASES = []
if DB_OK and os.getenv("VOXERA_PATIENTFETCH", "1").strip() != "0":
    try:
        from voxera_patientfetch.call_integration import CallAssistant, PHRASES as PF_PHRASES
        PF_OK = True
    except Exception as e:
        print(f"[BOOT] Patient Intelligence layer unavailable ({type(e).__name__}); using the classic call flow.")


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
        self.closing = False
        self.finished = False
        self.pf = None                      # CallAssistant when Patient Intelligence is active
        self.ml = None                      # MultiLang when the multilingual voice is active
        self._logged_lang = None

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
            if PF_OK:
                try:
                    pf = CallAssistant(db, caller_phone=phone)
                    self.pf = pf if pf.enabled else None
                    if self.pf:
                        print("[PATIENT_AI] patient-ID verification is ON for this call.")
                except Exception as e:
                    print(f"[PATIENT_AI] disabled for this call ({type(e).__name__}).")
                    self.pf = None
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
            if self.pf:
                try:
                    s.update(self.pf.summary_extras())     # voice_signal / clinical_context / record_context
                except Exception:
                    pass
            if self.ml_on and self.ml.tracker.decided:
                from voxera_multilang.lang import NAMES
                s["language"] = {"reply_language": NAMES[self.ml.lang], "switches": self.ml.tracker.switches,
                                 "note": "Hindi/Marathi replies are curated wording, not generated text."}
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

    # -- multilingual helpers -------------------------------------------------
    @property
    def ml_on(self):
        return bool(self.ml and self.ml.enabled)

    @property
    def lang(self):
        return self.ml.lang if self.ml_on else "en"

    def log_language(self):
        """Store the language the call is being held in (calls.language) once it is known / changes."""
        if not (self.ml_on and self.ml.tracker.decided and self.call_id and self.ml.lang != self._logged_lang):
            return
        self._logged_lang = self.ml.lang
        if self.writer:
            from voxera_multilang.lang import NAMES
            self.writer.submit(lambda: db.supabase.table("calls").update(
                {"language": NAMES[self.ml.lang]}).eq("id", self.call_id).execute())

    # -- Patient Intelligence hooks ---------------------------------------
    def finish_reply(self, reply, mic):
        """Speak + record a reply produced by the patient-intelligence layer (no LLM)."""
        if self.ml_on:
            reply = self.ml.localize(reply)               # fixed English lines -> the caller's language
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

    # ---- end of call: file the call under the caller's record ---------------------------------------------
    def begin_wrapup(self, mic):
        """The caller is done (or nothing new is left to say). Ask for the patient ID ONCE so this call is added to
        that patient's record, then say goodbye and hang up."""
        pf = self.pf
        self.to(S.PROCESSING)
        if pf and pf.enabled and not pf.verified and not pf.id_declined and not pf.verifier.locked:
            self.closing = True
            pf.awaiting_id = True
            pf.nudges = 0
            pf.pending_complaint = None
            return self.finish_reply(_ident.ASK_ID_END, mic)
        res = self.finish_reply(_ident.SAVED_GOODBYE if (pf and pf.verified) else _ident.GOODBYE, mic)
        self.finished = True
        return res

    def handle_id_turn(self, text, mic):
        """Patient-ID phase: verify, then carry on with any complaint already given."""
        self.to(S.PROCESSING)
        if self.closing and declines_id(text):
            self.pf.awaiting_id = False
            self.pf.id_declined = True
            self.finished = True
            return self.finish_reply(_ident.GOODBYE, mic)
        out = self.pf.id_turn(text)
        if self.closing:
            if out.verified:
                pid = self.pf.rebind_call(self.call_id)           # the call now belongs to this patient
                if pid:
                    self.patient_id = pid
                    self.patient = self.pf.patient
                self.finished = True
                return self.finish_reply(_ident.SAVED_GOODBYE, mic)
            if out.finished:
                self.finished = True
                return self.finish_reply(_ident.GOODBYE, mic)
            return self.finish_reply(out.text, mic)
        if out.verified:
            pid = self.pf.rebind_call(self.call_id)      # calls.patient_id stays NOT NULL
            if pid:
                self.patient_id = pid
                self.patient = self.pf.patient
            if self.ml_on:                                # the record's language preference is a tiebreak for hi vs mr
                pref = str((self.pf.patient or {}).get("preferred_language") or "").strip().lower()
                self.ml.tracker.preferred = {"hindi": "hi", "marathi": "mr", "english": "en"}.get(pref)
        res = self.finish_reply(out.text, mic)
        if out.finished and out.complaint and not (res or {}).get("interrupted"):
            self.mem.add_user(out.complaint)             # what they said before giving the ID
            return self.handle_normal(out.complaint, mic)
        return res

    def handle_pf(self, text, mic):
        """Record questions, then adaptive clarification. Returns None to fall through
        to the classic care / LLM path. The frozen emergency detector has ALREADY run."""
        pf = self.pf
        ans = pf.record_reply(
            text, llm=lambda sysp, u: vx.llm_respond(sysp, [], u, tracker=None,
                                                     temperature=0.1, max_tokens=90))
        if ans is not None:
            pf.audit(self.call_id, "ask_question")
            if self.ml_on:
                ans = self.ml.record_text(ans, getattr(pf, "last_frame", None))
            return self.finish_reply(ans, mic)

        step = pf.triage_step(text, self.mem.last_assistant())
        if step.kind == "emergency":                     # detector fired on the caller's own combined words
            emg = self.ml.localize_emergency(step.emergency) if self.ml_on else step.emergency
            self.handle_emergency(text, emg, mic)
            return {"ok": True, "interrupted": False, "pending_audio": None}
        if step.kind == "ask":
            return self.finish_reply(self.ml.triage_text(step) if self.ml_on else step.text, mic)
        if step.kind == "conclude":
            print(f"[TRIAGE] conclusion={step.conclusion}")
            if step.conclusion == "URGENT_CLINICAL_REVIEW" and self.writer and self.call_id:
                self.writer.submit(pf.escalate_urgent, self.call_id, self.patient_id,
                                   (self.patient or {}).get("full_name", "Unknown caller"))
            return self.finish_reply(self.ml.triage_text(step) if self.ml_on else step.text, mic)
        return None

    def handle_normal(self, patient_text, mic):
        self.to(S.PROCESSING)

        if self.pf:
            self.pf.seed_facts(self.mem.facts)           # verified allergies/conditions -> OTC safety checks
            r = self.handle_pf(patient_text, mic)
            if r is not None:
                return r

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
            if self.ml_on and self.lang != "en":
                # Hindi / Marathi: the curated guidance is spoken from the reviewed catalog; the LLM is not used
                reply = self.ml.care_text(cg, otc, profile)
            if reply is None:
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
        if reply is None and self.ml_on and self.lang != "en":
            # The local LLM cannot be trusted to write Hindi/Marathi for a medical assistant (it produces
            # wrong or echoed sentences), so these turns use reviewed, deterministic wording instead.
            self.tracker.mark("ai_start")
            reply = self.ml.generic_reply(patient_text)
            if reply is None:
                return self.begin_wrapup(mic)
            self.tracker.mark("ai_first_token")
            self.tracker.mark("ai_end")
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
            reply = self.ml.say("fallback") if self.ml_on else FALLBACK_REPLY

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

        turn = None
        if self.ml_on:
            # language ID first; English keeps the existing base.en path, Hindi/Marathi use the multilingual model
            turn = self.ml.transcribe_turn(audio, lambda a: vx.transcribe(a, tracker=self.tracker),
                                           need_translation=not (self.pf and self.pf.awaiting_id))
            if turn.path == "multilingual":
                self.tracker.mark("stt_start")
                self.tracker.mark("stt_end")
            text, text_en = turn.native, (turn.english or turn.native)
        else:
            text = vx.transcribe(audio, tracker=self.tracker)
            text_en = text
        if not text or len(text.strip()) < 2:
            print("[AUDIO] no reliable speech — listening again.")
            return None

        print("\n" + "-" * 62)
        print(f"PATIENT: {text}")
        if turn is not None and turn.path == "multilingual":
            print(f"   (English understanding: {text_en})")
        print("-" * 62)

        context = self.mem.context_excluding_last_user()
        last_ai = self.mem.last_assistant()

        if self.pf:
            self.pf.submit_audio(audio)      # voice signal: async, AFTER Whisper, never blocks the turn

        # what the English-only logic sees, and what is written to the transcript
        id_text = text_en
        if self.pf and self.pf.awaiting_id:
            from voxera_multilang.safety import normalize_spoken_id_text
            cands = [normalize_spoken_id_text(text), text_en, text] if self.ml_on else [text]
            id_text = self.pf.pick_id_reading(cands) or next((c for c in cands if self.pf.looks_like_id(c)), text_en)
        shown = text_en
        shown_native = text
        if self.pf and self.pf.awaiting_id and self.pf.looks_like_id(id_text):
            shown = shown_native = "[patient ID provided]"  # the spoken ID is not stored in transcripts / LLM memory
        self.mem.add_user(shown)
        self.persist_turn("patient", shown_native)
        if turn is not None and turn.path == "multilingual" and shown_native != "[patient ID provided]":
            self.persist_turn("system", f"translation_en: {text_en}")
        self.log_language()

        if turn is not None and self.ml.maybe_switch_on_request(turn):
            self.log_language()
            return self.finish_reply(self.ml.say("lang_switched"), mic)

        # The FROZEN emergency detector always runs first — even before ID verification.
        # For Hindi/Marathi it is fed English renderings of the same words (safety.py); only the SPOKEN reply is localised.
        if turn is not None:
            emg = self.ml.emergency(check_emergency, turn, context=context, last_assistant=last_ai)
        else:
            emg = check_emergency(text, context=context, last_assistant=last_ai)
        if emg:
            self.handle_emergency(text_en, emg, mic)
            res = {"ok": True, "interrupted": False, "pending_audio": None}
        else:
            print("[SAFETY] no emergency signal")
            if (not self.closing and not (self.pf and self.pf.awaiting_id)
                    and is_closing(text_en, text, last_ai)):
                res = self.begin_wrapup(mic)
            elif self.pf and self.pf.awaiting_id:
                res = self.handle_id_turn(id_text, mic)
            else:
                res = self.handle_normal(text_en, mic)

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
    # load the three slow things at once (speech recognition, the voice, the database) instead of one after another
    import threading
    _boot = [threading.Thread(target=vx.load_stt, name="boot-stt"),
             threading.Thread(target=call.open_supabase_call, name="boot-db")]
    for t in _boot:
        t.start()
    vx.load_tts()
    for t in _boot:
        t.join()
    vx.prewarm_phrases([GREETING, FALLBACK_REPLY] + CANNED_RESPONSES)

    greeting = GREETING
    if call.pf:
        greeting = call.pf.greeting()          # "...Before we begin, could you please tell me your patient ID?"
        if os.getenv("VOXERA_PREWARM_PF", "1") != "0":
            vx.prewarm_phrases(PF_PHRASES)

    if ML_OK:
        call.ml = MultiLang(vx)
        if call.ml.enabled:
            call.ml.boot()                     # multilingual STT loads in the background; TTS hook installed
            call.ml.prewarm_greeting(with_id=bool(call.pf and call.pf.ask_first))
        if call.ml.enabled:
            # the language is unknown until the caller speaks, so the first line is a short trilingual greeting
            greeting = call.ml.greeting_text(with_id=bool(call.pf and call.pf.ask_first))
            print("[LANG] multilingual voice ON: English / हिन्दी / मराठी (language is detected from the caller)")

    with vx.MicCapture() as mic:
        mic.calibrate()
        if call.pf:
            call.pf.warm()                     # emotion model loads in its own low-priority process

        # -- greeting ------------------------------------------
        call.to(S.GREETING)
        call.mem.add_assistant(greeting)
        call.persist_turn("ai", greeting)
        print(f"\nVOXERA: {greeting}\n")
        vx.speak(greeting, tracker=call.tracker, mic=mic)

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
                if call.finished:
                    print("\n[CALL] wrapped up.")
                    break
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
            if call.pf:
                call.pf.close()
            print(call.tracker.summary())
            print("\n[CALL] ended.\n")


if __name__ == "__main__":
    main()
