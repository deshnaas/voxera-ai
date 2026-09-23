"""Adaptive clarification before any non-emergency conclusion.

    complaint -> topic -> (ask 1 natural question at a time) -> parse answer
              -> frozen emergency detector on the patient's own words
              -> conclusion (fixed vocabulary) or next question

Design rules
  * Deterministic. No LLM in the loop: questions are fixed, plain-language and
    fast, so triage turns are FASTER than the LLM path, not slower.
  * ``voxera_emergency.check_emergency`` (frozen) is the ONLY emergency
    authority. This module never declares an emergency itself. It hands the
    patient's own words (never fabricated text) to the detector and returns
    kind="emergency" only if the detector says so.
  * Triage may conclude URGENT_CLINICAL_REVIEW on its own red-flag reading, but
    never EMERGENCY_ESCALATION.
  * Voice arousal is a conversational hint: it can make the tone gentler and
    move the red-flag question earlier. It never escalates or suppresses.
  * No keyword -> diagnosis. Chest "gas" is NOT concluded to be gas; the
    conclusion wording never names a cause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .models import Conclusion, ConversationClinicalState

# ------------------------------------------------------------------
# Topic detection
# ------------------------------------------------------------------

_SYMPTOM_WORD = r"(pain|hurt|hurts|hurting|feel|feels|feeling|discomfort|weird|strange|tight|tightness|burn|burning|pressure|ache|aching|heavy|heaviness|uncomfortable|gas|acid|acidity|cramp|cramps|pinch|sharp|odd)"
TOPICS = {
    "chest": re.compile(rf"\b(chest|heartburn|heart burn)\b.*\b{_SYMPTOM_WORD}\b|\b{_SYMPTOM_WORD}\b.*\bchest\b|\bheartburn\b", re.I),
    "abdomen": re.compile(rf"\b(stomach|belly|abdomen|abdominal|tummy)\b.*\b{_SYMPTOM_WORD}\b|\b{_SYMPTOM_WORD}\b.*\b(stomach|belly|abdomen|tummy)\b", re.I),
    "head": re.compile(r"\b(headache|migraine|head (is )?(hurting|paining|pounding)|pain in (my )?head)\b", re.I),
    "breathing": re.compile(r"\b(breathless|short of breath|shortness of breath|breathing (is )?(hard|difficult|heavy)|wheez\w*|out of breath)\b", re.I),
}
TOPIC_LABEL = {"chest": "chest discomfort", "abdomen": "stomach pain", "head": "headache", "breathing": "breathing difficulty"}

# ------------------------------------------------------------------
# Answer parsing (deterministic, negation-aware)
# ------------------------------------------------------------------

_NEG = re.compile(r"\b(no|nope|not|never|none|nothing|neither|without|nah|don'?t|doesn'?t|didn'?t|haven'?t|isn'?t|aren'?t)\b", re.I)
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|a little|a bit|somewhat|kind of|sort of|i do|i am|i have|it is|it does)\b", re.I)

RED_FLAG_TERMS = {
    "trouble breathing": r"trouble breathing|difficulty breathing|short(ness)? of breath|breathless|can'?t breathe|hard to breathe|breathing (is )?(hard|difficult)|out of breath",
    "faintness": r"faint\w*|dizz\w*|light ?headed|black(ing)? out|pass(ed|ing)? out|about to pass out",
    "sweating": r"sweat\w*|clammy|cold sweat",
    "weakness": r"very weak|weakness|weak\b|no energy|exhausted",
    "confusion": r"confus\w*|disoriented|not (able|being able) to think",
    "vomiting blood / black stool": r"blood in (my )?(vomit|stool)|vomit\w* blood|black (stool|motion)|tarry",
    "severe pain": r"unbearable|worst pain|excruciating|severe pain",
}
_RED = {k: re.compile(v, re.I) for k, v in RED_FLAG_TERMS.items()}

QUALITY = [
    ("burning", r"burn\w*|acid\w*|heartburn|gas\b|gassy|bloat\w*|sour|reflux|burp\w*"),
    ("pressure or tightness", r"pressure|tight\w*|heav\w*|squeez\w*|crush\w*|weight on"),
    ("sharp", r"sharp|stab\w*|pinch\w*|stitch"),
    ("dull ache", r"dull|ache|aching|sore"),
    ("cramping", r"cramp\w*|colic|twist\w*"),
    ("throbbing", r"throb\w*|pound\w*|pulsat\w*"),
    ("discomfort", r"weird|strange|odd|uncomfortable|discomfort|not right|funny"),
]
_QUALITY = [(n, re.compile(p, re.I)) for n, p in QUALITY]
_SUDDEN = re.compile(r"sudden\w*|all of a sudden|out of (the )?blue|out of nowhere|abrupt\w*|just (now )?started|came on fast", re.I)
_GRADUAL = re.compile(r"gradual\w*|slowly|slow(ly)? (built|increased)|over (time|the day|a few)|little by little|built up", re.I)
_NOW = re.compile(r"\b(right now|at the moment|currently|still (there|hurts|have it|feel it)|it is (happening|there)|happening now|now also|yes it is)\b", re.I)
_GONE = re.compile(r"\b(gone|went away|stopped|settled|better now|fine now|normal now|not now|no longer|completely normal|feel (completely )?(normal|fine|ok|okay) now|passed)\b", re.I)
_PAST = re.compile(r"\b(last (week|month|night|year)|yesterday|days? ago|weeks? ago|earlier (today|this week)|a while ago|previously|before)\b", re.I)
_DURATION = re.compile(r"(since (yesterday|morning|last night|this morning|dinner|lunch|breakfast|\w+day)|for (about |around )?(a |an |\d+ |one |two |three |few |several )?(minutes?|hours?|days?|weeks?|months?)|(\d+|an?|one|two|three|few|several) (minutes?|hours?|days?|weeks?|months?) ago|after (dinner|lunch|breakfast|eating|a meal|food)|last (night|week|month))", re.I)
_RADIATE = {"arm": r"\barms?\b", "shoulder": r"shoulder", "jaw": r"\bjaw\b", "back": r"\bback\b", "neck": r"\bneck\b"}
_MEAL = re.compile(r"after (eating|dinner|lunch|breakfast|a meal|food)|(ate|eaten|eating|meal|spicy|oily)|gas|acid\w*|burp\w*|bloat\w*", re.I)
_BEFORE = re.compile(r"\b(happened|had (this|it)|happens?|has happened|occurs?|recurr\w*|again)\b.*\b(before|earlier|often|sometimes|regularly|many times|previously)\b|\b(first time|never (had|happened))\b|\bhappens? (often|sometimes)\b", re.I)
_SEV_SEVERE = re.compile(r"\b(severe|very bad|terrible|unbearable|worst|intense|extreme|really bad|a lot of pain|very painful)\b", re.I)
_SEV_MILD = re.compile(r"\b(mild|slight|little|minor|not (too )?bad|barely|light)\b", re.I)
_SEV_MOD = re.compile(r"\b(moderate|medium|so-?so|manageable|bearable|okay pain)\b", re.I)


def _negated_before(text: str, m: re.Match, window: int = 28) -> bool:
    return bool(_NEG.search(text[max(0, m.start() - window):m.start()]))


def parse_answer(text: str) -> dict:
    """Extract structured slots from one utterance. Returns only what is present."""
    t = (text or "").strip()
    out: dict[str, Any] = {}
    low = t.lower()
    for name, rx in _QUALITY:
        m = rx.search(t)
        if m and not _negated_before(t, m, 12):
            out["quality"] = name
            break
    if _SUDDEN.search(t):
        out["onset"] = "sudden"
    elif _GRADUAL.search(t):
        out["onset"] = "gradual"
    dm = _DURATION.search(t)
    if dm:
        out["duration"] = dm.group(0)
    if _GONE.search(t) or (_PAST.search(t) and not _NOW.search(t)):
        out["ongoing_now"] = False
    if _NOW.search(t):
        out["ongoing_now"] = True
    if _SEV_SEVERE.search(t):
        out["severity"] = "severe"
    elif _SEV_MOD.search(t):
        out["severity"] = "moderate"
    elif _SEV_MILD.search(t):
        out["severity"] = "mild"
    # radiation
    rad = []
    for name, p in _RADIATE.items():
        m = re.search(p, t, re.I)
        if m and not _negated_before(t, m):
            rad.append(name)
    if rad:
        out["radiation"] = ", ".join(rad)
    # red flags, each with its own negation window
    pos, neg = [], []
    for name, rx in _RED.items():
        m = rx.search(t)
        if not m:
            continue
        (neg if _negated_before(t, m) else pos).append(name)
    out["red_flags"], out["negated_red_flags"] = pos, neg
    # bare yes / no
    words = re.findall(r"[a-z']+", low)
    out["is_no"] = bool(_NEG.search(t)) and len(words) <= 6 and not pos
    out["is_yes"] = bool(_YES.search(t)) and len(words) <= 6 and not out["is_no"]
    out["meal_related"] = bool(_MEAL.search(t))
    out["history"] = bool(_BEFORE.search(t))
    m = re.search(r"\b(?:location|here|at)\b", low)
    for place in ("upper stomach", "lower stomach", "upper abdomen", "lower abdomen", "left side", "right side",
                  "center", "middle", "left chest", "right chest", "behind the breastbone", "forehead", "temples", "back of (my )?head"):
        if re.search(place, low):
            out["location"] = place
            break
    return out


# ------------------------------------------------------------------
# Question banks (natural wording; order = priority)
# ------------------------------------------------------------------

Q = {
    "quality": "Can you describe what you're feeling — pain, pressure, burning, tightness, or something else?",
    "redflags_chest": "Are you having any trouble breathing, feeling faint, unusually sweaty, or very weak?",
    "redflags_abdomen": "Have you had vomiting, blood in your vomit or stool, fainting, severe weakness, or trouble breathing?",
    "redflags_head": "Did it come on suddenly and severely, or come with weakness, confusion, trouble speaking, vision changes, or a stiff neck?",
    "redflags_breathing": "Are you able to speak in full sentences, and is there any chest pain, blue lips, or feeling faint?",
    "redflags_which": "Which of those are you having — trouble breathing, feeling faint, sweating, or weakness?",
    "onset": "When did it start, and did it come on suddenly or gradually?",
    "ongoing": "Is it happening right now?",
    "radiation": "Does it spread to your arm, shoulder, jaw, back, or neck?",
    "location": "Where exactly do you feel it?",
    "severity": "How severe is it right now — mild, moderate, or severe?",
    "meal": "Did you recently eat, and does it feel like acidity or gas?",
    "history": "Has this happened to you before?",
    "past_features": "When it happened, did it come with any trouble breathing, sweating, or spreading to your arm or jaw?",
    "recurrence": "Has it come back since, or is it completely gone?",
}

# (key, applies-to-topic tuple)
BANK = {
    "chest": ["quality", "redflags_chest", "ongoing", "onset", "radiation", "meal", "history"],
    "abdomen": ["location", "onset", "severity", "redflags_abdomen", "quality", "history"],
    "head": ["onset", "severity", "redflags_head", "history"],
    "breathing": ["redflags_breathing", "onset", "ongoing", "history"],
}
PAST_BANK = ["past_features", "recurrence"]          # chest discomfort that has already resolved
MAX_QUESTIONS = {"chest": 5, "abdomen": 5, "head": 4, "breathing": 4}
MUST_ASK = {"chest": "redflags_chest", "abdomen": "redflags_abdomen", "head": "redflags_head", "breathing": "redflags_breathing"}

SAFETY_NET = ("if you develop severe pressure, difficulty breathing, fainting, sudden weakness, or if it gets worse, "
              "please seek urgent medical care right away")

TEXT_CONCLUSION = {
    Conclusion.INSUFFICIENT_INFORMATION: (
        "I can't determine the cause from the call alone. Based on what you've told me so far, I don't have enough "
        f"information to classify this as an emergency, but {SAFETY_NET}."),
    Conclusion.ROUTINE_CLINICAL_REVIEW: (
        "I can't determine the cause from the call alone. Nothing you've told me so far points to an emergency, "
        f"but it's worth having a doctor look at it, and {SAFETY_NET}."),
    Conclusion.GENERAL_HOME_CARE: (
        "I can't determine the cause from the call alone. Based on what you've told me, I don't have enough "
        f"information to classify this as an emergency, but {SAFETY_NET}."),
    Conclusion.URGENT_CLINICAL_REVIEW: (
        "Because of what you've described, I think a doctor should see you soon, ideally today. "
        "I'm flagging this to the hospital now. If anything gets worse, or you feel faint, short of breath, or very unwell, "
        "please call emergency services immediately."),
}


@dataclass
class TriageStep:
    kind: str                      # ask | conclude | emergency | passthrough
    text: str = ""
    key: Optional[str] = None
    conclusion: Optional[str] = None
    emergency: Any = None          # EmergencyResult from the frozen detector, when kind == emergency
    handoff_care: bool = False     # after a benign conclusion, let the care/LLM layer continue


def detect_topic(text: str) -> Optional[str]:
    for topic, rx in TOPICS.items():
        if rx.search(text or ""):
            return topic
    return None


class TriagePlanner:
    skip_topics: frozenset = frozenset()

    def __init__(self, check_emergency: Callable[..., Any], state: Optional[ConversationClinicalState] = None):
        self.check_emergency = check_emergency
        self.state = state or ConversationClinicalState()
        self._patient_words: list[str] = []
        self._pending_key: Optional[str] = None
        self._active = False
        self._done_topics: set = set()
        self._which_asked = False
        self._unclear = 0

    # ---- external inputs ---------------------------------------------
    def set_patient(self, verified: bool, allergies=None, meds=None, history=None) -> None:
        s = self.state
        s.patient_verified = bool(verified)
        s.allergies = list(allergies or [])
        s.current_medications = list(meds or [])
        s.relevant_history = list(history or s.relevant_history)

    def set_voice(self, sig) -> None:
        if sig is not None and getattr(sig, "available", False):
            self.state.voice_arousal = sig.arousal_level
            self.state.voice_signal_confidence = sig.confidence

    @property
    def active(self) -> bool:
        return self._active

    # ---- main entry ----------------------------------------------------
    def process(self, text: str, *, last_assistant: str = "") -> TriageStep:
        s = self.state
        s.turns += 1
        topic = detect_topic(text)
        if topic in self.skip_topics:
            topic = None                                   # this topic is handled by the everyday-symptom flow

        if not self._active:
            if not topic or topic in self._done_topics:
                return TriageStep("passthrough")
            self._start(topic, text)
        elif topic and topic != s.topic and topic not in self._done_topics:
            self._start(topic, text)                       # patient moved to a different complaint
        else:
            self._absorb(text)

        self._patient_words.append(text)

        # 1. the frozen detector gets the patient's OWN accumulated words
        em = self._run_detector(text, last_assistant)
        if em is not None:
            s.emergency_status = "detected"
            s.conclusion = Conclusion.EMERGENCY_ESCALATION.value
            s.next_action = "existing emergency workflow"
            self._active = False
            return TriageStep("emergency", emergency=em, conclusion=s.conclusion)

        return self._decide()

    # ---- internals -------------------------------------------------------
    def _start(self, topic: str, text: str) -> None:
        s = self.state
        s.topic = topic
        s.chief_complaint = TOPIC_LABEL[topic]
        s.follow_up_questions = []
        s.conclusion = None
        s.red_flags, s.negated_red_flags = [], []
        s.quality = s.onset = s.duration = s.severity = s.location = s.radiation = s.ongoing_now = None
        self._patient_words = []
        self._pending_key = None
        self._which_asked = False
        self._unclear = 0
        self._active = True
        s.symptoms = [TOPIC_LABEL[topic]]
        self._absorb(text)

    def _absorb(self, text: str) -> None:
        s = self.state
        p = parse_answer(text)
        for k in ("quality", "onset", "duration", "severity", "location", "radiation", "ongoing_now"):
            if p.get(k) is not None:
                setattr(s, k, p[k])
        for f in p["red_flags"]:
            if f not in s.red_flags:
                s.red_flags.append(f)
                s.negated_red_flags = [n for n in s.negated_red_flags if n != f]
        for f in p["negated_red_flags"]:
            if f not in s.red_flags and f not in s.negated_red_flags:
                s.negated_red_flags.append(f)
        if p["meal_related"] and "meal or gas related" not in s.associated_symptoms:
            s.associated_symptoms.append("meal or gas related")
        if p["history"] and "has happened before" not in s.relevant_history:
            s.relevant_history.append("has happened before")

        # interpret a short yes/no against the question we just asked
        key = self._pending_key
        if key:
            if key.startswith("redflags"):
                if p["is_no"]:
                    for f in _flags_for(key):
                        if f not in s.negated_red_flags and f not in s.red_flags:
                            s.negated_red_flags.append(f)
                    s.follow_up_questions.append(key + ":answered")
                elif p["is_yes"] and not p["red_flags"]:
                    if key != "redflags_which" and not self._which_asked:
                        self._which_asked = True          # ambiguous "yes": ask which, once
                        self._pending_key = "redflags_which"
                        return
                    s.red_flags.append("unspecified warning symptom")      # still yes -> treat as positive
                elif p["red_flags"] or p["negated_red_flags"]:
                    s.follow_up_questions.append(key + ":answered")
            elif key == "ongoing" and p["is_no"]:
                s.ongoing_now = False
            elif key == "ongoing" and p["is_yes"]:
                s.ongoing_now = True
            elif key == "radiation" and p["is_no"]:
                s.radiation = "no"
            elif key == "meal" and p["is_yes"]:
                if "meal or gas related" not in s.associated_symptoms:
                    s.associated_symptoms.append("meal or gas related")
            elif key == "history":
                if p["is_yes"] and "has happened before" not in s.relevant_history:
                    s.relevant_history.append("has happened before")
                if p["is_no"] and "first time" not in s.relevant_history:
                    s.relevant_history.append("first time")
            elif key == "recurrence" and p["is_no"]:
                s.ongoing_now = False
            elif key == "past_features" and p["is_no"]:
                for f in ("trouble breathing", "sweating"):
                    if f not in s.negated_red_flags:
                        s.negated_red_flags.append(f)
                s.radiation = s.radiation or "no"
            # nothing usable at all?
            if not any(p.get(k) for k in ("quality", "onset", "duration", "severity", "location", "radiation", "ongoing_now")) \
                    and not (p["is_yes"] or p["is_no"] or p["red_flags"] or p["negated_red_flags"] or p["meal_related"] or p["history"]):
                self._unclear += 1

    def _run_detector(self, text: str, last_assistant: str):
        ctx = " ".join(self._patient_words[:-1]) if len(self._patient_words) > 1 else ""
        try:
            r = self.check_emergency(text, context=ctx, last_assistant=last_assistant or "")
            if r:
                return r
            if len(self._patient_words) > 1:
                # patient's own words joined (never synthesised) so combinations are seen together
                return self.check_emergency(" . ".join(self._patient_words), context="", last_assistant="")
        except Exception:                                   # noqa: BLE001
            return None
        return None

    def _past_only(self) -> bool:
        s = self.state
        return s.topic == "chest" and s.ongoing_now is False

    def _next_key(self) -> Optional[str]:
        s = self.state
        asked = {k.split(":")[0] for k in s.follow_up_questions}
        if self._pending_key == "redflags_which" and "redflags_which" not in asked:
            return "redflags_which"
        bank = list(PAST_BANK if self._past_only() else BANK[s.topic])
        must = MUST_ASK[s.topic]
        # high vocal arousal: the safety question moves to the front (a tone/priority hint only)
        if s.voice_arousal == "high" and must in bank and "quality" != must:
            bank.remove(must)
            bank.insert(0, must)
        for key in bank:
            if key in asked:
                continue
            if key == "quality" and s.quality:
                continue
            if key == "ongoing" and s.ongoing_now is not None:
                continue
            if key == "onset" and (s.onset or s.duration):
                continue
            if key == "radiation" and s.radiation:
                continue
            if key == "location" and s.location:
                continue
            if key == "severity" and s.severity:
                continue
            if key == "meal" and "meal or gas related" in s.associated_symptoms:
                continue
            if key == "history" and s.relevant_history:
                continue
            if key.startswith("redflags") and (s.red_flags or len(s.negated_red_flags) >= 2):
                continue
            return key
        return None

    def _decide(self) -> TriageStep:
        s = self.state
        must = MUST_ASK[s.topic]
        asked = {k.split(":")[0] for k in s.follow_up_questions}
        limit = MAX_QUESTIONS[s.topic]
        n_asked = len([k for k in asked if k != "redflags_which"])

        must_done = bool(s.red_flags) or len(s.negated_red_flags) >= 2 or must in asked or self._past_only() and "past_features" in asked

        # concerning answers -> conclude urgently rather than keep asking
        if s.red_flags and s.topic in ("chest", "abdomen", "breathing", "head"):
            return self._conclude(Conclusion.URGENT_CLINICAL_REVIEW)
        if s.radiation and s.radiation != "no" and s.topic == "chest" and s.ongoing_now is not False:
            return self._conclude(Conclusion.URGENT_CLINICAL_REVIEW)
        if s.severity == "severe" and (s.onset == "sudden" or s.ongoing_now):
            return self._conclude(Conclusion.URGENT_CLINICAL_REVIEW)

        if self._unclear >= 2:
            return self._conclude(Conclusion.INSUFFICIENT_INFORMATION)

        key = self._next_key()
        if key and (n_asked < limit or (must not in asked and not must_done)):
            return self._ask(key)

        # enough information
        benign = (s.quality == "burning" or "meal or gas related" in s.associated_symptoms) and not s.red_flags \
            and (not s.radiation or s.radiation == "no") and s.severity != "severe" and not self._past_only()
        if self._past_only():
            return self._conclude(Conclusion.ROUTINE_CLINICAL_REVIEW)
        if benign and s.topic in ("chest", "abdomen") and must_done:
            return self._conclude(Conclusion.GENERAL_HOME_CARE, handoff=True)
        if s.topic == "head" and s.severity == "severe":
            return self._conclude(Conclusion.URGENT_CLINICAL_REVIEW)
        return self._conclude(Conclusion.INSUFFICIENT_INFORMATION if not must_done else Conclusion.ROUTINE_CLINICAL_REVIEW)

    def _ask(self, key: str) -> TriageStep:
        s = self.state
        self._pending_key = key
        s.asked_last = key
        s.follow_up_questions.append(key)
        text = Q[key]
        if s.voice_arousal == "high" and s.turns <= 2:
            text = "I'm here with you. " + text                # gentler tone only
        elif s.turns == 1 and key == "quality":
            text = "Okay, let me understand a bit more. " + text
        remaining = max(0, MAX_QUESTIONS[s.topic] - len({k.split(':')[0] for k in s.follow_up_questions}))
        s.questions_remaining = remaining
        return TriageStep("ask", text=text, key=key)

    def _conclude(self, concl: Conclusion, handoff: bool = False) -> TriageStep:
        s = self.state
        s.conclusion = concl.value
        s.next_action = {
            Conclusion.URGENT_CLINICAL_REVIEW: "flag_to_hospital_urgent",
            Conclusion.ROUTINE_CLINICAL_REVIEW: "suggest_doctor_visit",
            Conclusion.GENERAL_HOME_CARE: "home_care_guidance_with_safety_net",
            Conclusion.INSUFFICIENT_INFORMATION: "safety_net_advice",
        }.get(concl, "none")
        s.questions_remaining = 0
        self._active = False
        self._done_topics.add(s.topic)
        self._pending_key = None
        return TriageStep("conclude", text=TEXT_CONCLUSION[concl], conclusion=concl.value, handoff_care=handoff)

    # ---- summary ------------------------------------------------------------
    def summary_context(self) -> dict:
        s = self.state
        d = {k: getattr(s, k) for k in ("chief_complaint", "symptoms", "duration", "onset", "severity", "quality",
                                        "location", "radiation", "red_flags", "negated_red_flags", "relevant_history",
                                        "conclusion", "next_action")}
        return {k: v for k, v in d.items() if v not in (None, "", [])}


def _flags_for(key: str) -> list:
    return {
        "redflags_chest": ["trouble breathing", "faintness", "sweating", "weakness"],
        "redflags_abdomen": ["vomiting blood / black stool", "faintness", "weakness", "trouble breathing"],
        "redflags_head": ["confusion", "weakness", "severe pain"],
        "redflags_breathing": ["trouble breathing", "faintness"],
        "redflags_which": ["trouble breathing", "faintness", "sweating", "weakness"],
    }.get(key, [])
