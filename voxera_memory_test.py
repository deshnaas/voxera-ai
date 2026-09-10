"""
VOXERA - Persistent Conversation Memory Test

Tests:
1. Create a temporary patient
2. Create a call
3. Save patient message
4. Save AI response
5. Save patient follow-up
6. Retrieve the conversation
7. Verify previous context is preserved
8. Clean up all test data
"""

import uuid

from voxera_supabase import (
    create_patient,
    create_call,
    save_turn,
    get_conversation,
    get_recent_conversation,
)


print("=" * 70)
print("🧠 VOXERA PERSISTENT CONVERSATION MEMORY TEST")
print("=" * 70)

patient = None
call = None

# Generate a unique phone number for every test run.
# This avoids the UNIQUE constraint on patients.phone.
unique_phone = "9" + str(uuid.uuid4().int)[-9:]

try:

    # ---------------------------------------------------------
    # 1. CREATE TEST PATIENT
    # ---------------------------------------------------------
    print("\n👤 Creating test patient...")

    patient = create_patient(
        full_name="Voxera Memory Test Patient",
        phone=unique_phone,
        preferred_language="English",
    )

    patient_id = patient["id"]

    print(f"✅ Patient created")
    print(f"   Patient ID: {patient_id}")
    print(f"   Test phone: {unique_phone}")

    # ---------------------------------------------------------
    # 2. CREATE TEST CALL
    # ---------------------------------------------------------
    print("\n📞 Creating test call...")

    call = create_call(
        patient_id=patient_id,
        language="English",
        call_type="initial_assessment",
    )

    call_id = call["id"]

    print(f"✅ Call created")
    print(f"   Call ID: {call_id}")

    # ---------------------------------------------------------
    # 3. SAVE FIRST PATIENT MESSAGE
    # ---------------------------------------------------------
    print("\n🗣️ Saving first patient message...")

    first_message = (
        "I have a fever of 104 degrees. "
        "I have been feeling very nauseous and "
        "I have been throwing up for two days."
    )

    save_turn(
        call_id=call_id,
        speaker="patient",
        message=first_message,
    )

    print("✅ Patient message saved")

    # ---------------------------------------------------------
    # 4. SAVE AI RESPONSE
    # ---------------------------------------------------------
    print("\n🤖 Saving AI response...")

    ai_message = (
        "A fever of 104°F with vomiting for two days "
        "needs prompt medical attention. "
        "Are you able to keep fluids down?"
    )

    save_turn(
        call_id=call_id,
        speaker="ai",
        message=ai_message,
    )

    print("✅ AI response saved")

    # ---------------------------------------------------------
    # 5. SAVE FOLLOW-UP MESSAGE
    # ---------------------------------------------------------
    print("\n🗣️ Saving patient follow-up...")

    followup_message = (
        "I am drinking plenty of water. "
        "What should I do about my blocked nose?"
    )

    save_turn(
        call_id=call_id,
        speaker="patient",
        message=followup_message,
    )

    print("✅ Follow-up message saved")

    # ---------------------------------------------------------
    # 6. FETCH FULL CONVERSATION
    # ---------------------------------------------------------
    print("\n📚 Fetching conversation from Supabase...")

    conversation = get_conversation(call_id)

    print(f"✅ Retrieved {len(conversation)} conversation turns")

    # ---------------------------------------------------------
    # 7. DISPLAY CONVERSATION
    # ---------------------------------------------------------
    print("\n" + "-" * 70)
    print("📖 STORED CONVERSATION")
    print("-" * 70)

    for turn in conversation:
        print(
            f"\n[{turn['sequence_number']}] "
            f"{turn['speaker'].upper()}:"
        )
        print(turn["message"])

    # ---------------------------------------------------------
    # 8. VERIFY CONVERSATION
    # ---------------------------------------------------------
    print("\n🔍 Verifying stored memory...")

    assert len(conversation) == 3, (
        f"Expected 3 turns, got {len(conversation)}"
    )

    # First patient message
    first_stored = conversation[0]["message"].lower()

    assert "104" in first_stored
    assert "nauseous" in first_stored
    assert (
        "vomiting" in first_stored
        or "throwing up" in first_stored
    )

    # AI response
    ai_stored = conversation[1]["message"].lower()

    assert "104" in ai_stored
    assert (
        "vomiting" in ai_stored
        or "fluids" in ai_stored
    )

    # Follow-up
    followup_stored = conversation[2]["message"].lower()

    assert "blocked nose" in followup_stored
    assert "water" in followup_stored

    print("✅ First patient symptoms preserved")
    print("✅ AI response preserved")
    print("✅ Follow-up symptoms preserved")

    # ---------------------------------------------------------
    # 9. TEST RECENT CONTEXT
    # ---------------------------------------------------------
    print("\n🧠 Testing recent conversation context...")

    recent = get_recent_conversation(
        call_id,
        limit=8,
    )

    print(f"✅ Retrieved {len(recent)} recent turns")

    combined_context = " ".join(
        turn["message"].lower()
        for turn in recent
    )

    assert "104" in combined_context
    assert "nauseous" in combined_context
    assert (
        "vomiting" in combined_context
        or "throwing up" in combined_context
    )
    assert "blocked nose" in combined_context

    print("✅ Previous fever context is available")
    print("✅ Previous nausea context is available")
    print("✅ Previous vomiting context is available")
    print("✅ New blocked-nose context is available")

    # ---------------------------------------------------------
    # 10. FINAL RESULT
    # ---------------------------------------------------------
    print("\n" + "=" * 70)
    print("🎉 SUPABASE PERSISTENT MEMORY TEST PASSED")
    print("=" * 70)

    print("\nVoxera can now:")
    print("  ✅ Store patient messages")
    print("  ✅ Store AI responses")
    print("  ✅ Retrieve previous conversation")
    print("  ✅ Preserve symptom context")
    print("  ✅ Retrieve recent conversation context")
    print("  ✅ Support follow-up questions")

finally:

    # ---------------------------------------------------------
    # CLEANUP
    # ---------------------------------------------------------
    if patient is not None and call is not None:

        print("\n🧹 Cleaning up test data...")

        try:
            from voxera_supabase import supabase

            # conversation_turns must be deleted first
            supabase.table("conversation_turns") \
                .delete() \
                .eq("call_id", call["id"]) \
                .execute()

            # Then delete the call
            supabase.table("calls") \
                .delete() \
                .eq("id", call["id"]) \
                .execute()

            # Finally delete the patient
            supabase.table("patients") \
                .delete() \
                .eq("id", patient["id"]) \
                .execute()

            print("✅ Cleanup complete.")

        except Exception as cleanup_error:
            print(f"⚠️ Cleanup warning: {cleanup_error}")