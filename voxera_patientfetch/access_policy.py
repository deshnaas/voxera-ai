"""Deterministic record-access policy (minimum necessary) + audit helpers.

A patient ID alone is NEVER authorisation. Access needs:
    authenticated staff user -> hospital_users(facility, role) -> policy -> audit

The table below is data, not code paths, so it can be tightened later without
touching the API or the dashboard. The dashboard receives only the sections the
policy grants; it cannot ask for more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

# Sections of a patient record
IDENTITY = "identity"            # patient_id, name, age, gender, phone
ALLERGIES = "allergies"          # allergies + blood group (essential safety info)
MEDICATIONS = "medications"      # active + unverified medicines with provenance
PRESCRIPTIONS = "prescriptions"
CONSULTATIONS = "consultations"  # derived encounter summaries (no raw transcript)
REFERRALS = "referrals"
APPOINTMENTS = "appointments"
CALLS = "calls"                  # Voxera call list + summaries
TRANSCRIPTS = "transcripts"      # raw conversation turns
DOCUMENTS = "documents"
AUDIT = "audit"                  # who accessed this record
NOTES = "notes"                  # internal clinical notes

ALL = {IDENTITY, ALLERGIES, MEDICATIONS, PRESCRIPTIONS, CONSULTATIONS, REFERRALS,
       APPOINTMENTS, CALLS, TRANSCRIPTS, DOCUMENTS, AUDIT, NOTES}

ROLES = ("admin", "doctor", "nurse", "receptionist", "operator")

REASONS = ("patient_referred", "emergency_care", "follow_up", "other")

HOME_POLICY = {
    "doctor": set(ALL),
    "nurse": {IDENTITY, ALLERGIES, MEDICATIONS, PRESCRIPTIONS, CONSULTATIONS, REFERRALS, APPOINTMENTS},
    "receptionist": {IDENTITY, APPOINTMENTS},
    "operator": {IDENTITY, APPOINTMENTS},
    "admin": {IDENTITY, APPOINTMENTS, AUDIT},          # administrative, not clinical
}

# Another authorised facility: essentials only, and a reason is mandatory.
EXTERNAL_POLICY = {
    "doctor": {IDENTITY, ALLERGIES, MEDICATIONS, PRESCRIPTIONS, CONSULTATIONS, REFERRALS},
    "nurse": {IDENTITY, ALLERGIES, MEDICATIONS},
    "receptionist": {IDENTITY},
    "operator": {IDENTITY},
    "admin": {IDENTITY},
}

# Fields of the identity section per relationship (external never gets phone/DOB)
IDENTITY_FIELDS_HOME = ("id", "patient_id", "full_name", "age", "gender", "phone", "preferred_language",
                        "village_or_locality", "district")
IDENTITY_FIELDS_EXTERNAL = ("id", "patient_id", "full_name", "age", "gender")


@dataclass
class AccessDecision:
    allowed: bool
    relationship: str                     # home | external | none
    sections: set = field(default_factory=set)
    requires_reason: bool = False
    reason_ok: bool = True
    label: str = ""                       # banner text for the UI
    deny_reason: str = ""

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["sections"] = sorted(self.sections)
        return d


def normalise_role(role: Optional[str]) -> Optional[str]:
    """Known roles only. The legacy value 'staff' (every pre-existing hospital_users row) is
    mapped via VOXERA_LEGACY_STAFF_ROLE (default 'doctor', matching the SQL policies so the
    current demo accounts keep working). Assign real roles to tighten this."""
    import os
    r = (role or "").strip().lower()
    if r == "staff":
        r = os.getenv("VOXERA_LEGACY_STAFF_ROLE", "doctor").strip().lower()
    return r if r in ROLES else None


def decide(role: Optional[str], requester_facility: Optional[str],
           patient_facilities: Iterable[str], reason: Optional[str] = None,
           reason_text: Optional[str] = None) -> AccessDecision:
    role_n = normalise_role(role)
    if not role_n or not requester_facility:
        return AccessDecision(False, "none", deny_reason="not_staff")

    facilities = set(patient_facilities or [])
    home = requester_facility in facilities

    if home:
        return AccessDecision(True, "home", set(HOME_POLICY[role_n]), False, True, "Patient record")

    sections = set(EXTERNAL_POLICY[role_n])
    reason_ok = reason in REASONS and (reason != "other" or bool((reason_text or "").strip()))
    label = "External patient record — accessing records from another facility"
    if not reason_ok:
        return AccessDecision(False, "external", sections, True, False, label, "reason_required")
    return AccessDecision(True, "external", sections, True, True, label)


def redact_identity(patient: dict, relationship: str) -> dict:
    fields = IDENTITY_FIELDS_HOME if relationship == "home" else IDENTITY_FIELDS_EXTERNAL
    return {k: patient.get(k) for k in fields if k in patient}


def search_projection(patient: dict, role: Optional[str], summary: Optional[dict] = None) -> dict:
    """What a search RESULT may show. Search is identity-level only for every role;
    clinical counts appear only for roles whose HOME policy includes medications."""
    r = normalise_role(role)
    row = {k: patient.get(k) for k in ("id", "patient_id", "full_name", "age", "gender")}
    if summary and r and MEDICATIONS in HOME_POLICY.get(r, set()):
        row.update({k: summary.get(k) for k in ("last_consultation", "active_medications", "allergies")})
    return row


def build_audit_row(*, patient_uuid: str, user_id: Optional[str], facility_id: Optional[str],
                    role: Optional[str], reason: Optional[str], reason_text: Optional[str],
                    source: str, decision: Optional[AccessDecision], action: str,
                    call_id: Optional[str] = None, meta: Optional[dict] = None) -> dict:
    """One audit row. Contains NO clinical content, only who/what/why/when-outcome."""
    return {
        "patient_id": patient_uuid,
        "accessed_by_user_id": user_id,
        "facility_id": facility_id,
        "role": normalise_role(role),
        "access_reason": (reason or "") + (f": {reason_text.strip()[:200]}" if reason_text else ""),
        "source": source,                       # dashboard | call | api
        "action": action,                       # search | open_record | ask_question | upload | verify | denied
        "relationship": decision.relationship if decision else None,
        "granted": bool(decision.allowed) if decision else None,
        "sections": sorted(decision.sections) if decision and decision.allowed else [],
        "call_id": call_id,
        "meta": meta or {},
    }
