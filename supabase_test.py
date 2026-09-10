# ============================================================
# LOAD SUPABASE CONFIG
# ============================================================
# Reuse the single configured client from voxera_supabase so credential
# handling (SUPABASE_SERVICE_ROLE_KEY, with fallbacks) lives in one place
# and the secret is never printed here.

from voxera_supabase import supabase, SUPABASE_URL, _SUPABASE_KEY_SOURCE

_host = SUPABASE_URL.split("//")[-1].split(".")[0]
print(f"🔌 Connecting to Supabase project '{_host}' via {_SUPABASE_KEY_SOURCE} ...")
print("✅ Supabase client created.")
print()


# ============================================================
# STEP 1 — CREATE TEST PATIENT
# ============================================================

print("👤 Creating temporary test patient...")

patient_response = (
    supabase
    .table("patients")
    .insert({
        "full_name": "Voxera Test Patient",
        "phone": "9999999999",
        "preferred_language": "English",
    })
    .execute()
)

if not patient_response.data:
    raise RuntimeError("❌ Failed to create test patient.")

patient = patient_response.data[0]
patient_id = patient["id"]

print(f"✅ Test patient created")
print(f"   Patient ID: {patient_id}")
print()


# ============================================================
# STEP 2 — CREATE TEST CALL
# ============================================================

print("📞 Creating test call...")

call_response = (
    supabase
    .table("calls")
    .insert({
        "patient_id": patient_id,          # calls.patient_id is NOT NULL
        "facility_id": None,
        "call_type": "initial_assessment",
        "status": "in_progress",
        "language": "English",
    })
    .execute()
)

if not call_response.data:
    raise RuntimeError("❌ Failed to create test call.")

call = call_response.data[0]
call_id = call["id"]

print("✅ Test call created")
print(f"   Call ID: {call_id}")
print()


# ============================================================
# STEP 3 — ADD PATIENT TURN
# ============================================================

print("🗣️ Adding patient conversation turn...")

patient_turn_response = (
    supabase
    .table("conversation_turns")
    .insert({
        "call_id": call_id,
        "speaker": "patient",
        "message": "I have a fever and I have been feeling nauseous.",
        "sequence_number": 1,
    })
    .execute()
)

if not patient_turn_response.data:
    raise RuntimeError("❌ Failed to insert patient turn.")

print("✅ Patient turn saved.")
print()


# ============================================================
# STEP 4 — ADD AI TURN
# ============================================================

print("🤖 Adding AI conversation turn...")

ai_turn_response = (
    supabase
    .table("conversation_turns")
    .insert({
        "call_id": call_id,
        "speaker": "ai",
        "message": "How high is your fever, and when did it start?",
        "sequence_number": 2,
    })
    .execute()
)

if not ai_turn_response.data:
    raise RuntimeError("❌ Failed to insert AI turn.")

print("✅ AI turn saved.")
print()


# ============================================================
# STEP 5 — READ CONVERSATION BACK
# ============================================================

print("📖 Reading conversation back from Supabase...")

conversation_response = (
    supabase
    .table("conversation_turns")
    .select("speaker, message, sequence_number, created_at")
    .eq("call_id", call_id)
    .order("sequence_number")
    .execute()
)

print()

if not conversation_response.data:
    raise RuntimeError("❌ Could not read conversation.")

print("✅ Conversation retrieved:")
print()

for turn in conversation_response.data:
    print(
        f"[{turn['sequence_number']}] "
        f"{turn['speaker'].upper()}: "
        f"{turn['message']}"
    )

print()


# ============================================================
# STEP 6 — CLEAN UP TEST DATA
# ============================================================

print("🧹 Cleaning up test data...")

# Delete conversation turns first
supabase \
    .table("conversation_turns") \
    .delete() \
    .eq("call_id", call_id) \
    .execute()

# Delete call
supabase \
    .table("calls") \
    .delete() \
    .eq("id", call_id) \
    .execute()

# Delete patient
supabase \
    .table("patients") \
    .delete() \
    .eq("id", patient_id) \
    .execute()

print("✅ Test data deleted.")
print()


# ============================================================
# FINAL RESULT
# ============================================================

print("============================================================")
print("🎉 SUPABASE FULL CONVERSATION TEST PASSED")
print("============================================================")
print()
print("Patient → Call → Conversation Turns → Read → Delete")
print("Everything is working.")