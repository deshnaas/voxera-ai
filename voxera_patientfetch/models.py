"""Shared, dependency-free data types for the Voxera Patient Intelligence AI.

Everything here is plain dataclasses / enums so it can be serialised into the
call summary and unit-tested without Supabase, torch or an LLM.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


# ------------------------------------------------------------------
# Provenance
# ------------------------------------------------------------------

class Source(str, enum.Enum):
    """Where a medication / fact came from. Never blur these in the UI or in speech."""
    CLINICIAN = "clinician"                  # entered by hospital staff
    OCR = "ocr"                              # extracted from an uploaded document
    OCR_VERIFIED = "ocr_verified"            # OCR candidate a clinician confirmed
    PATIENT_REPORTED = "patient_reported"    # told to Voxera on a call
    IMPORTED = "imported"                    # bulk import / other system
    AI_SUMMARY = "ai_summary"                # generated summary (never authoritative)


class Verification(str, enum.Enum):
    PENDING = "pending"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


# Only these sources may ever be described as "prescribed by a doctor".
CLINICIAN_AUTHORED = {Source.CLINICIAN.value, Source.OCR_VERIFIED.value}


def is_clinician_authored(source: Optional[str]) -> bool:
    return (source or "") in CLINICIAN_AUTHORED


# ------------------------------------------------------------------
# Identity
# ------------------------------------------------------------------

@dataclass
class VerificationResult:
    verified: bool
    reason: str                       # ok | not_found | ambiguous | bad_format | locked | error
    patient: Optional[dict] = None    # only populated when verified
    attempts_used: int = 0
    attempts_left: int = 0
    phone_matched: Optional[bool] = None   # secondary signal only
    assurance: str = "none"          # high (id+phone) | standard (id, phone unknown) | low (phone mismatch) | none
    spoken: str = ""                  # what Voxera should say next


# ------------------------------------------------------------------
# Patient context (minimum-necessary snapshot used during a call)
# ------------------------------------------------------------------

@dataclass
class PatientContext:
    patient: dict = field(default_factory=dict)              # patient_id, first_name, age, sex
    allergies: list = field(default_factory=list)
    conditions: list = field(default_factory=list)
    active_medications: list = field(default_factory=list)  # confirmed / clinician only
    unverified_medications: list = field(default_factory=list)  # OCR-pending / patient-reported
    previous_prescriptions: list = field(default_factory=list)
    previous_consultations: list = field(default_factory=list)
    previous_calls: list = field(default_factory=list)
    recent_referrals: list = field(default_factory=list)
    recent_emergencies: list = field(default_factory=list)
    appointments: list = field(default_factory=list)
    relevant_documents: list = field(default_factory=list)
    loaded_at: float = 0.0
    partial: bool = False   # True if some tables were unavailable

    def to_dict(self) -> dict:
        return asdict(self)

    def llm_view(self, max_items: int = 3) -> dict:
        """Tiny PHI-minimised view that is safe to put in an LLM prompt."""
        p = self.patient or {}
        return {
            "age": p.get("age"),
            "sex": p.get("sex"),
            "allergies": self.allergies[:5],
            "conditions": self.conditions[:5],
            "active_medications": [m.get("medicine_name") for m in self.active_medications[:max_items]],
        }


# ------------------------------------------------------------------
# Record Q&A
# ------------------------------------------------------------------

@dataclass
class RecordAnswer:
    answer: str
    intent: str
    sources: list = field(default_factory=list)   # [{type,id,date,source}]
    confidence: float = 0.0
    found: bool = False
    conflict: bool = False
    used_llm: bool = False
    notes: list = field(default_factory=list)
    frame: dict = field(default_factory=dict)   # structured facts, so the answer can be worded in any language

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------
# Voice signal
# ------------------------------------------------------------------

@dataclass
class VoiceSignal:
    """Conversational signal ONLY. Not diagnostic. Never triggers an emergency."""
    available: bool = False
    arousal_level: str = "unavailable"     # low | moderate | high | unavailable
    emotion_signal: str = "uncertain"      # neutral | calm | sad | angry | high_arousal | uncertain
    confidence: float = 0.0
    speech_rate: Optional[float] = None    # syllable-like peaks / second
    rms: Optional[float] = None
    pitch_variability: Optional[float] = None   # coefficient of variation of F0
    signal_quality: str = "unknown"        # good | fair | poor | unknown
    reason: str = ""                       # why unavailable
    latency_ms: Optional[int] = None
    disclaimer: str = "Conversational signal only; not a diagnostic indicator."

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------
# Triage
# ------------------------------------------------------------------

class Conclusion(str, enum.Enum):
    EMERGENCY_ESCALATION = "EMERGENCY_ESCALATION"
    URGENT_CLINICAL_REVIEW = "URGENT_CLINICAL_REVIEW"
    ROUTINE_CLINICAL_REVIEW = "ROUTINE_CLINICAL_REVIEW"
    GENERAL_HOME_CARE = "GENERAL_HOME_CARE"
    RECORD_INFORMATION_ONLY = "RECORD_INFORMATION_ONLY"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


@dataclass
class ConversationClinicalState:
    patient_verified: bool = False
    chief_complaint: Optional[str] = None
    topic: Optional[str] = None                 # chest | abdomen | breathing | head | fever | generic
    symptoms: list = field(default_factory=list)
    onset: Optional[str] = None                 # sudden | gradual | <free>
    duration: Optional[str] = None
    severity: Optional[str] = None              # mild | moderate | severe
    quality: Optional[str] = None               # burning | pressure | sharp ...
    location: Optional[str] = None
    radiation: Optional[str] = None             # yes | no | <where>
    ongoing_now: Optional[bool] = None
    associated_symptoms: list = field(default_factory=list)
    red_flags: list = field(default_factory=list)
    negated_red_flags: list = field(default_factory=list)   # patient explicitly denied
    relevant_history: list = field(default_factory=list)
    current_medications: list = field(default_factory=list)
    allergies: list = field(default_factory=list)
    voice_arousal: Optional[str] = None
    voice_signal_confidence: Optional[float] = None
    emergency_status: str = "none"              # none | detected
    follow_up_questions: list = field(default_factory=list)   # asked (keys)
    questions_remaining: int = 0
    conclusion: Optional[str] = None
    next_action: Optional[str] = None
    asked_last: Optional[str] = None
    turns: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ConversationClinicalState":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)


def clean_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}
