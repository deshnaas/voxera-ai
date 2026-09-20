"""Emergency detection for Hindi / Marathi speech — WITHOUT touching or replacing the frozen detector.

voxera_emergency.py only understands English. So Hindi/Marathi speech reaches it as English, two ways (both are tried,
either can trigger it; false alarms are the acceptable direction):

  1. this lexicon: distinctive emergency phrases in Devanagari and in romanised Hindi/Marathi are mapped to the plain
     English sentence the frozen detector's own rules recognise ("सीने में दर्द है" -> "I have chest pain");
  2. Whisper's English translation of the same audio (see stt.py).

The English sentences are then given to the ORIGINAL ``check_emergency``; the decision, the category and the
department all still come from it. Only the spoken reply is localised afterwards (catalog.emergency_text).

Negation ("सीने में दर्द नहीं है" = "no chest pain") is respected so a denial does not escalate.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

_NEG_AFTER = re.compile(r"(?:नहीं|नही|नाही|नाहीत|न\s|नको|\bnahi\b|\bnahin\b|\bnot\b|\bno\b)", re.I)
_NEG_BEFORE = re.compile(r"(?:बिना|शिवाय|without|कोई\s+नहीं|काहीही\s+नाही)\s*$", re.I)


def _rx(p: str):
    return re.compile(p, re.I)


CHEST = r"(?:सीने|सीना|छाती|छातीत|छातीमध्ये|chest|chhati|seene|seena|chhatit)"
PAIN = r"(?:दर्द|दुख\w*|दुःख|वेदना|जकड़न|जकडन|जकड\w*|दबाव|दाब|भारीपन|भारी|dard|dukh\w*|vedana|jakad\w*|dabav|bhari)"
BREATH = r"(?:साँस|सांस|श्वास|दम|saans|shwas|dam)"

# (pattern, canonical English sentence). The English sentences are chosen to match voxera_emergency's rules.
LEXICON = [
    # --- chest ------------------------------------------------------------------------------------
    (_rx(rf"{CHEST}[^।.\n]{{0,25}}{PAIN}"), "I have really bad chest pain."),
    (_rx(rf"{PAIN}[^।.\n]{{0,20}}{CHEST}"), "I have really bad chest pain."),
    (_rx(rf"{CHEST}[^।.\n]{{0,15}}(?:जड़|जड|भारी|दब|कस)"), "My chest feels really tight and heavy."),
    # --- breathing --------------------------------------------------------------------------------
    (_rx(rf"{BREATH}[^।.\n]{{0,25}}(?:नहीं\s*(?:ले|आ|आ\s*रही)|नाही\s*(?:घेता|येत)|(?:घेता|घेऊ)\s*(?:येत\s*|शकत\s*)?नाही|रुक|अटक|घुट|nahi\s*(?:le|aa|aat|ho)|band)"), "I can't breathe."),
    (_rx(rf"{BREATH}[^।.\n]{{0,25}}(?:तकलीफ|तकलीफ़|दिक्कत|कठीण|कठिन|त्रास|फूल|फुल|taklif|dikkat|tras)"), "I'm having a lot of trouble breathing."),
    (_rx(r"(?:धाप\s*लाग|दम\s*घुट|दम\s*फूल|दम\s*लाग|dam\s*ghut)"), "I'm having a lot of trouble breathing."),
    (_rx(r"(?:गला|घसा)[^।.\n]{0,15}(?:बंद|घुट|अडक)"), "I feel like I'm choking."),
    # --- stroke -----------------------------------------------------------------------------------
    (_rx(r"(?:चेहरा|मुँह|मुंह|तोंड|चेहऱ्याची)[^।.\n]{0,15}(?:टेढ़ा|टेढा|लटक|झुक|वाकड|वाकडा|वाकडे|एक\s*बाजू)"), "My face is drooping and one side went weak."),
    (_rx(r"(?:बोल\w*[^।.\n]{0,15}(?:दिक्कत|नहीं\s*पा|अटक|लड़खड़|लडखड|अडखळ|येत\s*नाही)|ज़बान[^।.\n]{0,10}(?:लड़खड़|लडखड|अटक)|जुबान[^।.\n]{0,10}(?:लड़खड़|अटक)|आवाज़[^।.\n]{0,10}लड़खड़|बोलता\s*येत\s*नाही|जीभ[^।.\n]{0,8}(?:जड़|जड))"),
     "My speech suddenly became slurred."),
    (_rx(r"(?:अचानक|एकदम|अचानकपणे)[^।.\n]{0,25}(?:हाथ|पैर|हात|पाय|शरीर|अंग)[^।.\n]{0,20}(?:सुन्न|कमज़ोर|कमजोर|लकवा|अशक्त|लुळा|बधिर|बधीर)"),
     "Suddenly my left arm is numb and I can't speak."),
    (_rx(r"(?:हाथ|पैर|हात|पाय)[^।.\n]{0,15}(?:अचानक|एकदम)[^।.\n]{0,15}(?:सुन्न|कमज़ोर|कमजोर|अशक्त|लुळा)"),
     "Suddenly my left arm is numb and I can't speak."),
    # --- bleeding -----------------------------------------------------------------------------------
    (_rx(r"(?:खून|रक्त)[^।.\n]{0,20}(?:बह\s*रहा|बह\s*रही|बंद\s*नहीं|नहीं\s*रुक|थांबत\s*नाही|वाहत|खूप|बहुत)"), "I'm bleeding heavily and it won't stop."),
    (_rx(r"रक्तस्राव|खूनखराबा|khoon\s*beh|khoon\s*nahi\s*ruk"), "I'm bleeding heavily and it won't stop."),
    (_rx(r"(?:खून|रक्त)[^।.\n]{0,12}(?:उल्टी|खाँसी|खांसी|खोकल्या|उलटी)|(?:उल्टी|खाँसी|खांसी|खोकल्या|उलटी)[^।.\n]{0,12}(?:खून|रक्त)"), "I coughed up blood."),
    # --- unconscious / seizure ---------------------------------------------------------------------
    (_rx(r"(?:बेहोश|बेशुद्ध|होश\s*में\s*नहीं|होश\s*नहीं|शुद्धीवर\s*नाही|शुद्ध\s*हरप|उठ\s*नहीं\s*रहा|उठत\s*नाही|behosh|beshudh)"), "My father just passed out and won't wake up."),
    (_rx(r"(?:दौरा\s*पड़|दौरा\s*पड|मिर्गी|झटके|आकड़ी|आकडी|फिट\s*आ|फिट\s*येत|फिट\s*आली|तडका|mirgi|daura\s*pad)"), "She's having a seizure right now."),
    # --- self harm ---------------------------------------------------------------------------------
    (_rx(r"(?:आत्महत्या|जान\s*दे\s*द|जान\s*देना|मर\s*जाना\s*चाहत|मरना\s*चाहत|जीना\s*नहीं|जीव\s*द्याय|मरावेसे\s*वाट|मला\s*मराय|आयुष्य\s*संपव|aatmahatya|marna\s*chahta)"),
     "I want to kill myself."),
]


_PLAIN_DENIAL = re.compile(r"\s*(?:है|हैं|हूँ|हूं|आहे|आहेत|आहात|थी|था|होत|वाटत|जाणवत|लग|hai|he|[।.,!?]|$)", re.I)


def _negated(text: str, m: re.Match) -> bool:
    """True only for a plain denial ("सीने में दर्द नहीं है"). A negator that is part of the symptom
    ("खून रुक नहीं रहा" = bleeding that won't stop, "उठ नहीं रहे" = won't wake up) must NOT cancel it."""
    before = text[max(0, m.start() - 14): m.start()]
    if _NEG_BEFORE.search(before):
        return True
    if re.search(r"(?:नहीं|नाही)", m.group(0)):
        return False
    after = text[m.end(): m.end() + 24]
    neg = _NEG_AFTER.search(after)
    return bool(neg) and bool(_PLAIN_DENIAL.match(after[neg.end():]))


def to_english(text: str) -> list:
    """Canonical English sentences for the emergency phrases found in Hindi/Marathi (or romanised) `text`."""
    out: list = []
    t = text or ""
    for rx, sentence in LEXICON:
        for m in rx.finditer(t):
            if _negated(t, m):
                continue
            if sentence not in out:
                out.append(sentence)
            break
    return out


def emergency_probe(check_emergency: Callable, native: str, english: Optional[str], *, context: str = "",
                    last_assistant: str = "") -> Optional[object]:
    """Run the ORIGINAL detector over every English rendering of the turn. First hit wins."""
    seen: list = []
    for cand in [english, *to_english(native), native]:
        cand = (cand or "").strip()
        if not cand or cand in seen:
            continue
        seen.append(cand)
        r = check_emergency(cand, context=context, last_assistant=last_assistant)
        if r:
            return r
    return None


# ------------------------------------------------------------------------------------------------
# spoken patient IDs in Hindi / Marathi:  "वी एक्स शून्य चार दो एक"  ->  "vx 0 4 2 1"
# ------------------------------------------------------------------------------------------------

_NUM = {
    "शून्य": "0", "स्वून्य": "0", "सुन्य": "0", "शुन्य": "0", "सिफर": "0", "ज़ीरो": "0", "जीरो": "0", "झिरो": "0", "झीरो": "0", "ओ": "0",
    "एक": "1", "वन": "1", "दो": "2", "दोन": "2", "टू": "2", "तीन": "3", "थ्री": "3", "चार": "4", "फोर": "4",
    "पाँच": "5", "पांच": "5", "पाच": "5", "फाइव": "5", "छह": "6", "छः": "6", "छे": "6", "सहा": "6", "सिक्स": "6",
    "सात": "7", "सेवन": "7", "आठ": "8", "एट": "8", "नौ": "9", "नऊ": "9", "नाइन": "9",
}
_MULT = {"डबल": "double", "ट्रिपल": "triple", "दुहेरी": "double"}
_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_VX = re.compile(r"(?:वी|व्ही|वि|भी)\s*(?:एक्स|ऍक्स|ऐक्स|एक्ष|इक्स)|वीएक्स|व्हीएक्स|v\s*x", re.I)


def normalize_spoken_id_text(text: str) -> str:
    """Rewrite Hindi/Marathi number words + the 'VX' prefix into forms normalize_patient_id already understands."""
    t = (text or "").translate(_DEV_DIGITS)
    t = _VX.sub(" vx ", t)
    words = []
    for tok in re.findall(r"[ऀ-ॣ०-ॿ]+|[A-Za-z]+|\d+", t):
        if tok in _MULT:
            words.append(_MULT[tok])
        elif tok in _NUM:
            words.append(_NUM[tok])
        else:
            words.append(tok)
    return " ".join(words)
