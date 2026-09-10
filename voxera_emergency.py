# ============================================================
# VOXERA — DETERMINISTIC EMERGENCY / SAFETY LAYER
# ============================================================
#
# NEVER calls an LLM.  Runs before the conversational model on
# every patient utterance.  Two internal stages, both fast and
# deterministic:
#
#   STAGE A  candidate extraction   (regex, high-confidence patterns)
#   STAGE B  context validation     (negation, temporal, echo,
#                                    symptom-combination, voice/throat
#                                    context)  -- NO second LLM call
#
#   check_emergency(text, context="", last_assistant="") -> EmergencyResult | None
#
# Rules that matter for the demo:
#   * "chest pain" is NOT required — tightness / pressure / heaviness /
#     squeezing / crushing in the chest all count (current, not past).
#   * A bare "can't speak" is NOT a stroke.  Stroke needs a strong sign
#     (facial droop, slurred speech) OR >= 2 weaker signs OR one weaker
#     sign that is explicitly sudden and has no throat/voice/cold context.
#   * Negated ("no chest pain") and resolved/past ("had it yesterday,
#     fine now") symptoms do not trigger.
#   * Text echoed back from Voxera's own last line (mic bleed / Whisper
#     contamination) is stripped before analysis.
# ============================================================

import re
from dataclasses import dataclass


@dataclass
class EmergencyResult:
    category: str
    severity: str          # "emergency" | "crisis"
    trigger: str
    spoken_response: str
    recommended_department: str


# ------------------------------------------------------------
# Canned spoken responses.  Short.  No false reassurance.
# ------------------------------------------------------------

_R_CARDIO_RESP = (
    "That could be an emergency. Please call emergency services or get "
    "to the nearest emergency room right now. I'm escalating this to the "
    "hospital now. If you can, have someone stay with you."
)
_R_BREATHING = (
    "That could be an emergency. Please call emergency services right now, "
    "or have someone take you to the nearest emergency room. I'm escalating "
    "this to the hospital now."
)
_R_NEURO = (
    "These can be signs of a stroke and every minute matters. Please call "
    "emergency services right now. I'm escalating this to the hospital now."
)
_R_BLEED = (
    "That could be an emergency. Apply firm pressure to the bleeding and "
    "call emergency services right now. I'm escalating this to the hospital."
)
_R_GENERAL = (
    "That could be an emergency. Please get emergency medical help right "
    "now. I'm escalating this to the hospital now."
)
_R_CRISIS = (
    "I'm really glad you told me. You deserve support right now. Please "
    "stay on the line if you can, move away from anything you could use "
    "to hurt yourself, and reach out to someone you trust. If you might "
    "act on this now, please call your local emergency number or a "
    "suicide helpline. I'm flagging this so someone from the care team "
    "can follow up with you."
)
_FEVER_RESPONSE = (
    "A fever that high with these symptoms needs to be seen urgently. "
    "Please have someone take you to the emergency department now, or "
    "call emergency services if you can't travel safely. I'm escalating "
    "this to the hospital now."
)

CANNED_RESPONSES = [
    _R_CARDIO_RESP, _R_BREATHING, _R_NEURO, _R_BLEED,
    _R_GENERAL, _R_CRISIS, _FEVER_RESPONSE,
]


def _rx(p):
    return re.compile(p, re.IGNORECASE)


# ============================================================
# STAGE A — high-confidence patterns  (stroke handled separately)
# ============================================================

_CHEST_WORD = r"(?:chest|rib cage|breastbone|sternum)"
_CHEST_SENSATION = (
    r"(?:tight(?:ness|ening)?|pressure|heav(?:y|iness)|squeez(?:e|ing)|"
    r"cru(?:sh|shing)|clench(?:ing)?|band around|elephant on|weight on|"
    r"pain|ache|aching|hurts?|discomfort)"
)

_CRITICAL_RULES = [
    # --- Chest -------------------------------------------------------
    (_rx(rf"{_CHEST_SENSATION}[^.\n]{{0,40}}{_CHEST_WORD}"),
     "cardiac_chest", "Cardiology", _R_CARDIO_RESP),
    (_rx(rf"{_CHEST_WORD}[^.\n]{{0,40}}{_CHEST_SENSATION}"),
     "cardiac_chest", "Cardiology", _R_CARDIO_RESP),
    (_rx(r"something (?:is )?squeezing my chest"),
     "cardiac_chest", "Cardiology", _R_CARDIO_RESP),
    (_rx(r"\bangina\b"),
     "cardiac_chest", "Cardiology", _R_CARDIO_RESP),
    (_rx(r"(?:pain|numb(?:ness)?|tingling|heavy|ache)[^.\n]{0,40}"
         r"(?:left|right)?\s*(?:arm|jaw|shoulder)[^.\n]{0,40}"
         r"(?:chest|sweat|nausea|breath)"),
     "cardiac_chest", "Cardiology", _R_CARDIO_RESP),
    (_rx(r"chest[^.\n]{0,40}(?:spreading|radiat\w*)[^.\n]{0,20}"
         r"(?:arm|jaw|shoulder|back)"),
     "cardiac_chest", "Cardiology", _R_CARDIO_RESP),

    # --- Breathing ------------------------------------------------
    (_rx(r"(?:can'?t|cannot|can not|unable to|hard to|struggling to|"
         r"really hard to|trouble|difficulty|difficult to)\s+breathe?"),
     "respiratory", "Emergency", _R_BREATHING),
    (_rx(r"(?:can'?t|cannot)\s+catch (?:my|your) breath"),
     "respiratory", "Emergency", _R_BREATHING),
    (_rx(r"\b(?:choking|suffocating|gasping for air|not getting enough air|"
         r"can'?t get air)\b"),
     "respiratory", "Emergency", _R_BREATHING),
    (_rx(r"(?:throat|tongue)[^.\n]{0,20}(?:closing up|closing|swelling shut|swollen shut)"),
     "anaphylaxis", "Emergency", _R_BREATHING),
    (_rx(r"\banaphyla(?:xis|ctic)\b"),
     "anaphylaxis", "Emergency", _R_BREATHING),
    (_rx(r"\b(?:lips|face|hands) (?:are |is )?(?:turning )?blue\b"),
     "respiratory", "Emergency", _R_BREATHING),

    # --- Consciousness / seizure -------------------------------
    (_rx(r"\b(?:unconscious|passed out|blacked out|lost consciousness|"
         r"unresponsive|not responding|won'?t wake up|can'?t wake (?:him|her|them)"
         r"|collapsed and)\b"),
     "unconscious", "Emergency", _R_GENERAL),
    (_rx(r"\b(?:having a seizure|is seizing|seizure right now|convulsing|"
         r"convulsion)\b"),
     "seizure", "Emergency", _R_GENERAL),
    (_rx(r"\b(?:just )?faint(?:ed|ing)\b"),
     "syncope", "Emergency", _R_GENERAL),

    # --- Bleeding ---------------------------------------------
    (_rx(r"(?:severe|heavy|uncontrolled|a lot of|lots of|won'?t stop|"
         r"can'?t stop)[^.\n]{0,20}bleed\w*"),
     "hemorrhage", "Emergency", _R_BLEED),
    (_rx(r"bleed\w*[^.\n]{0,25}(?:heavil\w*|badly|a lot|uncontroll\w*|"
         r"profus\w*|everywhere|non.?stop|won'?t stop|can'?t stop|"
         r"will not stop|not stopping)"),
     "hemorrhage", "Emergency", _R_BLEED),
    (_rx(r"(?:vomit\w*|throw\w* up|throwing up|coughing up|coughed up|"
         r"spitting up)[^.\n]{0,15}blood"),
     "hemorrhage", "Emergency", _R_BLEED),

    # --- Poisoning / overdose -------------------------------
    (_rx(r"\b(?:overdose|overdosed|took too many pills|swallowed poison|"
         r"drank (?:bleach|poison))\b"),
     "poisoning", "Emergency", _R_GENERAL),

    # --- Obstetric -----------------------------------------
    (_rx(r"(?:pregnant|pregnancy)[^.\n]{0,40}(?:heavy bleeding|bleeding heavily|"
         r"severe (?:pain|cramp))"),
     "obstetric", "Emergency", _R_GENERAL),
]

_SELF_HARM = _rx(
    r"(?:want to die|wanna die|ready to die|don'?t want to (?:live|be alive)|"
    r"kill(?:ing)?\s+myself|killed myself|end(?:ing)? my life|end it all|"
    r"take my (?:own )?life|taking my (?:own )?life|"
    r"better off dead|no reason to live|nothing to live for|"
    r"going to hurt myself|hurt(?:ing)? myself|harm(?:ing)? myself|"
    r"suicidal|commit(?:ting)? suicide|thinking about suicide)"
)


# --- Stroke -------------------------------------------------

_STROKE_STRONG = _rx(
    r"\bface (?:is |has )?(?:started )?droop\w*"
    r"|facial droop\w*"
    r"|drooping on one side"
    r"|one side of my face (?:is |has gone |went )?(?:droop\w*|numb|weak)"
    r"|slurr\w+ speech"
    r"|speech (?:\w+ ){0,3}slurr\w+"
    r"|(?:i'?m |i am |i keep )?slurring (?:my )?(?:words|speech)"
    r"|words (?:\w+ ){0,3}slurr\w+"
    r"|my speech (?:\w+ ){0,3}(?:slurr\w+|jumbled|not clear|coming out wrong)"
)

_STROKE_WEAK = [
    ("speech_loss", _rx(
        r"\b(?:can'?t|cannot|could not|couldn'?t) (?:speak|talk)(?! because)"
        r"|lost my speech|unable to speak|difficulty speaking|trouble speaking"
        r"|can'?t get (?:my )?words out|words won'?t come out")),
    ("one_side_weak", _rx(
        r"weak(?:ness)? (?:on|down|in) (?:one|the left|the right|my left|my right) side"
        r"|(?:one|left|right) side (?:of my body )?(?:is |feels |went |has gone )?weak"
        r"|(?:my )?(?:left|right) (?:arm|leg|hand) (?:is |feels |went |has gone )?weak"
        r"|can'?t move my (?:left|right) (?:arm|leg|side|hand)"
        r"|(?:left|right) side (?:is |feels )?(?:paraly[sz]ed|dead)")),
    ("one_side_numb", _rx(
        r"numb(?:ness)? (?:on|down|in) (?:one|the left|the right|my left|my right) side"
        r"|(?:one|left|right) side (?:of my body |of my face )?(?:is |feels |went )?numb"
        r"|(?:my )?(?:left|right) (?:arm|leg|hand|side|face) (?:is |feels |went |has gone )?numb"
        r"|tingling down one side")),
    ("sudden_confusion", _rx(
        r"sudden(?:ly)? (?:very )?confus\w+|suddenly can'?t think (?:straight|clearly)"
        r"|suddenly (?:disoriented|not making sense)")),
    ("vision", _rx(
        r"sudden(?:ly)? (?:lost my vision|can'?t see|vision (?:is |has gone )?gone"
        r"|blurred vision|double vision)|lost (?:the )?vision in one eye")),
    ("droopy_face_feel", _rx(r"face (?:feels|is) (?:really )?droopy|my face feels off on one side")),
]

_SUDDEN = _rx(r"\b(?:sudden(?:ly)?|all of a sudden|out of nowhere|just started|came on fast)\b")

# throat / voice / cold context that explains "can't speak" without a stroke
_VOICE_CTX = _rx(
    r"\b(?:sore throat|throat hurts|throat is (?:sore|raw|killing)|my throat|strep"
    r"|hoarse|laryngitis|voice is (?:gone|hoarse|croaky)|lost my voice|no voice"
    r"|losing my voice|cold|blocked nose|stuffy nose|congest\w+|sinus|"
    r"cough\w*|flu|tonsil\w*)\b"
)


# ============================================================
# STAGE B helpers — context validation
# ============================================================

def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _sentences(x):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", x or "") if s.strip()]


def _tok(s):
    return set(re.findall(r"[a-z0-9']+", s.lower()))


def _collapse_repeats(text):
    """'okay okay okay.' -> 'okay.'   and drop immediately repeated sentences
    (a common Whisper failure on trailing near-silence / echo)."""
    if not text:
        return text
    text = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", text, flags=re.IGNORECASE)
    out = []
    prev = None
    for s in _sentences(text):
        key = _norm(s)
        if key and key == prev:
            continue
        # also skip a sentence that is just a repeated filler word
        if re.fullmatch(r"(?:ok(?:ay)?|yeah|uh|um|hmm|right)[.!?]?", key):
            if prev and prev.startswith(key[:2]):
                continue
        out.append(s)
        prev = key
    return " ".join(out) if out else text


def _strip_echo(text, last_assistant):
    """Remove patient 'sentences' that are really Voxera's previous line
    bleeding back through the mic / Whisper."""
    if not text or not last_assistant:
        return text or ""
    la_sents = [_tok(s) for s in _sentences(_norm(last_assistant)) if len(s) > 8]
    if not la_sents:
        return text
    kept = []
    for s in _sentences(text):
        st = _tok(s)
        if len(st) >= 3:
            echo = any(la and len(st & la) / len(st) >= 0.8 for la in la_sents)
            if echo:
                continue
        kept.append(s)
    return " ".join(kept) if kept else ""


_NEGATOR = _rx(
    r"(?:\bno\b|\bnot\b|\bnone\b|\bnever\b|\bwithout\b|\bany\b|"
    r"\bdenies?\b|\bdenied\b|\bdon'?t have\b|\bdoesn'?t have\b|"
    r"\bdidn'?t have\b|\bdon'?t\b|\bdoesn'?t\b|\bhaven'?t\b|"
    r"\bno more\b|\bwent away\b|\bgone now\b)"
)

_RESOLVED = _rx(
    r"\b(?:went away|gone now|gone away|stopped now|has stopped|stopped after|"
    r"resolved|passed now|has passed|no longer|better now|much better|"
    r"feeling better|feel fine now|i'?m fine now|i'?m ok now|i am fine now|"
    r"completely gone|totally gone|all better|not anymore|settled down|"
    r"eased off|cleared up|fine now)\b"
)

_PAST_MARKER = _rx(
    r"\b(?:yesterday|last night|last week|last month|earlier(?: today)?|"
    r"this morning|a (?:few |couple (?:of )?)?days? ago|a while ago|"
    r"the other day|used to|before(?: this| today| all this)?)\b"
)


def _negated(norm, start, end=None, window=30):
    pre = norm[max(0, start - window):start]
    mneg = _NEGATOR.search(pre)
    if mneg and not re.search(r"\b(but|however|now|suddenly|actually|except)\b",
                              pre[mneg.end():]):
        return True
    if end is not None and _RESOLVED.search(norm[end:end + 40]):
        return True
    return False


def _is_past(norm, start, end, window=70):
    """True only when there is a past-time marker AND explicit resolution
    ('had chest pain yesterday, fine now').  'since yesterday' / ongoing
    wording stays CURRENT."""
    span = norm[max(0, start - window):min(len(norm), end + window)]
    if not _PAST_MARKER.search(span):
        return False
    if re.search(r"\b(?:since|for the last|for the past|going on|still|"
                 r"but now|now it'?s worse|getting worse)\b", span):
        return False
    return bool(_RESOLVED.search(span))


def _blocked(norm, start, end):
    return _negated(norm, start, end) or _is_past(norm, start, end)


# ============================================================
# STROKE decision (STAGE B combination logic)
# ============================================================

def _check_stroke(norm, full_context):
    strong = _STROKE_STRONG.search(norm)
    weak_hits = [(name, m) for name, rx in _STROKE_WEAK
                 for m in [rx.search(norm)] if m]
    if not strong and not weak_hits:
        return None

    voice_ctx = bool(_VOICE_CTX.search(full_context))
    weak_names = [n for n, _ in weak_hits]
    only_speech = (not strong) and set(weak_names) <= {"speech_loss"}

    # "can't speak" + a sore throat / cold anywhere in context -> not a stroke
    if only_speech and voice_ctx:
        return None

    if strong:
        m = strong
        trigger = strong.group(0).strip()
    elif len(set(weak_names)) >= 2:
        m = weak_hits[0][1]
        trigger = " + ".join(sorted(set(weak_names)))
    elif _SUDDEN.search(norm) and not (only_speech and voice_ctx):
        m = weak_hits[0][1]
        trigger = weak_names[0] + " (sudden onset)"
    else:
        return None

    if _blocked(norm, m.start(), m.end()):
        return None

    return EmergencyResult(
        category="stroke",
        severity="emergency",
        trigger=trigger,
        spoken_response=_R_NEURO,
        recommended_department="Neurology",
    )


# ============================================================
# HIGH FEVER + red flag
# ============================================================

_HIGH_FEVER = _rx(r"(?:fever|temperature|temp)\D{0,25}(10[4-9](?:\.\d+)?|11\d(?:\.\d+)?)")
_FEVER_RED_FLAGS = [
    "can't walk", "cannot walk", "could not walk", "unable to walk",
    "can't stand", "cannot stand", "unable to stand", "can't get up",
    "can't sit up", "very weak", "extremely weak", "so weak", "really weak",
    "confused", "confusion", "not making sense", "stiff neck", "neck is stiff",
    "rash that won't fade", "purple rash", "won't wake", "hard to wake",
    "seizure", "fainting", "fainted", "passing out", "severe headache",
    "worst headache", "vomiting everything", "can't keep fluids",
    "cannot keep fluids", "not urinating", "lips are blue", "turning blue",
]


# ============================================================
# PUBLIC ENTRY
# ============================================================

def check_emergency(text, context="", last_assistant=""):
    """Return an EmergencyResult for a probable CURRENT emergency, else None.

    text           : the latest patient utterance (raw transcript)
    context        : recent conversation transcript (for symptom context)
    last_assistant : Voxera's previous spoken line (stripped out as echo)
    """
    cleaned = _collapse_repeats(_strip_echo(text or "", last_assistant))
    norm = _norm(cleaned)
    if not norm or len(_tok(norm)) < 1:
        return None

    full_context = (norm + " . " + _norm(context))[:2000]

    # 1. self-harm / suicide
    m = _SELF_HARM.search(norm)
    if m and not _negated(norm, m.start(), m.end()):
        return EmergencyResult("self_harm", "crisis", m.group(0),
                               _R_CRISIS, "Mental Health")

    # 2. stroke (combination logic + throat/voice context)
    stroke = _check_stroke(norm, full_context)
    if stroke:
        return stroke

    # 3. other high-confidence physical emergencies
    for rx, category, dept, response in _CRITICAL_RULES:
        for mm in rx.finditer(norm):
            if _blocked(norm, mm.start(), mm.end()):
                continue
            return EmergencyResult(category, "emergency",
                                   mm.group(0).strip(), response, dept)

    # 4. very high fever + at least one red flag
    fm = _HIGH_FEVER.search(norm)
    if fm and any(flag in full_context for flag in _FEVER_RED_FLAGS):
        if not _blocked(norm, fm.start(), fm.end()):
            return EmergencyResult("febrile_illness", "emergency",
                                   fm.group(0).strip(), _FEVER_RESPONSE,
                                   "Emergency")

    return None


# ------------------------------------------------------------
if __name__ == "__main__":
    cases = [
        ("My chest feels really tight and heavy.", "", "", True),
        ("Something is squeezing my chest.", "", "", True),
        ("I can't breathe properly.", "", "", True),
        ("My face is drooping and my left arm went numb.", "", "", True),
        ("My speech suddenly became slurred.", "", "", True),
        ("I've had a fever of 104 and I'm very weak and confused.", "", "", True),
        ("I think I want to die.", "", "", True),
        # must NOT trigger
        ("I've had a mild cough since yesterday.", "", "", False),
        ("My fever is about 100 and I feel tired.", "", "", False),
        ("I have a blocked nose and a sore throat, my voice is basically gone.",
         "", "", False),
        ("I have a bad cold and I can't really speak, my throat hurts so much.",
         "", "", False),
        ("I had chest pain yesterday but it's completely gone now.", "", "", False),
        ("I couldn't breathe yesterday but I'm fine now.", "", "", False),
        ("No, I don't have any chest pain.", "", "", False),
        ("I have a fever of 104 and a blocked nose.", "", "", False),
        # echo contamination -> must NOT trigger stroke
        ("you can't speak or stand or sit. but what do i do though",
         "PATIENT: my throat is burning and my nose is blocked",
         "Okay, you can't speak or stand or sit. You should see a doctor right away.",
         False),
    ]
    ok = 0
    for text, ctx, la, expect in cases:
        r = check_emergency(text, ctx, la)
        got = r is not None
        flag = "OK " if got == expect else "XX "
        ok += got == expect
        print(f"{flag} expect={expect!s:5} got={got!s:5} {text[:60]!r}"
              + (f" -> {r.category}/{r.trigger!r}" if r else ""))
    print(f"\n{ok}/{len(cases)} passed")
