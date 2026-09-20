"""Voxera Patient Intelligence (voxera_patientfetch).

Retrieval-first patient identity, record Q&A, prescription understanding,
access control/audit, voice-signal analysis and adaptive triage support.

It is an ADD-ON to the existing Voxera call flow: the frozen deterministic
emergency detector (voxera_emergency.py) stays the only emergency authority.
"""

from .models import (Conclusion, ConversationClinicalState, PatientContext,   # noqa: F401
                     RecordAnswer, Source, Verification, VerificationResult, VoiceSignal)

__version__ = "1.0.0"
