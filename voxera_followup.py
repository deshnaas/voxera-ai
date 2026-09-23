"""Everyday-symptom conversation: help first, then ask, then conclude.  (English / Hindi / Marathi, no LLM.)

    caller mentions a symptom
      -> a warm acknowledgement + the first-aid / home-remedy / over-the-counter advice (voxera_care, safety-gated)
      -> 3-4 short follow-up questions, ONE at a time (red flags first, then how bad, how long, anything else)
      -> only then a conclusion: manage at home / see a doctor soon / be seen today

Rules this module keeps
  * The frozen emergency detector still runs on every caller turn BEFORE this (voxera.Call.run_turn). Nothing here
    declares or suppresses an emergency.
  * Advice text is never invented: care steps, OTC lines and "get help if" come from voxera_care (English) and the
    reviewable catalog in voxera_multilang (Hindi / Marathi). No doses. No diagnosis - it never names a cause.
  * A positive red-flag answer always makes the conclusion at least "see a doctor today". If an answer can't be
    understood twice, Voxera says it wasn't sure and recommends seeing a doctor - it never assumes "fine".
  * Hindi / Marathi wording below has NOT been reviewed by a clinician (same warning as voxera_multilang/catalog.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------------------------------------------
# 1. what the caller said -> which care topic(s)
# ---------------------------------------------------------------------------------------------------------------

_T = {
    "mild_fever": r"\b(fever|feverish|temperature|running a temp)\b",
    "mild_headache": r"\b(headache|migraine|head (is |has )?(hurt\w*|ach\w*|pain\w*|pound\w*)|my head hurts?)\b",
    "cold_congestion": r"\b(cold|runny nose|blocked nose|stuffy nose|congest\w*|sneez\w*)\b",
    "sore_throat": r"\b(sore throat|throat (is )?(sore|pain\w*|hurt\w*|itch\w*)|pain in (my )?throat)\b",
    "mild_cough": r"\b(cough\w*)\b",
    "mild_indigestion": r"\b(acidity|indigestion|heartburn|acid reflux|gas(sy)?|bloated|bloating|upset stomach)\b",
    "mild_dehydration": r"\b(dehydrat\w*|very thirsty|dry mouth)\b",
    "minor_sprain": r"\b(sprain\w*|twist\w*|rolled my ankle)\b",
    "minor_burn": r"\b(burn(ed|t)? (my|the)|scald\w*|a burn)\b",
    "minor_cut": r"\b(cut (my|the|myself)|a cut|deep cut|small cut)\b",
    "minor_abrasion": r"\b(graze\w*|scrape\w*|scratch\w*|scraped)\b",
}
_TOPIC_RX = {k: re.compile(v, re.I) for k, v in _T.items()}
_PRIORITY = ["mild_fever", "mild_headache", "sore_throat", "mild_cough", "cold_congestion", "mild_indigestion",
             "mild_dehydration", "minor_sprain", "minor_burn", "minor_cut", "minor_abrasion"]
_NEGATED_TOPIC = re.compile(r"\b(no|not|without|don'?t have|haven'?t got|no more)\s+(a |any )?$", re.I)

LABEL = {"mild_fever": "fever", "mild_headache": "headache", "cold_congestion": "cold", "sore_throat": "sore throat",
         "mild_cough": "cough", "mild_indigestion": "acidity", "mild_dehydration": "dehydration",
         "minor_sprain": "sprain", "minor_burn": "burn", "minor_cut": "cut", "minor_abrasion": "graze"}


def detect_topics(text: str) -> list:
    """Care topics mentioned in `text` (English understanding), most important first. Negated mentions ('no fever')
    don't count."""
    found = []
    t = text or ""
    for cid in _PRIORITY:
        m = _TOPIC_RX[cid].search(t)
        if m and not _NEGATED_TOPIC.search(t[:m.start()][-14:]):
            found.append(cid)
    return found


# ---------------------------------------------------------------------------------------------------------------
# 2. reading the caller's answers
# ---------------------------------------------------------------------------------------------------------------

_NO = re.compile(r"^\W*(no|nope|nah|not really|none|nothing|not at all|no i (don'?t|do not|haven'?t)|i (don'?t|do not) (have|think so)|nothing like that)\b", re.I)
_YES = re.compile(r"\b(yes|yeah|yep|yup|i do|i have|it does|it is|a little|a bit|some|somewhat|sort of|kind of|there is|there's)\b", re.I)
_DONT_KNOW = re.compile(r"\b(not sure|don'?t know|no idea|can'?t say|maybe)\b", re.I)

_SEV = [("severe", r"\b(severe|very bad|really bad|terrible|unbearable|worst|awful|extreme|intense|very (much|painful)|a lot|"
                  r"(very|really|extremely|too|so) high|burning up|through the roof)\b"),
        ("mild", r"\b(mild|slight|little|small|not (too )?bad|minor|manageable|light)\b"),
        ("moderate", r"\b(moderate|medium|so-?so|okay|ok|in between|fairly)\b")]
_NUM = re.compile(r"\b(10|[1-9])\b(?:\s*(?:out of|/)\s*10)?")
_DUR = re.compile(r"\b(\d+|a|an|one|two|three|four|five|six|seven|few|couple)\s*(?:of\s*)?(hour|hr|day|week|month)s?\b|"
                  r"\b(since|from)\s+(yesterday|last night|this morning|morning|last week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|"
                  r"\b(yesterday|last night|this morning|today|just now|right now)\b", re.I)
_WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "few": 3, "couple": 2}


def parse_severity(text: str) -> Optional[str]:
    t = text or ""
    for lab, rx in _SEV:
        if re.search(rx, t, re.I):
            return lab
    m = _NUM.search(t)
    if m:
        n = int(m.group(1))
        return "severe" if n >= 7 else ("moderate" if n >= 4 else "mild")
    return None


def parse_duration_days(text: str) -> Optional[float]:
    m = _DUR.search(text or "")
    if not m:
        return None
    g = m.group(0).lower()
    if m.group(1):
        raw = m.group(1).lower()
        n = int(raw) if raw.isdigit() else _WORDNUM.get(raw, 1)
        unit = m.group(2).lower()
        return n * {"hour": 1 / 24, "hr": 1 / 24, "day": 1, "week": 7, "month": 30}[unit]
    if "last week" in g:
        return 7
    if any(w in g for w in ("yesterday", "last night", "since morning", "this morning", "today", "just now", "right now")):
        return 1 if ("yesterday" in g or "last night" in g) else 0.3
    return 2


_FLAG_WORDS = {
    "mild_fever": r"stiff neck|rash|confus\w*|trouble breathing|hard to breathe|short of breath|can'?t breathe|fit|seizure|drowsy",
    "mild_headache": r"worst|sudden\w*|thunderclap|vomit\w*|vision|blurr\w*|weak\w*|numb\w*|stiff neck|confus\w*|slurr\w*",
    "cold_congestion": r"high fever|face pain|sinus pain|trouble breathing|short of breath|hard to breathe",
    "sore_throat": r"can'?t swallow|hard to swallow|drool\w*|trouble breathing|hard to breathe|swell\w*",
    "mild_cough": r"blood|wheez\w*|short of breath|trouble breathing|hard to breathe|chest pain",
    "mild_indigestion": r"arm|jaw|back|sweat\w*|breathless|short of breath|faint\w*|dizzy|vomit\w* blood|black stool",
    "mild_dehydration": r"can'?t keep|throwing up|vomit\w*|very dizzy|little urine|no urine|confus\w*|faint\w*",
    "minor_sprain": r"can'?t (put )?weight|swollen|swelling|out of shape|deform\w*|numb\w*|very painful",
    "minor_burn": r"bigger than|large|blister\w*|face|hand|private|deep|white|charred",
    "minor_cut": r"heav\w*|deep|dirty|won'?t stop|spurt\w*|gap\w*",
    "minor_abrasion": r"dirt|grit|stuck|bleeding a lot|heav\w*",
}
SEE_AFTER_DAYS = {"mild_fever": 3, "mild_headache": 3, "cold_congestion": 10, "sore_throat": 7, "mild_cough": 7,
                  "mild_indigestion": 3, "mild_dehydration": 1, "minor_sprain": 3, "minor_burn": 3, "minor_cut": 3,
                  "minor_abrasion": 3}

# ---------------------------------------------------------------------------------------------------------------
# 3. wording (English source of truth; Hindi / Marathi unreviewed)
# ---------------------------------------------------------------------------------------------------------------

Q = {   # question key -> {lang: text}
    "severity": {"en": "How bad would you say it is: mild, moderate, or severe?",
                 "hi": "यह कितना ज़्यादा है: हल्का, मध्यम, या तेज़?",
                 "mr": "हे किती जास्त आहे: सौम्य, मध्यम, की तीव्र?"},
    "duration": {"en": "How long has this been going on?",
                 "hi": "यह कब से हो रहा है?",
                 "mr": "हे कधीपासून होत आहे?"},
    "other": {"en": "Are you having any other symptoms along with it that worry you?",
              "hi": "क्या इसके साथ कोई और लक्षण हैं जो आपको परेशान कर रहे हैं?",
              "mr": "याच्यासोबत आणखी काही त्रास आहेत का जे तुम्हाला काळजीत टाकतात?"},
    "tried": {"en": "Have you taken or tried anything for it so far?",
              "hi": "क्या आपने अब तक इसके लिए कुछ लिया या आज़माया है?",
              "mr": "तुम्ही आतापर्यंत यासाठी काही घेतले किंवा करून पाहिले आहे का?"},
    "flags_mild_fever": {"en": "Do you have a stiff neck, a rash, confusion, or any trouble breathing?",
                         "hi": "क्या आपकी गर्दन अकड़ी है, कोई चकत्ते हैं, उलझन है, या साँस लेने में तकलीफ़ है?",
                         "mr": "मान ताठ झाली आहे का, पुरळ आहे का, गोंधळ होत आहे का, किंवा श्वास घेण्यास त्रास आहे का?"},
    "flags_mild_headache": {"en": "Did it come on suddenly, or is it the worst headache you've ever had? Any vomiting, vision changes, or weakness?",
                            "hi": "क्या यह अचानक शुरू हुआ, या यह आपके जीवन का सबसे बुरा सिरदर्द है? कोई उल्टी, नज़र में बदलाव, या कमज़ोरी?",
                            "mr": "हे अचानक सुरू झाले का, किंवा आयुष्यातली सर्वात वाईट डोकेदुखी आहे का? उलटी, दृष्टीत बदल, किंवा अशक्तपणा आहे का?"},
    "flags_cold_congestion": {"en": "Do you have a high fever, pain in your face, or any trouble breathing?",
                              "hi": "क्या आपको तेज़ बुखार, चेहरे में दर्द, या साँस लेने में तकलीफ़ है?",
                              "mr": "तुम्हाला तीव्र ताप, चेहऱ्यावर दुखणे, किंवा श्वास घेण्यास त्रास आहे का?"},
    "flags_sore_throat": {"en": "Is it hard to swallow, or hard to breathe?",
                          "hi": "क्या निगलने में या साँस लेने में मुश्किल हो रही है?",
                          "mr": "गिळायला किंवा श्वास घ्यायला अवघड जात आहे का?"},
    "flags_mild_cough": {"en": "Are you coughing up blood, wheezing, or feeling short of breath?",
                         "hi": "क्या खाँसी में खून आ रहा है, साँस में सीटी जैसी आवाज़ है, या साँस फूल रही है?",
                         "mr": "खोकल्यातून रक्त येत आहे का, श्वासात शिट्टीसारखा आवाज येतो का, किंवा दम लागतो का?"},
    "flags_mild_indigestion": {"en": "Does the discomfort spread to your arm, jaw or back, or come with sweating, dizziness or breathlessness?",
                               "hi": "क्या तकलीफ़ हाथ, जबड़े या पीठ तक जाती है, या पसीना, चक्कर या साँस फूलने के साथ आती है?",
                               "mr": "त्रास हात, जबडा किंवा पाठीपर्यंत जातो का, किंवा घाम, चक्कर किंवा दम लागण्यासोबत येतो का?"},
    "flags_mild_dehydration": {"en": "Are you unable to keep fluids down, very dizzy, or passing very little urine?",
                               "hi": "क्या आप तरल पदार्थ पचा नहीं पा रहे, बहुत चक्कर आ रहे हैं, या पेशाब बहुत कम हो रहा है?",
                               "mr": "तुम्हाला द्रव पदार्थ टिकत नाहीत का, खूप चक्कर येतात का, किंवा लघवी खूप कमी होते का?"},
    "flags_minor_sprain": {"en": "Is it badly swollen, out of shape, or too painful to put any weight on?",
                           "hi": "क्या बहुत सूजन है, आकार बिगड़ा है, या इतना दर्द है कि वज़न नहीं डाल सकते?",
                           "mr": "खूप सूज आहे का, आकार बिघडला आहे का, किंवा वजन टाकता येणार नाही इतके दुखते का?"},
    "flags_minor_burn": {"en": "Is the burn bigger than your palm, blistering, or on your face, hands or private area?",
                         "hi": "क्या जलन हथेली से बड़ी है, फफोले हैं, या चेहरे, हाथों या निजी अंग पर है?",
                         "mr": "भाजलेली जागा तळहातापेक्षा मोठी आहे का, फोड आले आहेत का, किंवा चेहरा, हात किंवा खाजगी भागावर आहे का?"},
    "flags_minor_cut": {"en": "Is it still bleeding heavily, or is it deep or dirty?",
                        "hi": "क्या अभी भी बहुत खून बह रहा है, या घाव गहरा या गंदा है?",
                        "mr": "अजूनही खूप रक्त वाहत आहे का, किंवा जखम खोल किंवा घाण आहे का?"},
    "flags_minor_abrasion": {"en": "Is there dirt stuck in it, or is it bleeding a lot?",
                             "hi": "क्या उसमें मिट्टी फँसी है, या बहुत खून बह रहा है?",
                             "mr": "त्यात माती अडकली आहे का, किंवा खूप रक्त वाहत आहे का?"},
    "repeat_yes_no": {"en": "Sorry, just to be sure, is that a yes or a no?",
                      "hi": "माफ़ कीजिए, बस पक्का करने के लिए, हाँ या नहीं?",
                      "mr": "माफ करा, फक्त खात्री करण्यासाठी, हो की नाही?"},
    "repeat_other": {"en": "What else are you feeling?",
                     "hi": "और क्या महसूस हो रहा है?",
                     "mr": "आणखी काय जाणवत आहे?"},
}

T = {   # everything else
    "empathy": {"en": ["I'm sorry you're not feeling well.", "Oh, I'm sorry to hear that.", "That sounds uncomfortable."],
                "hi": ["आपकी तबीयत ठीक नहीं है, यह जानकर दुख हुआ।", "ओह, यह सुनकर बुरा लगा।", "यह तकलीफ़देह लग रहा है।"],
                "mr": ["तुमची तब्येत बरी नाही, हे ऐकून वाईट वाटले.", "अरेरे, हे ऐकून वाईट वाटले.", "हे त्रासदायक वाटत आहे."]},
    "help_now": {"en": "Here's what can help right now.", "hi": "अभी यह मदद कर सकता है।", "mr": "आत्ता हे मदत करू शकते."},
    "few_questions": {"en": "A few quick questions, to be sure it's nothing more serious.",
                      "hi": "कुछ छोटे सवाल, ताकि पक्का हो सके कि यह कुछ गंभीर नहीं है।",
                      "mr": "काही छोटे प्रश्न, म्हणजे हे गंभीर नाही याची खात्री होईल."},
    "ack": {"en": ["Okay.", "Alright.", "Got it.", "I see."],
            "hi": ["ठीक है।", "समझ गई।", "जी, ठीक है।", "अच्छा।"],
            "mr": ["ठीक आहे.", "समजले.", "बरं.", "अच्छा."]},
    "help_if": {"en": "Please get medical help sooner if", "hi": "अगर ऐसा हो तो जल्दी चिकित्सा सहायता लें:", "mr": "असे झाल्यास लवकर वैद्यकीय मदत घ्या:"},
    "self_care": {"en": "From what you've told me, this sounds like something you can look after at home for now. Keep going with the advice I gave you.",
                  "hi": "आपने जो बताया, उससे लगता है कि अभी आप इसे घर पर संभाल सकते हैं। मैंने जो सलाह दी, उसे जारी रखिए।",
                  "mr": "तुम्ही जे सांगितले त्यावरून असे वाटते की सध्या तुम्ही हे घरी सांभाळू शकता. मी सांगितलेला सल्ला चालू ठेवा."},
    "days_rule": {"en": "If it isn't getting better within {n} days, please see a doctor.",
                  "hi": "अगर {n} दिन में आराम न मिले, तो कृपया डॉक्टर को दिखाइए।",
                  "mr": "{n} दिवसांत आराम न पडल्यास, कृपया डॉक्टरांना दाखवा."},
    "see_soon": {"en": "Because of what you've told me, I'd suggest seeing a doctor in the next day or so, rather than just waiting it out. Keep doing what we talked about until then.",
                 "hi": "आपने जो बताया, उसे देखते हुए मैं सुझाव दूँगी कि अगले एक-दो दिन में डॉक्टर को दिखाइए, सिर्फ़ इंतज़ार मत कीजिए। तब तक जो हमने बताया वह करते रहिए।",
                 "mr": "तुम्ही जे सांगितले त्यावरून मी सुचवते की येत्या एक-दोन दिवसांत डॉक्टरांना दाखवा, फक्त वाट पाहू नका. तोपर्यंत आपण जे बोललो ते करत रहा."},
    "urgent": {"en": "Because of what you've told me, I'd like you to be checked by a doctor today, please don't wait. If it gets worse, or you feel faint, have chest pain, or trouble breathing, call one zero eight straight away.",
               "hi": "आपने जो बताया, उसे देखते हुए मैं चाहती हूँ कि आज ही डॉक्टर आपको देखें, कृपया इंतज़ार मत कीजिए। अगर हालत बिगड़े, चक्कर आए, सीने में दर्द हो, या साँस लेने में तकलीफ़ हो, तो तुरंत एक शून्य आठ पर कॉल कीजिए।",
               "mr": "तुम्ही जे सांगितले त्यावरून मला वाटते की आजच डॉक्टरांनी तुम्हाला तपासावे, कृपया थांबू नका. परिस्थिती बिघडल्यास, चक्कर आल्यास, छातीत दुखल्यास किंवा श्वास घेण्यास त्रास झाल्यास लगेच एक शून्य आठ वर कॉल करा."},
    "unsure": {"en": "I wasn't fully sure about one of your answers, so to be safe I'd suggest seeing a doctor soon.",
               "hi": "आपके एक जवाब के बारे में मैं पूरी तरह पक्की नहीं हो पाई, इसलिए सावधानी के तौर पर जल्दी डॉक्टर को दिखाने का सुझाव दूँगी।",
               "mr": "तुमच्या एका उत्तराबद्दल मला पूर्ण खात्री झाली नाही, म्हणून काळजी म्हणून लवकर डॉक्टरांना दाखवण्याचा सल्ला देईन."},
    "cant_diagnose": {"en": "I can't diagnose over the phone, so a doctor should confirm.",
                      "hi": "मैं फ़ोन पर कोई निदान नहीं कर सकती, इसलिए पुष्टि के लिए डॉक्टर सबसे अच्छे हैं।",
                      "mr": "मी फोनवर निदान करू शकत नाही, म्हणून खात्रीसाठी डॉक्टर सर्वोत्तम आहेत."},
    "also_offer": {"en": "You also mentioned {x}. Shall we go through that too?",
                   "hi": "आपने {x} का भी ज़िक्र किया था। क्या हम उसके बारे में भी बात करें?",
                   "mr": "तुम्ही {x} चाही उल्लेख केला होता. आपण त्याबद्दलही बोलूया का?"},
    "anything_else": {"en": "Is there anything else I can help you with?",
                      "hi": "क्या मैं आपकी और कुछ मदद कर सकती हूँ?",
                      "mr": "मी तुमची आणखी काही मदत करू शकते का?"},
}
LABEL_L = {   # topic label in each language (for "you also mentioned ...")
    "en": LABEL,
    "hi": {"mild_fever": "बुखार", "mild_headache": "सिरदर्द", "cold_congestion": "सर्दी", "sore_throat": "गले की खराश",
           "mild_cough": "खाँसी", "mild_indigestion": "एसिडिटी", "mild_dehydration": "पानी की कमी", "minor_sprain": "मोच",
           "minor_burn": "जलन", "minor_cut": "कटने", "minor_abrasion": "खरोंच"},
    "mr": {"mild_fever": "ताप", "mild_headache": "डोकेदुखी", "cold_congestion": "सर्दी", "sore_throat": "घसा दुखणे",
           "mild_cough": "खोकला", "mild_indigestion": "ॲसिडिटी", "mild_dehydration": "पाण्याची कमतरता", "minor_sprain": "मुरगळणे",
           "minor_burn": "भाजणे", "minor_cut": "कापणे", "minor_abrasion": "खरचटणे"},
}


def _end(text: str, lang: str) -> str:
    """Make sure a spoken sentence ends with punctuation (care text comes without a full stop)."""
    t = text.strip()
    return t if t[-1:] in ".?!।" else t + ("।" if lang == "hi" else ".")


def _pick(key: str, lang: str, i: int) -> str:
    opts = T[key].get(lang) or T[key]["en"]
    return opts[i % len(opts)]


# ---------------------------------------------------------------------------------------------------------------
# 4. the planner
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class FUStep:
    kind: str                       # passthrough | ask | conclude | start_next
    text: str = ""
    care_id: Optional[str] = None
    level: Optional[str] = None     # self_care | see_soon | urgent   (on conclude)
    key: Optional[str] = None       # question key (on ask)


@dataclass
class _State:
    care_id: str
    asked: list = field(default_factory=list)          # question keys already asked
    pending: Optional[str] = None                      # question awaiting an answer
    repeats: int = 0
    severity: Optional[str] = None
    duration_days: Optional[float] = None
    flag_yes: bool = False
    flag_unsure: bool = False
    other_note: bool = False
    tried: bool = False


class FollowUp:
    MIN_QUESTIONS = 3
    MAX_QUESTIONS = 4

    def __init__(self) -> None:
        self.state: Optional[_State] = None
        self.done: set = set()
        self.queue: list = []
        self._offered: Optional[str] = None
        self._n = 0                                     # rotates the acknowledgements

    @property
    def active(self) -> bool:
        return self.state is not None or self._offered is not None

    def reset(self) -> None:
        self.__init__()

    # ---- starting ---------------------------------------------------------------------------------------------
    def new_topics(self, text: str) -> list:
        return [c for c in detect_topics(text) if c not in self.done]

    def start(self, care_id: str, advice: str, text: str, lang: str = "en", others: Optional[list] = None) -> FUStep:
        """`advice` = the already-rendered, safety-gated first-aid / home-remedy / OTC line for this topic in `lang`."""
        st = _State(care_id)
        st.severity = parse_severity(text)
        st.duration_days = parse_duration_days(text)
        self.state = st
        self.queue = [c for c in (others or []) if c != care_id and c not in self.done]
        self._n += 1
        q = self._next_question(lang)
        parts = [_pick("empathy", lang, self._n), T["help_now"].get(lang, T["help_now"]["en"]), advice,
                 T["few_questions"].get(lang, T["few_questions"]["en"])]
        return FUStep("ask", " ".join(p.strip() for p in parts if p) + " " + q.text, care_id, key=q.key)

    # ---- one caller turn while active -------------------------------------------------------------------------
    def process(self, text: str, lang: str = "en", care_help: str = "") -> FUStep:
        if self._offered is not None:                                    # "shall we go through the headache too?"
            cid, self._offered = self._offered, None
            t = (text or "").strip()
            if _NO.match(t) or (not _YES.search(t) and not t):
                self.queue = []
                return FUStep("conclude", T["anything_else"][lang], level="self_care")
            return FUStep("start_next", care_id=cid)
        st = self.state
        if st is None:
            return FUStep("passthrough")
        t = (text or "").strip()
        key = st.pending
        if key is not None:
            if not self._absorb(st, key, t, lang):
                st.repeats += 1
                if st.repeats <= 1:                                       # one gentle re-ask, then move on
                    again = Q["repeat_other" if key == "other" else "repeat_yes_no"][lang]
                    return FUStep("ask", again, st.care_id, key=key)
                if key.startswith("flags_"):
                    st.flag_unsure = True
                st.repeats = 0
            else:
                st.repeats = 0
            st.asked.append(key)
            st.pending = None
        q = self._next_question(lang)
        self._n += 1
        if q is not None:
            return FUStep("ask", _pick("ack", lang, self._n) + " " + q.text, st.care_id, key=q.key)
        return self._conclude(st, lang, care_help)

    # ---- internals ---------------------------------------------------------------------------------------------
    @dataclass
    class _Q:
        key: str
        text: str

    def _next_question(self, lang: str):
        st = self.state
        asked = len(st.asked)
        order = ["flags_" + st.care_id]
        if st.severity is None:
            order.append("severity")
        if st.duration_days is None:
            order.append("duration")
        order += ["other", "tried"]
        for key in order:
            if key in st.asked:
                continue
            if asked >= self.MAX_QUESTIONS:
                return None
            if st.flag_yes and asked >= 1:                                # a red flag: stop asking, get them help
                return None
            st.pending = key
            return self._Q(key, Q[key][lang])
        return None

    def _absorb(self, st: _State, key: str, t: str, lang: str) -> bool:
        """Return True if the answer was understood."""
        low = t.lower()
        if key.startswith("flags_"):
            words = _FLAG_WORDS.get(st.care_id, "")
            neg = bool(_NO.match(low))
            if neg:
                return True
            if _YES.search(low) or (words and re.search(words, low)):
                st.flag_yes = True
                return True
            if _DONT_KNOW.search(low):
                st.flag_unsure = True
                return True
            return False
        if key == "severity":
            sev = parse_severity(low)
            if sev:
                st.severity = sev
                return True
            return True                                                   # unknown severity is not worth a repeat
        if key == "duration":
            d = parse_duration_days(low)
            if d is not None:
                st.duration_days = d
            return True
        if key == "other":
            if _NO.match(low) or not low:
                return True
            if re.fullmatch(r"\W*(yes|yeah|yep|i do|a few)\W*", low):
                return False                                              # "yes" alone: ask what else
            st.other_note = True
            serious = r"trouble breathing|hard to breathe|short of breath|faint\w*|chest pain|confus\w*|blood"
            if re.search(serious, low) and not re.search(r"\b(no|not|without)\b[^.]{0,20}(breath|faint|chest|confus|blood)", low):
                st.flag_yes = True
            return True
        if key == "tried":
            st.tried = not bool(_NO.match(low))
            return True
        return True

    def _conclude(self, st: _State, lang: str, care_help: str) -> FUStep:
        self.done.add(st.care_id)
        days = SEE_AFTER_DAYS.get(st.care_id, 3)
        if st.flag_yes:
            level, body = "urgent", T["urgent"][lang]
        elif st.flag_unsure:
            level, body = "see_soon", T["unsure"][lang]
        elif st.severity == "severe" or (st.duration_days is not None and st.duration_days > days):
            level, body = "see_soon", T["see_soon"][lang]
        else:
            level, body = "self_care", T["self_care"][lang]
        parts = [_pick("ack", lang, self._n), body]
        if level != "urgent":
            if care_help:
                parts.append(_end(f"{T['help_if'][lang]} {care_help}", lang))
            if level == "self_care":
                parts.append(T["days_rule"][lang].format(n=days))
        parts.append(T["cant_diagnose"][lang])
        self.state = None
        nxt = next((c for c in self.queue if c not in self.done), None)
        if nxt and level != "urgent":
            self._offered = nxt
            self.queue = [c for c in self.queue if c != nxt]
            parts.append(T["also_offer"][lang].format(x=LABEL_L[lang][nxt]))
        else:
            self.queue = []
            parts.append(T["anything_else"][lang])
        return FUStep("conclude", " ".join(p.strip() for p in parts if p), st.care_id, level=level)


# ---------------------------------------------------------------------------------------------------------------
# 5. the sentences that can be pre-rendered as audio (so replies start instantly on a phone call)
# ---------------------------------------------------------------------------------------------------------------

def fixed_texts(lang: str) -> list:
    out: list = []
    for v in Q.values():
        out.append(v[lang])
    for k, v in T.items():
        x = v.get(lang)
        if isinstance(x, list):
            out += x
        elif x:
            out.append(x.replace("{n}", "3").replace("{x}", LABEL_L[lang]["mild_fever"]))
    for n in (1, 2, 3, 7, 10):
        out.append(T["days_rule"][lang].format(n=n))
    for cid in LABEL:
        out.append(T["also_offer"][lang].format(x=LABEL_L[lang][cid]))
    return out
