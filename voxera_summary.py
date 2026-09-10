# ============================================================
# VOXERA — STRUCTURED CALL SUMMARY
# ============================================================
#
# After a call, build ONE structured summary from information that is
# ACTUALLY present:
#   - conversation_turns / rolling transcript
#   - deterministic extracted facts   (voxera_core.extract_facts)
#   - the frozen emergency result      (voxera_emergency)
#   - the curated care/OTC result      (voxera_care)
#   - referral / appointment rows created this call
#
# NOTHING here invents clinical facts and NOTHING here calls an LLM.
# The OTC block is explicitly labelled as guidance, never a prescription.
#
#   build_summary(...)  -> dict   (the {chief_concern, symptoms, ...} shape)
#   render_text(summary) -> str   (human-readable card, deterministic)
#   persist_summary(...) -> None  (writes to Supabase; schema-tolerant)
# ============================================================

import json
import datetime as _dt


# ------------------------------------------------------------
# BUILD
# ------------------------------------------------------------

def build_summary(patient=None, facts=None, transcript="", turns=None,
                  emergency=None, care_events=None, referral=None,
                  appointment=None, outcome=None, call=None):
    """Return the structured summary dict. All inputs optional / defensive."""
    facts = facts or {}
    care_events = care_events or []
    turns = turns or []

    # -- chief concern: the first thing the patient actually said --------
    chief = ""
    for t in turns:
        if str(t.get("speaker", t.get("role", ""))).lower() in ("patient", "user"):
            chief = str(t.get("message", t.get("content", ""))).strip()
            break
    if not chief and transcript:
        for line in transcript.splitlines():
            if line.upper().startswith("PATIENT:"):
                chief = line.split(":", 1)[1].strip()
                break
    chief = chief[:400]

    # -- patient profile: only fields we truly have ---------------------
    prof = {}
    if patient:
        for k in ("full_name", "phone", "gender", "preferred_language",
                  "district", "date_of_birth"):
            if patient.get(k):
                prof[k] = patient[k]
    if facts.get("patient_name") and "full_name" not in prof:
        prof["full_name"] = facts["patient_name"]
    if facts.get("age") is not None:
        prof["age"] = facts["age"]
    if facts.get("is_child"):
        prof["is_child"] = True
    if facts.get("pregnant"):
        prof["pregnant"] = True
    if facts.get("allergies"):
        prof["allergies"] = facts["allergies"]
    if facts.get("conditions"):
        prof["conditions"] = facts["conditions"]

    # -- emergency ----------------------------------------------------
    if emergency is not None:
        emergency_status = {
            "detected": True,
            "category": getattr(emergency, "category", None),
            "severity": getattr(emergency, "severity", None),
            "trigger_phrase": getattr(emergency, "trigger", None),
            "recommended_department": getattr(emergency, "recommended_department", None),
            "note": "Deterministic emergency layer fired. Patient advised to "
                    "seek emergency help immediately.",
        }
    else:
        emergency_status = {"detected": False,
                            "note": "No emergency signal detected."}

    # -- care / OTC given -------------------------------------------
    care_given = []
    otc_guidance = []
    for ev in care_events:
        if ev.get("label"):
            care_given.append({
                "topic": ev.get("topic"),
                "label": ev.get("label"),
                "steps": ev.get("steps", []),
                "escalation": ev.get("escalation", []),
            })
        otc = ev.get("otc")
        if otc:
            otc_guidance.append({
                "type": "OTC guidance (not a prescription)",
                "deferred": bool(otc.get("deferred")),
                "items": otc.get("items", []),
                "spoken": otc.get("spoken", ""),
                "safety": "No numeric dose generated. 'As directed on the "
                          "packet' only." if not otc.get("deferred")
                          else "No medicine suggested; deferred to a "
                               "pharmacist / clinician.",
            })

    # -- follow-up -------------------------------------------------
    follow_up = None
    if appointment:
        follow_up = {
            "type": "appointment",
            "status": appointment.get("status"),
            "date": appointment.get("appointment_date"),
            "time": appointment.get("appointment_time"),
            "department": appointment.get("department"),
        }
    elif emergency is not None:
        follow_up = {"type": "escalation",
                     "note": "Emergency referral raised to the hospital."}
    elif care_given:
        esc = care_given[0].get("escalation") or []
        follow_up = {"type": "self_care",
                     "note": "Seek medical care if: " + esc[0] if esc else
                             "Home care advised; seek care if symptoms worsen."}

    ref_block = None
    if referral:
        ref_block = {
            "id": referral.get("id"),
            "status": referral.get("status"),
            "urgency": referral.get("urgency"),
            "recommended_department": referral.get("recommended_department"),
            "receiving_facility_id": referral.get("receiving_facility_id"),
        }

    return {
        "chief_concern": chief or "Not clearly stated.",
        "symptoms": facts.get("symptoms", []),
        "duration": facts.get("duration"),
        "temperature_f": facts.get("temperature_f"),
        "medications_mentioned": facts.get("medications", []),
        "patient_profile": prof,
        "emergency_status": emergency_status,
        "care_given": care_given,
        "otc_guidance": otc_guidance,
        "referral": ref_block,
        "appointment": follow_up if follow_up and follow_up.get("type") == "appointment" else None,
        "follow_up": follow_up,
        "call_outcome": outcome,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "generated_by": "voxera_summary (deterministic, no LLM)",
    }


# ------------------------------------------------------------
# RENDER (deterministic text card — the dashboard can also render from JSON)
# ------------------------------------------------------------

def render_text(s):
    L = []
    L.append("CALL SUMMARY")
    L.append("")
    L.append("Chief concern:")
    L.append(f"  {s.get('chief_concern', 'Not stated.')}")

    syms = s.get("symptoms") or []
    L.append("")
    L.append("Symptoms:")
    if syms:
        L += [f"  - {x}" for x in syms]
    else:
        L.append("  - none explicitly reported")
    if s.get("duration"):
        L.append(f"  duration: {s['duration']}")
    if s.get("temperature_f"):
        L.append(f"  temperature: {s['temperature_f']} F (patient-reported)")

    prof = s.get("patient_profile") or {}
    L.append("")
    L.append("Patient:")
    who = prof.get("full_name", "Unknown")
    bits = []
    if prof.get("age") is not None:
        bits.append(f"age {prof['age']}")
    if prof.get("is_child"):
        bits.append("child")
    if prof.get("pregnant"):
        bits.append("pregnant")
    L.append(f"  {who}" + (f" ({', '.join(bits)})" if bits else ""))
    if prof.get("allergies"):
        L.append(f"  allergies: {', '.join(prof['allergies'])}")
    if prof.get("conditions"):
        L.append(f"  conditions: {', '.join(prof['conditions'])}")

    es = s.get("emergency_status") or {}
    L.append("")
    L.append("Emergency:")
    if es.get("detected"):
        L.append(f"  DETECTED - {es.get('category')} ({es.get('severity')}).")
        L.append(f"  trigger phrase: \"{es.get('trigger_phrase')}\"")
        if es.get("recommended_department"):
            L.append(f"  recommended department: {es['recommended_department']}")
    else:
        L.append("  No emergency signal detected.")

    cg = s.get("care_given") or []
    L.append("")
    L.append("Guidance provided:")
    if cg:
        for c in cg:
            L.append(f"  - {c.get('label')}")
            for st in (c.get("steps") or [])[:3]:
                L.append(f"      {st}")
    elif not es.get("detected"):
        L.append("  - general conversational guidance")
    else:
        L.append("  - emergency safety response")

    otc = s.get("otc_guidance") or []
    L.append("")
    L.append("Medication guidance (OTC only - NOT a prescription):")
    if otc:
        for o in otc:
            if o.get("deferred"):
                L.append("  - deferred to a pharmacist / clinician "
                         "(no medicine suggested)")
            else:
                L.append(f"  - {', '.join(o.get('items') or []) or 'supportive care'}"
                         f" - {o.get('safety')}")
            if o.get("spoken"):
                L.append(f"      spoken: {o['spoken']}")
    else:
        L.append("  - none")

    fu = s.get("follow_up")
    L.append("")
    L.append("Follow-up:")
    if fu:
        if fu.get("type") == "appointment":
            L.append(f"  appointment {fu.get('status')} for {fu.get('date')} "
                     f"{fu.get('time') or ''} ({fu.get('department') or 'clinic'})")
        else:
            L.append(f"  {fu.get('note')}")
    else:
        L.append("  none recorded")

    return "\n".join(L)


# ------------------------------------------------------------
# PERSIST  (schema-tolerant; never raises)
# ------------------------------------------------------------

def persist_summary(db, call_id, patient_id, summary, text):
    """Store the summary. Prefer a dedicated `call_summaries` table; if it
    does not exist yet, fall back to a `conversation_turns` system row that
    carries the JSON so the dashboard can still read it."""
    payload = {
        "call_id": call_id,
        "patient_id": patient_id,
        "chief_concern": summary.get("chief_concern"),
        "summary_json": summary,
        "summary_text": text,
        "emergency_detected": bool(summary.get("emergency_status", {}).get("detected")),
        "call_outcome": summary.get("call_outcome"),
    }
    try:
        row = db._insert_tolerant("call_summaries", payload,
                                  required=("call_id",))
        if row:
            print(f"[SUMMARY] stored in call_summaries ({row['id']})")
            return
    except Exception as e:
        print(f"[SUMMARY] call_summaries unavailable ({str(e)[:70]}); "
              "falling back to a system conversation turn")

    # Fallback: a system turn carrying the JSON (always readable by the dashboard)
    try:
        db.save_turn(call_id, "system",
                     "CALL_SUMMARY " + json.dumps(summary, ensure_ascii=False)[:9000])
        print("[SUMMARY] stored as a system conversation_turn")
    except Exception as e:
        print(f"[SUMMARY] could not persist summary at all: {e}")


# ------------------------------------------------------------
if __name__ == "__main__":
    demo = build_summary(
        patient={"full_name": "Test Child", "phone": "999"},
        facts={"symptoms": ["skin abrasion", "bleeding"], "is_child": True,
               "age": 6},
        transcript="PATIENT: my child fell off his bike and his knee is bleeding\n"
                   "VOXERA: rinse it under water...",
        care_events=[{
            "topic": "minor_abrasion", "label": "a graze or scrape from a fall",
            "steps": ["Rinse it gently under clean running water.",
                      "Press a clean cloth firmly for a few minutes."],
            "escalation": ["a child was hit by a vehicle or hit their head"],
            "otc": {"deferred": True, "items": [],
                    "spoken": "For a child, check with a pharmacist or doctor."},
        }],
        outcome="completed",
    )
    print(json.dumps(demo, indent=2)[:1500])
    print("\n" + "=" * 50 + "\n")
    print(render_text(demo))
