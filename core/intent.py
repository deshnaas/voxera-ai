def detect_intent(message):

    text = message.lower().strip()

    # Patient reports symptoms
    symptom_words = [
        "pain",
        "hurt",
        "hurts",
        "dizzy",
        "dizziness",
        "headache",
        "fever",
        "cough",
        "vomit",
        "vomiting",
        "nausea",
        "weak",
        "weakness",
        "breathing",
        "breath",
        "bleeding",
        "swelling",
        "rash",
        "sick",
        "feel",
        "feeling"
    ]

    if any(word in text for word in symptom_words):
        return "SYMPTOM"

    # Reports
    report_words = [
        "report",
        "blood test",
        "blood report",
        "test result",
        "lab result",
        "scan",
        "xray",
        "x-ray",
        "mri",
        "ct scan"
    ]

    if any(word in text for word in report_words):
        return "REPORT"

    # Prescriptions / medicines
    prescription_words = [
        "prescription",
        "prescribed",
        "medicine",
        "medication",
        "medicines",
        "tablets",
        "tablet",
        "what medicine"
    ]

    if any(word in text for word in prescription_words):
        return "PRESCRIPTION"

    # General medical questions
    medical_words = [
        "what is",
        "what causes",
        "why does",
        "is it normal",
        "should i",
        "can i",
        "health",
        "disease",
        "symptom"
    ]

    if any(word in text for word in medical_words):
        return "MEDICAL_QUESTION"

    return "GENERAL_CONVERSATION"


def select_workflow(intent):

    workflows = {
        "SYMPTOM": "symptom_assessment",
        "REPORT": "report_retrieval",
        "PRESCRIPTION": "prescription_retrieval",
        "MEDICAL_QUESTION": "medical_information",
        "GENERAL_CONVERSATION": "general_conversation",
    }

    return workflows.get(
        intent,
        "general_conversation"
    )