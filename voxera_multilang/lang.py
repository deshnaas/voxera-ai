"""Language detection for a phone call: English / Hindi / Marathi.

Evidence, strongest first:
  1. The WORDS of the transcript. Devanagari text is Hindi or Marathi; the two are told apart by function words
     that differ ("आहे / मला / तुम्ही" are Marathi, "है / मुझे / आप" are Hindi). This matters because Whisper's own
     language-ID is biased: on Marathi audio it reports ~85-97 % Hindi and only ~1-6 % Marathi (measured).
  2. Whisper's audio language probabilities, used to separate English from the Devanagari family.
  3. The patient's preferred_language from their record (a tiebreak only).

The tracker keeps the language stable over a call and switches only on clear, repeated evidence, so a single
English word (e.g. the patient ID) can't flip a Hindi conversation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

LANGS = ("en", "hi", "mr")
NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi"}

# Devanagari letters + matras + digits, without the danda punctuation (U+0964, U+0965)
_DEV_WORD = re.compile(r"[ऀ-ॣ०-ॿ]+")
_DEV_ANY = re.compile(r"[ऀ-ॿ]")
_LATIN_WORD = re.compile(r"[A-Za-z']+")

# words that are distinctive (NOT shared spelling) — Hindi
HI_WORDS = {
    "है", "हैं", "हूँ", "हूं", "था", "थी", "थे", "मैं", "मुझे", "मेरा", "मेरी", "मेरे", "हमें", "हम", "आप", "आपका", "आपकी", "आपके",
    "नहीं", "नही", "और", "क्या", "कब", "कहाँ", "कैसे", "क्यों", "रहा", "रही", "रहे", "बुखार", "खांसी", "खाँसी", "सिरदर्द",
    "पेट", "सीने", "साँस", "सांस", "तकलीफ", "तकलीफ़", "दर्द", "बहुत", "थोड़ा", "अभी", "कल", "आज", "दो", "दिन", "से", "को", "में", "पर",
    "है।", "हो", "होता", "होती", "लग", "लगा", "लगी", "चाहिए", "करें", "कीजिए", "बताइए", "कृपया", "जी", "हाँ", "हां",
    "पाँच", "पांच", "छह", "नौ",          # number words (Marathi: पाच, सहा, नऊ, दोन)
}
# Marathi
MR_WORDS = {
    "आहे", "आहेत", "आहात", "आहोत", "होते", "होता", "होती", "मी", "मला", "माझा", "माझी", "माझे", "माझ्या", "आम्ही", "आपण", "तुम्ही", "तुमचा",
    "तुमची", "तुमचे", "तुमच्या", "नाही", "नाहीत", "आणि", "काय", "केव्हा", "कधी", "कुठे", "कसे", "का", "ताप", "खोकला", "डोकेदुखी", "पोट",
    "छातीत", "छाती", "श्वास", "त्रास", "वेदना", "खूप", "थोडे", "आत्ता", "उद्या", "आज", "दिवस", "पासून", "साठी", "वर", "मध्ये", "ला",
    "झाले", "झाली", "झाला", "वाटत", "वाटते", "दुखत", "दुखते", "येत", "जात", "करा", "सांगा", "कृपया", "हो", "नको", "पाहिजे", "होत",
    "दोन", "पाच", "सहा", "नऊ",           # number words
}
# shared / ambiguous in both — never counted
_SHARED = {"दिन", "आज", "हो", "कृपया", "दर्द", "श्वास", "पेट"}
HI_WORDS = {w for w in HI_WORDS if w not in _SHARED or w in {"है", "हैं"}}
MR_WORDS = {w for w in MR_WORDS if w not in _SHARED}

# romanised (Latin-script) Hindi / Marathi that Whisper sometimes emits for accented or code-mixed speech
HI_ROMAN = {"mujhe", "mera", "meri", "mere", "hai", "hain", "nahi", "nahin", "dard", "bukhar", "khansi", "saans", "seene", "kya", "aap",
            "bahut", "thoda", "abhi", "kal", "tabiyat", "pet", "sir", "haan", "kripya", "rha", "rahi", "raha"}
MR_ROMAN = {"mala", "mazya", "majha", "majhi", "aahe", "aahet", "nahi", "dukhat", "taap", "khokla", "pot", "chhatit", "shwas", "tras", "khup",
            "kay", "kasa", "tumhi", "kripaya", "sanga", "zale", "vatat"}


def _tokens(text: str) -> list:
    return _DEV_WORD.findall(text or "")


def script_share(text: str) -> float:
    """Fraction of letters that are Devanagari (0 = all Latin, 1 = all Devanagari)."""
    dev = len(_DEV_ANY.findall(text or ""))
    lat = len(re.findall(r"[A-Za-z]", text or ""))
    return dev / (dev + lat) if (dev + lat) else 0.0


@dataclass
class TextVerdict:
    lang: Optional[str]          # en | hi | mr | None (not enough evidence)
    confidence: float
    hi_score: int = 0
    mr_score: int = 0
    dev_share: float = 0.0


def detect_from_text(text: str, prior: Optional[str] = None, preferred: Optional[str] = None) -> TextVerdict:
    t = (text or "").strip()
    if not t:
        return TextVerdict(None, 0.0)
    share = script_share(t)
    dev = _tokens(t)
    if share >= 0.4 and dev:
        hi = sum(1 for w in dev if w in HI_WORDS)
        mr = sum(1 for w in dev if w in MR_WORDS)
        # Marathi verb endings that Hindi does not use ("...त आहे", "...ले", "...ला" attached) add a little weight
        mr += sum(1 for w in dev if re.search(r"(?:ल्या|ल्यात|मध्ये|साठी|पासून)$", w))
        if mr > hi:
            return TextVerdict("mr", min(0.95, 0.6 + 0.1 * (mr - hi)), hi, mr, share)
        if hi > mr:
            return TextVerdict("hi", min(0.95, 0.6 + 0.1 * (hi - mr)), hi, mr, share)
        # tie: no distinguishing words (short utterances). Use the call so far, then the record, then Hindi.
        pick = prior if prior in ("hi", "mr") else (preferred if preferred in ("hi", "mr") else "hi")
        return TextVerdict(pick, 0.45, hi, mr, share)
    # Latin script: English, or romanised Hindi / Marathi
    words = [w.lower() for w in _LATIN_WORD.findall(t)]
    hi = sum(1 for w in words if w in HI_ROMAN)
    mr = sum(1 for w in words if w in MR_ROMAN)
    if max(hi, mr) >= 2 and max(hi, mr) >= 0.25 * max(1, len(words)):
        return TextVerdict("mr" if mr > hi else "hi", 0.6, hi, mr, share)
    if len(words) >= 2:
        return TextVerdict("en", 0.8 if share < 0.1 else 0.5, hi, mr, share)
    return TextVerdict(None, 0.0, hi, mr, share)


def family_from_lid(probs: dict) -> tuple:
    """Whisper language probabilities -> ('en'|'dev', confidence). Hindi/Marathi/Urdu/Nepali all count as Devanagari-family
    because the model cannot reliably separate them; the words decide."""
    en = float(probs.get("en", 0.0))
    dev = sum(float(probs.get(k, 0.0)) for k in ("hi", "mr", "ur", "ne", "sa", "bn", "gu", "pa"))
    tot = en + dev
    if tot <= 0:
        return None, 0.0
    return ("en", en / tot) if en >= dev else ("dev", dev / tot)


@dataclass
class Observation:
    lang: str
    confidence: float
    words: int = 0


@dataclass
class LanguageTracker:
    """Stable per-call language with hysteresis."""
    preferred: Optional[str] = None          # patient's record preference (tiebreak only)
    lang: Optional[str] = None
    history: list = field(default_factory=list)
    switches: int = 0
    tentative: bool = False

    @property
    def decided(self) -> bool:
        return self.lang is not None

    def current(self, default: str = "en") -> str:
        return self.lang or default

    def observe(self, lang: Optional[str], confidence: float, words: int = 0) -> str:
        """Feed one turn's verdict; returns the language to reply in."""
        if lang not in LANGS or words < 1:
            return self.current()
        self.history.append(Observation(lang, confidence, words))
        if self.lang is None:
            if confidence >= 0.5 or words >= 3:
                self.lang = lang
                self.tentative = confidence < 0.5          # decided on weak evidence (e.g. a spoken ID of digits)
            return self.current()
        if lang == self.lang:
            if confidence >= 0.6:
                self.tentative = False
            return self.lang
        # a different language: switch only on clear repeated evidence ...
        recent = self.history[-2:]
        two_in_a_row = len(recent) == 2 and all(o.lang == lang and o.words >= 2 for o in recent)
        one_strong = confidence >= 0.9 and words >= 4
        # ... or immediately, when the current language was only a tentative guess and this turn is clear
        fix_guess = self.tentative and confidence >= 0.6 and words >= 2
        if two_in_a_row or one_strong or fix_guess:
            self.lang = lang
            self.tentative = False
            self.switches += 1
        return self.lang

    def force(self, lang: str) -> None:
        if lang in LANGS:
            self.lang = lang


# a patient may simply ask for a language
_ASK = {
    "hi": re.compile(r"(?:हिंदी|हिन्दी)\s*(?:में|मे)|\bin hindi\b|\bhindi\s+(?:please|mein|me)\b|speak (?:in )?hindi", re.I),
    "mr": re.compile(r"मराठी\s*(?:मध्ये|मधे|त|में)|\bin marathi\b|\bmarathi\s+(?:please|madhye)\b|speak (?:in )?marathi", re.I),
    "en": re.compile(r"(?:इंग्रजी|अंग्रेज़ी|अंग्रेजी)\s*(?:मध्ये|मधे|में|मे|त)|\bin english\b|\benglish\s+please\b|speak (?:in )?english", re.I),
}


def explicit_request(text: str) -> Optional[str]:
    """'Hindi mein baat kijiye' / 'please speak in Marathi' -> that language."""
    for lang, rx in _ASK.items():
        if rx.search(text or ""):
            return lang
    return None
