# ============================================================
# VOXERA REASONING ENGINE
# ============================================================
#
# Fast conversational decision layer.
#
# IMPORTANT:
# This module does NOT call the LLM.
#
# The previous version used a separate LLM inference here.
# That added unnecessary latency before Voxera could respond.
#
# The conversational Brain is responsible for natural language.
# This module only decides WHAT Voxera should do next.
# ============================================================


def reason(
    message,
    understanding,
    safety,
    conversation_context=""
):

    # --------------------------------------------------------
    # SAFETY ALWAYS WINS
    # --------------------------------------------------------

    risk = safety.get(
        "risk_level",
        "URGENT"
    )

    if risk == "EMERGENCY":

        return {
            "known_information": _known_information(
                understanding
            ),

            "missing_information": [],

            "next_action": "ESCALATE",

            "reason": (
                "The safety layer identified a possible "
                "medical emergency."
            ),

            "follow_up_question": "",

            "possible_explanations": []
        }

    # --------------------------------------------------------
    # URGENT
    # --------------------------------------------------------

    if risk == "URGENT":

        return {
            "known_information": _known_information(
                understanding
            ),

            "missing_information": _missing_information(
                understanding
            ),

            "next_action": "SEEK_MEDICAL_CARE",

            "reason": (
                "The situation may require prompt "
                "medical evaluation."
            ),

            "follow_up_question": "",

            "possible_explanations": []
        }

    # --------------------------------------------------------
    # NORMAL CONVERSATION
    # --------------------------------------------------------

    topic = understanding.get(
        "topic",
        "GENERAL"
    )

    # If this is a symptom conversation, determine whether
    # we have enough information for another useful question.

    if topic == "SYMPTOM":

        missing = _missing_information(
            understanding
        )

        # Ask about the most useful missing piece.
        if missing:

            question = _choose_followup(
                understanding
            )

            return {
                "known_information": _known_information(
                    understanding
                ),

                "missing_information": missing,

                "next_action": "ASK_FOLLOWUP",

                "reason": (
                    "More relevant information is needed "
                    "to continue the symptom assessment."
                ),

                "follow_up_question": question,

                "possible_explanations": []
            }

    # --------------------------------------------------------
    # EVERYTHING ELSE
    # --------------------------------------------------------

    return {
        "known_information": _known_information(
            understanding
        ),

        "missing_information": [],

        "next_action": "CONTINUE",

        "reason": (
            "The conversation can continue naturally."
        ),

        "follow_up_question": "",

        "possible_explanations": []
    }


# ============================================================
# KNOWN INFORMATION
# ============================================================

def _known_information(understanding):

    known = []

    symptoms = understanding.get(
        "symptoms",
        []
    )

    if symptoms:

        known.append(
            "Symptoms: " +
            ", ".join(str(x) for x in symptoms)
        )

    duration = understanding.get(
        "duration"
    )

    if duration:

        known.append(
            "Duration: " +
            str(duration)
        )

    onset = understanding.get(
        "onset"
    )

    if onset:

        known.append(
            "Onset: " +
            str(onset)
        )

    severity = understanding.get(
        "severity"
    )

    if severity:

        known.append(
            "Severity: " +
            str(severity)
        )

    associated = understanding.get(
        "associated_symptoms",
        []
    )

    if associated:

        known.append(
            "Associated symptoms: " +
            ", ".join(
                str(x)
                for x in associated
            )
        )

    triggers = understanding.get(
        "triggers",
        []
    )

    if triggers:

        known.append(
            "Triggers: " +
            ", ".join(
                str(x)
                for x in triggers
            )
        )

    feeling = understanding.get(
        "patient_feeling"
    )

    if feeling:

        known.append(
            "Patient feeling: " +
            str(feeling)
        )

    return known


# ============================================================
# MISSING INFORMATION
# ============================================================

def _missing_information(understanding):

    missing = []

    symptoms = understanding.get(
        "symptoms",
        []
    )

    duration = understanding.get(
        "duration"
    )

    onset = understanding.get(
        "onset"
    )

    severity = understanding.get(
        "severity"
    )

    # No symptom identified.
    if not symptoms:

        missing.append(
            "What symptoms are you experiencing?"
        )

        return missing

    # Duration is often useful early in an assessment.
    if not duration:

        missing.append(
            "How long the symptoms have been present"
        )

    # Onset can help establish whether something happened
    # suddenly or gradually.
    if not onset:

        missing.append(
            "Whether the symptoms started suddenly or gradually"
        )

    # Severity is useful once the basic symptom is established.
    if not severity:

        missing.append(
            "How severe the symptoms are"
        )

    # Don't ask for everything forever.
    #
    # Only return a small number of useful missing fields.
    return missing[:2]


# ============================================================
# FOLLOW-UP QUESTION
# ============================================================

def _choose_followup(understanding):

    symptoms = understanding.get(
        "symptoms",
        []
    )

    duration = understanding.get(
        "duration"
    )

    onset = understanding.get(
        "onset"
    )

    severity = understanding.get(
        "severity"
    )

    triggers = understanding.get(
        "triggers",
        []
    )

    associated = understanding.get(
        "associated_symptoms",
        []
    )

    symptom_text = ", ".join(
        str(x)
        for x in symptoms
    )

    # --------------------------------------------------------
    # If we know the symptom but not duration.
    # --------------------------------------------------------

    if not duration:

        return (
            f"How long have you been experiencing "
            f"{symptom_text}?"
        )

    # --------------------------------------------------------
    # If duration is known but onset isn't.
    # --------------------------------------------------------

    if not onset:

        return (
            "Did it start suddenly, or did it come on gradually?"
        )

    # --------------------------------------------------------
    # If severity isn't known.
    # --------------------------------------------------------

    if not severity:

        return (
            "How severe is it right now?"
        )

    # --------------------------------------------------------
    # If there is a trigger but no associated symptoms.
    # --------------------------------------------------------

    if triggers and not associated:

        return (
            "Have you noticed any other symptoms along with this?"
        )

    # --------------------------------------------------------
    # Generic useful continuation.
    # --------------------------------------------------------

    return (
        "Have you noticed any other symptoms "
        "or anything that makes it better or worse?"
    )