"""Build the English "understanding" of a Hindi / Marathi turn for the English-only logic.

Whisper's English translation of Hindi is usable; of Marathi it is often wrong (measured: "मला कालपासून सौम्य ताप आणि
डोकेदुखी आहे" -> "Malakalpa Sun Samyatap is in the heart of the village"). Triage, care topics, yes/no answers and
facts must not depend on that. So the translation is COMBINED with a small lexicon of common symptom and answer
words in both languages: whatever the lexicon recognises is stated in plain English, appended to the translation.

Only words the lexicon knows are added; it never invents anything, and it never decides an emergency (that is
safety.py -> the frozen detector).
"""

from __future__ import annotations

import re

# (native pattern, plain English statement). Devanagari + romanised.
SYMPTOMS = [
    (r"बुखार|ताप\b|तापमान|जर\b|फीवर|फिवर|टेम्परेचर|bukhar|taap|fever", "I have a fever."),
    (r"हल्का\s*बुखार|सौम्य\s*ताप|थोडा\s*ताप|हलका\s*भुखार|halka\s*bukhar", "I have a mild fever."),
    (r"खांसी|खाँसी|खोकला|खोकल्या|कफ\b|कफ़\b|khansi|khokla", "I have a cough."),
    (r"सिरदर्द|हेडेक|सिर\s*(?:में\s*)?दर्द|डोकेदुखी|डोके\s*दुख|डोक्यात\s*दुख|sir\s*dard|dokedukhi|डोके\s*दुखि", "I have a headache."),
    (r"गले\s*(?:में\s*)?(?:खराश|दर्द|खिचखिच)|घसा\s*(?:दुख|खवखव|खरा)", "I have a sore throat."),
    (r"नाक\s*बंद|बंद\s*नाक|नाक\s*चोंद|नाक\s*वाह|सर्दी|जुकाम|सर्दी-खोकला|नाक\s*गळत", "I have a cold and a blocked nose."),
    (r"पेट\s*(?:में\s*)?दर्द|पोट\s*दुख|पोटात\s*दुख|पेट\s*दुख|pet\s*dard", "My stomach is hurting."),
    (r"एसिडिटी|अम्लता|ॲसिडिटी|अ‍ॅसिडिटी|गैस|गॅस|अपचन|बदहज़मी|बदहजमी|छाती\s*(?:में|मध्ये)?\s*जळजळ|सीने\s*में\s*जलन|acidity", "I have indigestion and acidity."),
    (r"जल\s*गया|जळाले|भाजल|जल\s*गई|आग\s*से\s*जल", "I burned my hand on a pan."),
    (r"कट\s*गया|कापले|कापल|कट\s*लगा|चीरा|खरचट|खरोंच|छिल", "I have a small cut on my finger."),
    (r"मोच|मुरगळ|मुरगळल", "I twisted my ankle, a minor sprain."),
    (r"उल्टी|उलटी|उलट्या|उबकाई|मळमळ", "I have been vomiting."),
    (r"दस्त|जुलाब|पतली\s*टट्टी", "I have diarrhea."),
    (r"चक्कर|घुमरी|भोवळ", "I feel dizzy."),
    (r"कमज़ोरी|कमजोरी|अशक्तपणा|थकान|थकवा", "I feel weak and tired."),
    (r"पानी\s*की\s*कमी|निर्जलीकरण|डिहाइड्रेशन", "I think I'm a bit dehydrated."),
    (r"पसीना|घाम", "I am sweating."),
]
YES = re.compile(r"^\W*(?:हाँ|हां|हा|जी\s*हाँ|जी\s*हां|जी|होय|हो|बिल्कुल|बरोबर|सही|yes|haan|ho|hoy)\W*$", re.I)
NO = re.compile(r"^\W*(?:नहीं|नही|नाही|नको|जी\s*नहीं|नाहीं|बिल्कुल\s*नहीं|no|nahi|nahin)\W*$", re.I)
NEG_SYMPTOM = re.compile(r"(?:नहीं|नही|नाही|नको)\s*(?:है|हैं|आहे|आहेत)?\s*$")


# Whisper's English translation is usually right about the symptom word even when the sentence around it is odd
# ("What can I do for my fever?"); the English-only care logic wants "I have a fever." so state it plainly.
_EN_TERMS = [
    (r"\b(?:fever|feverish)\b", "I have a fever."),
    (r"\bheadache\b", "I have a headache."),
    (r"\bcough(?:ing)?\b", "I have a cough."),
    (r"\bsore throat\b", "I have a sore throat."),
    (r"\b(?:runny nose|blocked nose|stuffy nose)\b", "I have a cold and a blocked nose."),
    (r"\b(?:acidity|indigestion)\b", "I have indigestion and acidity."),
    (r"\bvomit(?:ing)?\b", "I have been vomiting."),
    (r"\bdiarrh?oea\b|\bdiarrhea\b", "I have diarrhea."),
    (r"\bdizz(?:y|iness)\b", "I feel dizzy."),
]
_EN_NEG = re.compile(r"(?:\bno|\bnot|\bwithout|\bdon'?t have(?: a)?|\bhaven'?t(?: had)?(?: a)?)\s+(?:\w+\s+){0,1}$", re.I)


def _stated_en(translation: str) -> list:
    out: list = []
    for rx, sentence in _EN_TERMS:
        m = re.search(rx, translation or "", re.I)
        if m and not _EN_NEG.search(translation[:m.start()]) and sentence not in out:
            out.append(sentence)
    return out


def _stated(native: str) -> list:
    out: list = []
    for rx, sentence in SYMPTOMS:
        for m in re.finditer(rx, native, re.I):
            tail = native[m.end(): m.end() + 14]
            if NEG_SYMPTOM.search(tail.split("।")[0]) and re.match(r"\s*(?:नहीं|नही|नाही|नको)", tail):
                continue                                        # "बुखार नहीं है" (no fever) is not a fever
            if sentence not in out:
                out.append(sentence)
            break
    # a specific statement supersedes the generic one ("mild fever" over "fever")
    if "I have a mild fever." in out and "I have a fever." in out:
        out.remove("I have a fever.")
    return out


def understand(native: str, translation: str) -> str:
    """English text for the English-only logic = the translation + what the lexicon recognised in the native words."""
    native = (native or "").strip()
    translation = (translation or "").strip()
    if YES.match(native):
        return "Yes."
    if NO.match(native):
        return "No."
    extra = _stated(native)
    for s in _stated_en(translation):
        if s not in extra and not (s == "I have a fever." and "I have a mild fever." in extra):
            extra.append(s)
    return " ".join([translation, *extra]).strip() if extra else translation
