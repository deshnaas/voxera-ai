# ============================================================
# VOXERA — SUPABASE MEMORY / DATABASE LAYER
# ============================================================
#
# Purpose:
#   Connect the local Voxera AI to the existing Supabase schema.
#
# Current database relationship:
#
#   patients
#       ↓
#   calls
#       ↓
#   conversation_turns
#
# This module deliberately does NOT contain:
#   - Whisper
#   - VAD
#   - TTS
#   - Qwen
#   - barge-in
#
# It is only the database layer.
# ============================================================

import os
from dotenv import load_dotenv
from supabase import create_client


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()


def _load_supabase_key():
    """Server-side key. Preferred name is SUPABASE_SERVICE_ROLE_KEY; older
    names are still accepted so an existing .env keeps working. The value is
    never logged."""
    for name in (
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_KEY",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_ANON_KEY",
    ):
        val = os.getenv(name)
        if val:
            return val, name
    return None, None


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY, _SUPABASE_KEY_SOURCE = _load_supabase_key()

if not SUPABASE_URL:
    raise RuntimeError(
        "SUPABASE_URL is missing from .env"
    )

if not SUPABASE_KEY:
    raise RuntimeError(
        "No Supabase key found in .env. Set SUPABASE_SERVICE_ROLE_KEY "
        "(server-side service-role key)."
    )


# ============================================================
# SUPABASE CLIENT
# ============================================================

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# ============================================================
# SCHEMA-TOLERANT INSERT
# ============================================================
# Different Supabase projects for this app differ slightly (e.g. the older
# project's `referrals` has a legacy text `patient` column, the new one does
# not). Rather than hard-code a schema, drop any column PostgREST reports as
# unknown (error PGRST204) and retry. `required` columns are never dropped.

import re as _re


def _insert_tolerant(table_name: str, data: dict, required=()):
    payload = dict(data)
    for _ in range(12):
        try:
            resp = supabase.table(table_name).insert(payload).execute()
            return resp.data[0] if resp.data else None
        except Exception as e:
            msg = getattr(e, "message", None) or str(e)
            m = _re.search(r"find the '([^']+)' column", msg)
            if m and m.group(1) in payload and m.group(1) not in required:
                dropped = m.group(1)
                payload.pop(dropped, None)
                print(f"[SUPABASE] {table_name}: column '{dropped}' not in this "
                      f"project's schema - retrying without it")
                continue
            raise
    raise RuntimeError(f"{table_name}: too many unknown columns to insert")


# ============================================================
# PATIENT FUNCTIONS
# ============================================================

def find_patient_by_phone(phone: str):
    """
    Find an existing patient using their phone number.

    Returns:
        dict | None
    """

    if not phone:
        return None

    response = (
        supabase
        .table("patients")
        .select("*")
        .eq("phone", phone)
        .limit(1)
        .execute()
    )

    if response.data:
        return response.data[0]

    return None


def create_patient(
    full_name: str,
    phone: str = None,
    preferred_language: str = "English"
):
    """
    Create a patient record.

    Returns:
        patient dict
    """

    data = {
        "full_name": full_name,
        "preferred_language": preferred_language,
    }

    if phone:
        data["phone"] = phone

    response = (
        supabase
        .table("patients")
        .insert(data)
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Supabase did not return the created patient."
        )

    return response.data[0]


def get_or_create_patient(
    full_name: str,
    phone: str,
    preferred_language: str = "English"
):
    """
    Find a patient by phone.

    If the patient does not exist, create one.

    This is useful for the current local microphone demo.

    Later, when PSTN/backend integration is available,
    the backend can provide the patient_id directly.
    """

    patient = find_patient_by_phone(phone)

    if patient:
        return patient

    return create_patient(
        full_name=full_name,
        phone=phone,
        preferred_language=preferred_language
    )


# ============================================================
# CALL FUNCTIONS
# ============================================================

def create_call(
    patient_id: str,
    language: str = "English",
    call_type: str = "initial_assessment",
    facility_id=None,
    referral_id=None
):
    """
    Create a new Voxera call/session.

    patient_id is REQUIRED: the `calls` table enforces patient_id NOT NULL.
    Callers already get/create the patient at call start (get_or_create_patient)
    and must pass that UUID here.

    Returns:
        call dict
    """

    if not patient_id:
        raise ValueError(
            "create_call requires patient_id (calls.patient_id is NOT NULL). "
            "Call get_or_create_patient() first and pass its id."
        )

    data = {
        "patient_id": patient_id,
        "call_type": call_type,
        "status": "in_progress",
        "language": language,
    }

    if facility_id:
        data["facility_id"] = facility_id

    if referral_id:
        data["referral_id"] = referral_id

    response = (
        supabase
        .table("calls")
        .insert(data)
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Supabase did not return the created call."
        )

    return response.data[0]


# ============================================================
# CONVERSATION FUNCTIONS
# ============================================================

def get_conversation(
    call_id: str
):
    """
    Retrieve the complete conversation for a call.

    Returns:
        list of dictionaries ordered by sequence_number
    """

    response = (
        supabase
        .table("conversation_turns")
        .select(
            "id, speaker, message, sequence_number, created_at"
        )
        .eq("call_id", call_id)
        .order("sequence_number")
        .execute()
    )

    return response.data or []


def get_recent_conversation(
    call_id: str,
    limit: int = 8
):
    """
    Retrieve the most recent conversation turns.

    The database remains the source of truth.
    The AI can use a small recent window for latency.
    """

    turns = get_conversation(call_id)

    if len(turns) <= limit:
        return turns

    return turns[-limit:]


def get_next_sequence_number(
    call_id: str
):
    """
    Determine the next conversation sequence number.
    """

    turns = get_conversation(call_id)

    if not turns:
        return 1

    return max(
        turn["sequence_number"]
        for turn in turns
    ) + 1


def save_turn(
    call_id: str,
    speaker: str,
    message: str,
    sequence_number: int = None
):
    """
    Save one patient/AI/system conversation turn.

    speaker must be:
        patient
        ai
        system
    """

    if speaker not in (
        "patient",
        "ai",
        "system"
    ):
        raise ValueError(
            "speaker must be patient, ai, or system"
        )

    if not message or not message.strip():
        raise ValueError(
            "Cannot save an empty conversation turn."
        )

    if sequence_number is None:
        sequence_number = get_next_sequence_number(
            call_id
        )

    response = (
        supabase
        .table("conversation_turns")
        .insert({
            "call_id": call_id,
            "speaker": speaker,
            "message": message.strip(),
            "sequence_number": sequence_number,
        })
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Supabase did not return the saved conversation turn."
        )

    return response.data[0]


# ============================================================
# CALL STATUS
# ============================================================

def update_call_status(
    call_id: str,
    status: str,
    outcome: str = None
):
    """
    Update the status of a call.

    Valid statuses from your schema include:
        initiated
        in_progress
        completed
        failed
        no_answer
    """

    valid_statuses = {
        "initiated",
        "in_progress",
        "completed",
        "failed",
        "no_answer",
    }

    if status not in valid_statuses:
        raise ValueError(
            f"Invalid call status: {status}"
        )

    data = {
        "status": status,
    }

    if outcome is not None:
        data["outcome"] = outcome

    response = (
        supabase
        .table("calls")
        .update(data)
        .eq("id", call_id)
        .execute()
    )

    return response.data


# ============================================================
# CALL METRICS  (non-blocking demo telemetry)
# ============================================================

def update_call_metrics(call_id: str, **fields):
    """Best-effort update of the numeric telemetry columns on `calls`
    (ai_response_count, average_response_time_ms, interruption_count,
    emergency_checks_count, call_success, call_duration_seconds, ended_at).
    Silently ignores unknown columns / transient errors."""

    allowed = {
        "ai_response_count",
        "average_response_time_ms",
        "interruption_count",
        "emergency_checks_count",
        "call_success",
        "call_duration_seconds",
        "ended_at",
        "outcome",
    }

    data = {k: v for k, v in fields.items() if k in allowed and v is not None}

    if not data:
        return None

    return (
        supabase
        .table("calls")
        .update(data)
        .eq("id", call_id)
        .execute()
        .data
    )


# ============================================================
# FACILITIES
# ============================================================

def get_default_facility():
    """The facility that receives Voxera's referrals / emergencies.

    The hospital dashboard filters every view by the logged-in staff member's
    facility_id, so this MUST be a facility that has a `hospital_users` row.
    Set VOXERA_RECEIVING_FACILITY_ID in .env for the demo; otherwise the first
    facility that has a hospital_users mapping, else the first operational one.
    """

    override = os.getenv("VOXERA_RECEIVING_FACILITY_ID", "").strip()
    if override:
        try:
            r = (supabase.table("facilities").select("id, name, type")
                 .eq("id", override).limit(1).execute())
            if r.data:
                return r.data[0]
        except Exception:
            pass

    try:
        hu = supabase.table("hospital_users").select("facility_id").limit(1).execute()
        if hu.data and hu.data[0].get("facility_id"):
            r = (supabase.table("facilities").select("id, name, type")
                 .eq("id", hu.data[0]["facility_id"]).limit(1).execute())
            if r.data:
                return r.data[0]
    except Exception:
        pass

    try:
        res = (supabase.table("facilities").select("id, name, type")
               .eq("operational_status", True).limit(1).execute())
        if res.data:
            return res.data[0]
        res = supabase.table("facilities").select("id, name, type").limit(1).execute()
        return res.data[0] if res.data else None
    except Exception:
        return None


# ============================================================
# EMERGENCY REFERRAL / ESCALATION
# ============================================================

def create_referral(
    patient_name: str,
    reason: str,
    urgency: str = "high",
    patient_id: str = None,
    receiving_facility_id: str = None,
    recommended_department: str = None,
    ai_summary: str = None,
    ai_recommendation: str = None,
    required_service: str = "Emergency Care",
    notes: str = None,
    ai_assessment_id: str = None,
):
    """Create a referral row for an escalated emergency.

    Values use the hospital dashboard's convention (lowercase): the dashboard
    filters referrals with `status == "pending"` and treats
    `urgency in ("high","emergency")` as high-risk. `patient_name` is accepted
    for back-compat but the new schema links via patient_id only.
    """

    data = {
        "reason": (reason or "Escalated by Voxera voice agent")[:1000],
        "urgency": (urgency or "high").lower(),
        "status": "pending",
        "required_service": required_service,
        "patient": patient_name or "Unknown caller",   # dropped if column absent
    }

    if patient_id:
        data["patient_id"] = patient_id
    if receiving_facility_id:
        data["receiving_facility_id"] = receiving_facility_id
    if recommended_department:
        data["recommended_department"] = recommended_department
    if ai_summary:
        data["ai_summary"] = ai_summary[:2000]
    if ai_recommendation:
        data["ai_recommendation"] = ai_recommendation[:2000]
    if notes:
        data["notes"] = notes[:2000]
    if ai_assessment_id:
        data["ai_assessment_id"] = ai_assessment_id

    row = _insert_tolerant("referrals", data, required=("reason", "urgency"))
    if not row:
        raise RuntimeError("Supabase did not return the created referral.")
    return row


def create_ai_assessment(
    call_id: str,
    patient_id: str = None,
    severity: str = None,
    risk_level: str = None,
    symptoms_summary: str = None,
    ai_summary: str = None,
    ai_recommendation: str = None,
    recommended_department: str = None,
    confidence_score: float = None,
):
    """Per-call clinical assessment row the dashboard's referral detail page
    reads via `referrals.ai_assessment_id`. `call_id` is NOT NULL."""

    data = {"call_id": call_id}
    for k, v in (
        ("patient_id", patient_id),
        ("severity", severity),
        ("risk_level", risk_level),
        ("symptoms_summary", (symptoms_summary or "")[:2000] or None),
        ("ai_summary", (ai_summary or "")[:2000] or None),
        ("ai_recommendation", (ai_recommendation or "")[:2000] or None),
        ("recommended_department", recommended_department),
        ("confidence_score", confidence_score),
    ):
        if v is not None:
            data[k] = v
    return _insert_tolerant("ai_assessments", data, required=("call_id",))


def create_emergency_case(
    patient_id: str,
    facility_id: str,
    referral_id: str = None,
    priority: str = "critical",
    symptoms_summary: str = None,
    immediate_action: str = None,
):
    """Row for the dashboard's Emergency page (filtered by facility_id +
    status == 'active')."""

    data = {
        "patient_id": patient_id,
        "facility_id": facility_id,
        "priority": (priority or "critical").lower(),
        "status": "active",
    }
    if referral_id:
        data["referral_id"] = referral_id
    if symptoms_summary:
        data["symptoms_summary"] = symptoms_summary[:2000]
    if immediate_action:
        data["immediate_action"] = immediate_action[:2000]
    return _insert_tolerant("emergency_cases", data,
                            required=("patient_id", "facility_id"))


def get_latest_referral(patient_id: str):
    """Most recent referral for a patient (for the end-of-call summary)."""
    try:
        r = (supabase.table("referrals").select("*")
             .eq("patient_id", patient_id)
             .order("created_at", desc=True).limit(1).execute())
        return r.data[0] if r.data else None
    except Exception:
        return None


def create_referral_notification(
    referral_id: str,
    target_facility_id: str,
    message: str,
    type_: str = "new_referral",
):
    """Row that fires the dashboard's realtime "New Patient Referral" popup
    (Supabase realtime INSERT on referral_notifications)."""

    data = {
        "referral_id": referral_id,
        "target_facility_id": target_facility_id,
        "message": (message or "New referral from Voxera AI.")[:1000],
        "type": type_,
        "is_read": False,
    }
    return _insert_tolerant("referral_notifications", data,
                            required=("target_facility_id", "message"))


def add_referral_event(
    referral_id: str,
    event_type: str,
    description: str,
    new_status: str = None,
    previous_status: str = None,
    facility_id: str = None,
    actor_source: str = "system",
):
    """Append an audit event for a referral (the hospital-side timeline)."""

    data = {
        "referral_id": referral_id,
        "event_type": event_type,
        "description": (description or "")[:2000],
        "actor_source": actor_source,
    }

    if new_status:
        data["new_status"] = new_status
    if previous_status:
        data["previous_status"] = previous_status
    if facility_id:
        data["facility_id"] = facility_id

    return _insert_tolerant("referral_events", data,
                            required=("referral_id", "event_type"))


def escalate_emergency(
    call_id: str,
    patient_name: str,
    trigger_text: str,
    category: str,
    recommended_department: str = None,
    patient_id: str = None,
    conversation_summary: str = None,
    immediate_action: str = None,
):
    """Persist a full emergency escalation the hospital dashboard can see:

        ai_assessments -> referrals (+ ai_assessment_id)
                       -> referral_events
                       -> emergency_cases        (Emergency page)
                       -> referral_notifications (realtime popup + Notifications)
                       -> calls.outcome

    Best-effort: never raises into the live call. Returns the referral dict.
    """

    # Idempotency: one escalation per call. A flaky connection / re-submit
    # must not create duplicate referrals + emergency_cases.
    try:
        existing = (supabase.table("referrals").select("*")
                    .ilike("notes", f"%from call {call_id}.%")
                    .limit(1).execute().data)
        if existing:
            print(f"[SUPABASE] escalation for call {call_id} already exists "
                  f"(referral {existing[0]['id']}); not duplicating")
            return existing[0]
    except Exception:
        pass

    facility = get_default_facility()
    facility_id = facility["id"] if facility else None

    ai_summary = conversation_summary or (
        f"Voxera voice agent detected a possible emergency ({category}). "
        f"Patient statement: \"{trigger_text}\"."
    )
    ai_reco = (
        "Immediate clinical review. Patient was advised to contact emergency "
        "services / attend the nearest emergency room."
    )

    assessment_id = None
    try:
        a = create_ai_assessment(
            call_id=call_id, patient_id=patient_id,
            severity="high", risk_level="EMERGENCY",
            symptoms_summary=trigger_text,
            ai_summary=ai_summary, ai_recommendation=ai_reco,
            recommended_department=recommended_department,
            confidence_score=0.98,
        )
        assessment_id = a["id"] if a else None
    except Exception as e:
        print(f"[SUPABASE] ai_assessment insert failed: {e}")

    try:
        referral = create_referral(
            patient_name=patient_name,
            reason=f"[{category}] {trigger_text}",
            urgency="high",
            patient_id=patient_id,
            receiving_facility_id=facility_id,
            recommended_department=recommended_department,
            ai_summary=ai_summary,
            ai_recommendation=ai_reco,
            notes=f"Auto-created by Voxera voice agent from call {call_id}.",
            ai_assessment_id=assessment_id,
        )
    except Exception as e:
        print(f"[SUPABASE] referral insert failed: {e}")
        return None

    try:
        add_referral_event(
            referral_id=referral["id"], event_type="referral_created",
            description=(f"Emergency detected by Voxera during call {call_id}. "
                         f"Trigger: \"{trigger_text}\" ({category})."),
            new_status="pending", facility_id=facility_id, actor_source="system",
        )
    except Exception as e:
        print(f"[SUPABASE] referral_event insert failed: {e}")

    if facility_id and patient_id:
        try:
            create_emergency_case(
                patient_id=patient_id, facility_id=facility_id,
                referral_id=referral["id"], priority="critical",
                symptoms_summary=trigger_text,
                immediate_action=immediate_action or ai_reco,
            )
        except Exception as e:
            print(f"[SUPABASE] emergency_case insert failed: {e}")

    if facility_id:
        try:
            create_referral_notification(
                referral_id=referral["id"], target_facility_id=facility_id,
                message=(f"Voxera AI flagged a possible {category.replace('_', ' ')} "
                         f"emergency for {patient_name}. Referral is pending review."),
                type_="new_referral",
            )
        except Exception as e:
            print(f"[SUPABASE] referral_notification insert failed: {e}")

    try:
        update_call_status(call_id, "in_progress", outcome="emergency_escalated")
    except Exception:
        pass

    return referral


# ============================================================
# APPOINTMENTS  (hospital-side outbound flow)
# ============================================================

def get_default_doctor_department():
    return ("General Medicine", "Care Team")


def create_appointment(
    patient_name: str,
    appointment_date: str,
    appointment_time: str,
    facility_id: str = None,
    department: str = "General Medicine",
    doctor_name: str = "Care Team",
    reason: str = None,
    patient_id: str = None,
    status: str = "scheduled",
):
    """Create an appointment (used to seed the outbound-call demo)."""

    if facility_id is None:
        fac = get_default_facility()
        facility_id = fac["id"] if fac else None

    data = {
        "patient_name": patient_name,
        "appointment_date": appointment_date,   # 'YYYY-MM-DD'
        "appointment_time": appointment_time,   # 'HH:MM:SS'
        "department": department,
        "doctor_name": doctor_name,
        "status": status,
    }
    if facility_id:
        data["facility_id"] = facility_id
    if reason:
        data["reason"] = reason
    if patient_id:
        data["patient_id"] = patient_id

    row = _insert_tolerant("appointments", data, required=())
    if not row:
        raise RuntimeError("Supabase did not return the created appointment.")
    return row


def get_appointment(appointment_id: str):
    res = (
        supabase
        .table("appointments")
        .select("*")
        .eq("id", appointment_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def find_upcoming_appointment(patient_name: str = None, patient_id: str = None):
    """Most recent non-terminal appointment for the demo patient."""

    def _run(filter_col=None, filter_val=None):
        q = supabase.table("appointments").select("*")
        if filter_col and filter_val:
            q = q.eq(filter_col, filter_val)
        return q.order("created_at", desc=True).limit(10).execute()

    try:
        if patient_id:
            res = _run("patient_id", patient_id)
        elif patient_name:
            res = _run("patient_name", patient_name)
        else:
            res = _run()
    except Exception:
        # column may not exist in this project's schema - fall back to latest
        res = _run("patient_id", patient_id) if patient_id else _run()

    for row in res.data or []:
        if str(row.get("status", "")).lower() in (
            "scheduled", "booked", "pending", "rescheduled"
        ):
            return row

    return (res.data or [None])[0]


def update_appointment(
    appointment_id: str,
    status: str = None,
    appointment_date: str = None,
    appointment_time: str = None,
    follow_up_date: str = None,
):
    data = {}
    if status:
        data["status"] = status
    if appointment_date:
        data["appointment_date"] = appointment_date
    if appointment_time:
        data["appointment_time"] = appointment_time
    if follow_up_date:
        data["follow_up_date"] = follow_up_date

    if not data:
        return None

    return (
        supabase
        .table("appointments")
        .update(data)
        .eq("id", appointment_id)
        .execute()
        .data
    )


# ============================================================
# HEALTH CHECK
# ============================================================

def test_connection():
    """
    Simple database connectivity test.
    """

    response = (
        supabase
        .table("patients")
        .select("id")
        .limit(1)
        .execute()
    )

    return response.data is not None


# ============================================================
# MODULE TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 65)
    print("VOXERA SUPABASE MEMORY MODULE")
    print("=" * 65)
    print()

    # Show which project + which env var name supplied the key. Never the key.
    _host = SUPABASE_URL.split("//")[-1].split(".")[0] if SUPABASE_URL else "?"
    print(f"[db] Connecting to project '{_host}' using {_SUPABASE_KEY_SOURCE} ...")

    if test_connection():
        print("✅ Supabase connection successful.")
    else:
        print("⚠️ Supabase responded unexpectedly.")

    print()
    print("✅ voxera_supabase.py is ready.")
    print()