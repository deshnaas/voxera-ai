import re


# ============================================================
# VOXERA FAST SAFETY GATE
# ============================================================
#
# This module intentionally does NOT call the LLM.
#
# Safety must be extremely fast because it sits on the
# conversational critical path.
#
# Obvious emergency signals are detected immediately.
#
# A later version can add a secondary LLM/ML safety classifier
# for ambiguous cases without blocking normal conversation.
# ============================================================


EMERGENCY_PATTERNS = [

    # --------------------------------------------------------
    # BREATHING
    # --------------------------------------------------------

    r"\bcan't breathe\b",
    r"\bcannot breathe\b",
    r"\bcan not breathe\b",
    r"\btrouble breathing\b",
    r"\bdifficulty breathing\b",
    r"\bsevere breathing\b",
    r"\bnot able to breathe\b",
    r"\bcan't catch my breath\b",
    r"\bcannot catch my breath\b",
    r"\bchoking\b",

    # --------------------------------------------------------
    # CONSCIOUSNESS
    # --------------------------------------------------------

    r"\bunconscious\b",
    r"\bpassed out\b",
    r"\bpass out\b",
    r"\bfainted\b",
    r"\bfainting\b",
    r"\blost consciousness\b",
    r"\bnot responding\b",

    # --------------------------------------------------------
    # SEIZURES
    # --------------------------------------------------------

    r"\bseizure\b",
    r"\bseizing\b",
    r"\bconvulsion\b",
    r"\bconvulsions\b",

    # --------------------------------------------------------
    # CHEST / HEART
    # --------------------------------------------------------

    r"\bsevere chest pain\b",
    r"\bcrushing chest pain\b",
    r"\bpressure in my chest\b",
    r"\bheavy pressure in my chest\b",

    # --------------------------------------------------------
    # STROKE / NEUROLOGICAL EMERGENCY
    # --------------------------------------------------------

    r"\bsuddenly can't speak\b",
    r"\bsuddenly cannot speak\b",
    r"\bcan't speak suddenly\b",
    r"\bcannot speak suddenly\b",
    r"\bsudden paralysis\b",
    r"\bsuddenly paraly[sz]ed\b",
    r"\bface drooping\b",
    r"\bsudden weakness on one side\b",
    r"\bweakness on one side\b",

    # --------------------------------------------------------
    # SEVERE BLEEDING
    # --------------------------------------------------------

    r"\bsevere bleeding\b",
    r"\buncontrolled bleeding\b",
    r"\bbleeding won't stop\b",
    r"\bblood won't stop\b",

    # --------------------------------------------------------
    # SEVERE ALLERGIC REACTION
    # --------------------------------------------------------

    r"\banaphylaxis\b",
    r"\banaphylactic\b",
    r"\bthroat is closing\b",
    r"\bthroat closing\b",
    r"\bswelling.*throat\b",

    # --------------------------------------------------------
    # POISONING / OVERDOSE
    # --------------------------------------------------------

    r"\boverdose\b",
    r"\bdrug overdose\b",
    r"\bpoisoned\b",
    r"\bpoisoning\b",
    r"\bswallowed poison\b",

    # --------------------------------------------------------
    # SUICIDE / IMMEDIATE SELF HARM
    # --------------------------------------------------------

    r"\bkill myself\b",
    r"\bwant to die\b",
    r"\bgoing to kill myself\b",
    r"\bsuicide\b",
    r"\bsuicidal\b",
]


COMPILED_EMERGENCY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in EMERGENCY_PATTERNS
]


def _contains_emergency_signal(message):
    """
    Return the first emergency pattern that matches,
    or None if no obvious emergency signal is present.
    """

    if not message:
        return None

    normalized = " ".join(
        message.strip().split()
    )

    for pattern in COMPILED_EMERGENCY_PATTERNS:

        if pattern.search(normalized):
            return pattern.pattern

    return None


def analyze_safety(
    message,
    conversation_context=""
):
    """
    Ultra-fast first-line safety assessment.

    IMPORTANT:
    This function intentionally does not call the LLM.

    It is designed to sit on the real-time conversational
    path where latency matters.
    """

    matched_pattern = _contains_emergency_signal(
        message
    )

    # ========================================================
    # EMERGENCY
    # ========================================================

    if matched_pattern:

        return {
            "risk_level": "EMERGENCY",

            "reason": (
                "The message contains a possible "
                "life-threatening emergency signal."
            ),

            "confidence": 0.98,

            "recommended_action": "ESCALATE"
        }

    # ========================================================
    # NORMAL
    # ========================================================

    return {
        "risk_level": "NORMAL",

        "reason": (
            "No obvious immediate emergency "
            "signal was detected."
        ),

        "confidence": 0.80,

        "recommended_action": "CONTINUE"
    }