"""Glue between the live Voxera call (voxera.py) and Patient Intelligence.

voxera.py keeps ownership of audio, STT, TTS, barge-in, the frozen emergency
detector and the LLM. This class only answers three questions per turn:

    1. "Who is calling, and are they verified?"           -> id_turn()
    2. "Is this a question about their own record?"       -> record_reply()
    3. "Should Voxera clarify before concluding?"          -> triage_step()

Design points
  * Emergency detection ALWAYS runs first in voxera.py, before any of this, so a
    caller who says "I have chest pain" before giving an ID is escalated at once.
  * The layer is optional: if the migration hasn't been run (no patients.patient_id)
    or anything is unavailable, ``enabled`` is False and the classic flow runs.
  * Patient context is loaded once per call, in the background, right after
    verification. The turn loop never waits on the database or the voice model.
  * Nothing here logs names, IDs or record contents.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from voxera_emergency import check_emergency          # the FROZEN detector — imported, never modified

from . import identity as ident
from .identity import IdentityVerifier, extract_uuid, normalize_patient_id
from .models import Conclusion
from .patient_ai import PatientIntelligenceAI
from .record_qa import (CONFLICT, DB_DOWN, HEDGE, NOT_FOUND, OLD_RX_NOW, classify_intent)
from .repository import RepoError, SupabaseRepo
from .triage import Q as TRIAGE_Q
from .triage import TEXT_CONCLUSION, TriagePlanner
from .voice_signal import VoiceSignalAnalyzer

NUDGE = "Before we continue, could you please tell me your patient ID?"
NO_RECORD_QUESTION = ("I can't share record details without verifying your patient ID first. "
                      "I can still help with what you're feeling.")

PHRASES = [
    ident.ASK_ID, ident.ASK_ID_FOR_RECORD, ident.ASK_ID_END, ident.SAVED_GOODBYE, ident.GOODBYE, ident.GREETING_NO_ID, ident.RETRY_ID, ident.RETRY_FORMAT, ident.VERIFIED, ident.GIVE_UP, ident.DB_DOWN,
    ident.LOW_ASSURANCE, NUDGE, NO_RECORD_QUESTION, NOT_FOUND, CONFLICT, DB_DOWN,
    TRIAGE_Q["quality"], TRIAGE_Q["redflags_chest"], TRIAGE_Q["ongoing"], TRIAGE_Q["onset"],
    TRIAGE_Q["radiation"], TRIAGE_Q["redflags_which"], TRIAGE_Q["location"], TRIAGE_Q["severity"],
    *[t for t in TEXT_CONCLUSION.values()],
]

_AROUSAL_RANK = {"unavailable": -1, "low": 0, "moderate": 1, "high": 2}


@dataclass
class IdTurn:
    text: str
    verified: bool = False
    finished: bool = False            # ID phase is over (verified or gave up)
    complaint: Optional[str] = None   # a complaint the caller made before giving their ID
    reason: str = ""


class CallAssistant:
    def __init__(self, db, caller_phone: Optional[str] = None, repo=None, ask_first: Optional[bool] = None):
        self.db = db
        self.repo = repo if repo is not None else SupabaseRepo(db.supabase)   # repo= is for tests
        self.svc = PatientIntelligenceAI(self.repo, cache_ttl=1800.0)
        self.verifier = IdentityVerifier(self.repo, caller_phone)
        self.triage = TriagePlanner(check_emergency)
        self.voice = VoiceSignalAnalyzer()
        self.patient: Optional[dict] = None
        self.ctx = None
        self.assurance = "none"
        self.awaiting_id = False
        self.id_declined = False
        self.pending_complaint: Optional[str] = None
        self.nudges = 0
        self.voice_log: list = []
        self.urgent: Optional[dict] = None
        self.record_answers = 0
        self.last_frame: dict = {}
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pf")
        self._ctx_ready = threading.Event()
        self.enabled = True if repo is not None else self._probe()
        # The ID is asked for only when the caller wants something from their record (medicines, scans,
        # prescriptions, appointments). VOXERA_ASK_ID_FIRST=1 restores the old "ID before anything" flow.
        self.ask_first = (os.getenv("VOXERA_ASK_ID_FIRST", "0") == "1") if ask_first is None else ask_first
        self.awaiting_id = self.enabled and self.ask_first

    # ---- boot ---------------------------------------------------------
    def _probe(self) -> bool:
        """The ID flow needs patients.patient_id (created by the migration)."""
        try:
            self.db.supabase.table("patients").select("patient_id").limit(1).execute()
            return True
        except Exception as e:                                      # noqa: BLE001
            msg = str(getattr(e, "message", e))
            if "patient_id" in msg or "42703" in msg or "does not exist" in msg:
                print("[PATIENT_AI] patients.patient_id is missing - run sql/2026_voxera_patient_intelligence.sql "
                      "to enable patient-ID verification. Using the classic call flow.")
            else:
                print("[PATIENT_AI] record service unavailable; using the classic call flow.")
            return False

    def greeting(self) -> str:
        return ident.ASK_ID if self.ask_first else ident.GREETING_NO_ID

    def warm(self) -> None:
        self.voice.warm()

    # ---- identity -----------------------------------------------------------
    def looks_like_id(self, text: str) -> bool:
        return bool(normalize_patient_id(text) or extract_uuid(text))

    def pick_id_reading(self, candidates) -> Optional[str]:
        """Speech recognition can misread a spoken ID differently in each rendering (native script, English
        translation). Prefer the reading that is an ID that exists; lookups are cheap and burn no attempts."""
        seen = []
        for c in candidates:
            pid = normalize_patient_id(c)
            if pid and pid not in seen:
                seen.append(pid)
                try:
                    rows = self.repo.find_patient(patient_id=pid)
                except Exception:                                    # noqa: BLE001
                    return None
                if len(rows) == 1:
                    return c
        return None

    def id_turn(self, text: str) -> IdTurn:
        words = len((text or "").split())
        if not self.looks_like_id(text) and words >= 5:
            # a complaint, not an ID: keep it, ask once or twice more, never make them repeat it
            self.pending_complaint = ((self.pending_complaint + " ") if self.pending_complaint else "") + text
            self.nudges += 1
            if self.nudges > 2:
                self.awaiting_id = False
                self.id_declined = True
                return IdTurn(ident.GIVE_UP, finished=True, complaint=self.pending_complaint, reason="declined")
            return IdTurn(NUDGE, reason="nudge")

        r = self.verifier.attempt(text)
        if r.verified:
            self.patient, self.assurance = r.patient, r.assurance
            self.awaiting_id = False
            print("[PATIENT_AI] patient verified")
            self._load_context_async()
            spoken = ident.VERIFIED if not self.pending_complaint else "Thank you. I have your record."
            return IdTurn(spoken, verified=True, finished=True, complaint=self.pending_complaint, reason="ok")
        if r.reason in ("locked",) or self.verifier.locked:
            self.awaiting_id = False
            self.id_declined = True
            return IdTurn(ident.GIVE_UP, finished=True, complaint=self.pending_complaint, reason="locked")
        if r.reason == "error":
            self.awaiting_id = False
            self.id_declined = True
            return IdTurn(ident.DB_DOWN, finished=True, complaint=self.pending_complaint, reason="error")
        return IdTurn(r.spoken, reason=r.reason)

    @property
    def verified(self) -> bool:
        return self.patient is not None

    @property
    def can_disclose(self) -> bool:
        """Record details may be spoken only with a verified ID and no phone mismatch."""
        return self.verified and self.assurance != "low"

    def _load_context_async(self) -> None:
        def run():
            try:
                self.ctx = self.svc.build_clinical_context(self.patient["id"])
                self.triage.set_patient(True, allergies=self.ctx.allergies,
                                        meds=[m.get("medicine_name") for m in self.ctx.active_medications])
            except RepoError:
                self.ctx = None
            finally:
                self._ctx_ready.set()
        self._pool.submit(run)

    def seed_facts(self, facts: dict) -> None:
        """Verified allergies/conditions/age feed the existing OTC safety checks (never the reverse)."""
        if not self.ctx:
            return
        al = facts.setdefault("allergies", [])
        for a in self.ctx.allergies:
            if a.lower() not in {x.lower() for x in al}:
                al.append(a)
        cs = facts.setdefault("conditions", [])
        for c in self.ctx.conditions:
            if c.lower() not in {x.lower() for x in cs}:
                cs.append(c)
        age = (self.ctx.patient or {}).get("age")
        if age is not None and facts.get("age") is None:
            facts["age"] = age

    def rebind_call(self, call_id: Optional[str]) -> Optional[str]:
        """Point the call at the verified patient (calls.patient_id stays NOT NULL)."""
        if not (call_id and self.patient):
            return None
        try:
            self.db.supabase.table("calls").update({"patient_id": self.patient["id"]}).eq("id", call_id).execute()
        except Exception:                                           # noqa: BLE001
            return None
        return self.patient["id"]

    # ---- record questions -----------------------------------------------------
    def record_reply(self, text: str, llm=None) -> Optional[str]:
        """Return a spoken answer if `text` is a question about the caller's own record, else None."""
        self.last_frame = {}                                         # structured facts of the last answer (for other languages)
        info = classify_intent(text)
        if info["intent"] == "not_record":
            return None
        if not self.verified:
            if self.enabled and not self.id_declined and not self.verifier.locked and not self.awaiting_id:
                # they want something from their record: ask for the ID now, keep the question, answer it once verified
                self.awaiting_id = True
                self.nudges = 0
                self.pending_complaint = text
                return ident.ASK_ID_FOR_RECORD
            return NO_RECORD_QUESTION
        if not self.can_disclose:
            return ident.LOW_ASSURANCE
        self._ctx_ready.wait(timeout=3.0)                            # normally already loaded
        use_llm = os.getenv("VOXERA_RECORD_LLM", "0") == "1"
        try:
            ans = self.svc.answer_record_question(self.patient["id"], text, use_llm=use_llm, llm=llm if use_llm else None)
        except Exception:                                            # noqa: BLE001
            return DB_DOWN
        self.record_answers += 1
        self.last_frame = dict(getattr(ans, "frame", {}) or {})
        print(f"[PATIENT_AI] record question answered found={ans.found} llm={ans.used_llm}")
        return ans.answer or None

    def audit(self, call_id: Optional[str], action: str) -> None:
        if not self.verified:
            return
        def run():
            try:
                self.svc.audit_access(patient_uuid=self.patient["id"], user_id=None, facility_id=None, role=None,
                                      reason="patient_self", source="call", action=action, call_id=call_id)
            except Exception:                                        # noqa: BLE001
                pass
        self._pool.submit(run)

    # ---- triage ------------------------------------------------------------------
    def triage_step(self, text: str, last_assistant: str = ""):
        sig = self.voice.latest()
        if sig is not None:
            self.triage.set_voice(sig)
        return self.triage.process(text, last_assistant=last_assistant)

    # ---- voice signal ----------------------------------------------------------------
    def submit_audio(self, audio) -> None:
        """Fire-and-forget. Called AFTER Whisper so it never competes with STT."""
        try:
            self.voice.submit(audio, on_done=lambda s: self.voice_log.append(s) if s.available else None)
        except Exception:                                             # noqa: BLE001
            pass

    # ---- urgent (non-emergency) escalation --------------------------------------------
    def escalate_urgent(self, call_id: Optional[str], patient_uuid: Optional[str], patient_name: str) -> None:
        """URGENT_CLINICAL_REVIEW: flag the hospital WITHOUT creating an emergency case.
        Idempotent per call. Best-effort; never raises into the live call."""
        if not (call_id and patient_uuid) or self.urgent:
            return
        db = self.db
        s = self.triage.state
        why = ", ".join(s.red_flags) or (s.radiation or "") or s.severity or "clinician review advised"
        reason = f"[urgent_clinical_review] {s.chief_complaint or 'symptoms'}: {why}"
        try:
            existing = (db.supabase.table("referrals").select("id").ilike("notes", f"%urgent review from call {call_id}.%")
                        .limit(1).execute().data)
            if existing:
                self.urgent = existing[0]
                return
            fac = db.get_default_facility()
            fid = fac["id"] if fac else None
            a = db.create_ai_assessment(call_id=call_id, patient_id=patient_uuid, severity="high", risk_level="URGENT",
                                        symptoms_summary=reason, ai_summary=f"Voxera triage conclusion: {s.conclusion}. {reason}",
                                        ai_recommendation="Clinical review today; patient advised to seek urgent care if worse.",
                                        recommended_department="Emergency", confidence_score=0.7)
            ref = db.create_referral(patient_name=patient_name, reason=reason, urgency="high", patient_id=patient_uuid,
                                     receiving_facility_id=fid, recommended_department="Emergency",
                                     ai_summary=f"Voxera triage: {reason}", required_service="Urgent Clinical Review",
                                     notes=f"Auto-created by Voxera triage: urgent review from call {call_id}.",
                                     ai_assessment_id=a["id"] if a else None)
            db.add_referral_event(referral_id=ref["id"], event_type="referral_created",
                                  description=f"Voxera triage advised urgent clinical review ({why}).",
                                  new_status="pending", facility_id=fid, actor_source="system")
            if fid:
                db.create_referral_notification(referral_id=ref["id"], target_facility_id=fid,
                                                message="Voxera AI advises URGENT clinical review for a caller. Referral is pending.",
                                                type_="new_referral")
            self.urgent = ref
            print("[PATIENT_AI] urgent clinical review flagged to the hospital")
        except Exception as e:                                        # noqa: BLE001
            print(f"[PATIENT_AI] urgent flag failed: {type(e).__name__}")

    # ---- summary ------------------------------------------------------------------------
    def summary_extras(self) -> dict:
        out: dict = {}
        if self.voice_log:
            peak = max(self.voice_log, key=lambda s: (_AROUSAL_RANK.get(s.arousal_level, -1), s.confidence))
            out["voice_signal"] = {"arousal_level": peak.arousal_level, "confidence": peak.confidence,
                                   "signal_quality": peak.signal_quality, "turns_analyzed": len(self.voice_log),
                                   "note": "Conversational signal only; not a diagnostic indicator."}
        tc = self.triage.summary_context()
        if tc:
            out["clinical_context"] = tc
        rc: dict = {"patient_verified": self.verified, "identity_assurance": self.assurance}
        if self.ctx:
            comp = (self.triage.state.chief_complaint or "").split(" ")[0].lower()
            rel = [c for c in self.ctx.previous_consultations
                   if comp and comp in (str(c.get("chief_complaint")) + str(c.get("symptoms"))).lower()]
            if rel:
                rc["previous_relevant_consultation"] = {"date": rel[0].get("date"), "chief_complaint": rel[0].get("chief_complaint")}
            if self.ctx.active_medications:
                rc["relevant_medications"] = [m.get("medicine_name") for m in self.ctx.active_medications[:5]]
            if self.ctx.previous_prescriptions:
                p = self.ctx.previous_prescriptions[0]
                rc["relevant_prescription"] = {"medicine": p.get("medicine_name"), "date": p.get("date")}
        out["record_context"] = rc
        return out

    def close(self) -> None:
        try:
            self.voice.close()
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)
