# ============================================================
# VOXERA — CONTROLLED HOME-CARE + OTC KNOWLEDGE LAYER
# ============================================================
#
# Runs AFTER the deterministic emergency detector (voxera_emergency).
# The emergency detector is FROZEN and always wins: this module is only
# ever reached for utterances it did NOT flag.
#
# The LLM is NOT a source of medical knowledge here. It is only allowed to
# re-word APPROVED, curated guidance into natural spoken English. Anything it
# adds that looks like an invented medicine, dose, or instruction is rejected
# and the curated text is spoken verbatim instead.
#
#   lookup_care(text, context="")            -> CareGuidance | None
#   build_profile(facts)                     -> dict
#   suggest_otc(care_id, profile)            -> OtcSuggestion | None
#   render(care, otc, patient_text, llm)     -> str   (safe spoken response)
# ============================================================

import re
from dataclasses import dataclass, field


def _rx(p):
    return re.compile(p, re.IGNORECASE)


# ============================================================
# 1. CURATED HOME-CARE / FIRST-AID KNOWLEDGE
# ============================================================

@dataclass
class CareGuidance:
    care_id: str
    label: str
    steps: list              # short curated home-care steps
    see_help_if: list        # plain escalation cues (advice text, NOT detection)
    otc_keys: list = field(default_factory=list)
    ask_profile: list = field(default_factory=list)   # useful info to have


_CARE = {
    "minor_burn": CareGuidance(
        "minor_burn", "a minor burn",
        ["Hold it under cool running water for about twenty minutes, and take "
         "off any rings or tight items nearby before it swells.",
         "Once it's cooled, cover it loosely with cling film or a clean, "
         "non-fluffy dressing.",
         "Don't put butter, toothpaste, or ice on it, and don't burst blisters."],
        ["the burn is bigger than your palm, or it's on the face, hands, feet, "
         "or genitals",
         "it looks deep or white or leathery, or the pain is severe and not "
         "settling"],
        ["paracetamol"]),

    "minor_cut": CareGuidance(
        "minor_cut", "a minor cut with some bleeding",
        ["Press a clean cloth or dressing firmly on it for about ten minutes "
         "without lifting to check, and raise the area if you can.",
         "Once the bleeding has stopped, rinse it under clean running water, "
         "pat it dry, and cover it with a clean plaster or dressing.",
         "Change the dressing daily and keep it clean and dry."],
        ["the person feels faint, very pale, or unwell, or a child was hit by a "
         "vehicle, fell hard, or hit their head - get them seen even if it "
         "looks minor",
         "the bleeding soaks through or doesn't stop after ten minutes of firm "
         "pressure, or it's spurting",
         "the cut is deep or gaping, there's something stuck in it, or it was a "
         "dirty wound or animal bite"],
        ["paracetamol"],
        ["age"]),

    "minor_abrasion": CareGuidance(
        "minor_abrasion", "a graze or scrape from a fall",
        ["Rinse it gently under clean running water to float out any grit or "
         "dirt.",
         "If it's bleeding, press a clean cloth on it firmly for a few minutes.",
         "Once it's clean and the bleeding has stopped, pat it dry and cover it "
         "with a clean non-stick dressing.",
         "Leave any large stuck-in grit for a nurse rather than digging at it."],
        ["a child was hit by a vehicle, hit their head or tummy, fell hard, or "
         "seems drowsy, very pale, confused, or won't settle - get them checked "
         "urgently even if the scrape looks minor",
         "the bleeding won't stop after ten minutes of firm pressure, or the "
         "wound is deep, gaping, or over a joint",
         "there's grit or debris you can't rinse out, or it was a dirty road "
         "surface"],
        ["paracetamol"],
        ["age"]),

    "mild_fever": CareGuidance(
        "mild_fever", "a mild fever",
        ["Rest and drink plenty of fluids, small sips often.",
         "Keep the room comfortable, not too hot, and wear light clothing.",
         "Check your temperature again in a few hours."],
        ["the fever goes above 39.5 degrees, or lasts more than three days",
         "you get a stiff neck, a rash that doesn't fade when pressed, confusion, "
         "trouble breathing, or you can't keep fluids down"],
        ["paracetamol", "ibuprofen"],
        ["age", "pregnancy"]),

    "cold_congestion": CareGuidance(
        "cold_congestion", "a cold and a blocked nose",
        ["Rest, keep your fluids up, and give it a few days.",
         "Steam from a hot shower or a bowl of hot water can loosen the "
         "congestion.",
         "A saline nasal spray or rinse can help clear the blockage."],
        ["it lasts more than about ten days or keeps getting worse",
         "you get a high fever, face or sinus pain, or trouble breathing"],
        ["saline_nasal", "paracetamol"]),

    "sore_throat": CareGuidance(
        "sore_throat", "a sore throat",
        ["Sip warm drinks, and try gargling with warm salty water.",
         "Suck on a lozenge or a bit of hard sweet to keep the throat moist.",
         "Rest your voice and keep your fluids up."],
        ["it's severe or lasts more than a week",
         "you can't swallow your own saliva, you're drooling, or your breathing "
         "or voice changes suddenly",
         "you get a high fever with white patches on the tonsils"],
        ["throat_lozenge", "paracetamol"]),

    "mild_headache": CareGuidance(
        "mild_headache", "a mild headache",
        ["Have some water, since a headache is often just dehydration or tension.",
         "Rest somewhere quiet and dim for a bit, and ease off screens.",
         "A little fresh air or a short walk can help."],
        ["it's the worst headache you've ever had or it came on like a "
         "thunderclap",
         "it comes with fever and a stiff neck, weakness, confusion, vision "
         "changes, or it follows a head injury"],
        ["paracetamol", "ibuprofen"]),

    "mild_dehydration": CareGuidance(
        "mild_dehydration", "mild dehydration",
        ["Sip fluids steadily rather than gulping a lot at once.",
         "An oral rehydration solution replaces salts as well as water.",
         "Ease back on caffeine and alcohol until you've caught up."],
        ["you can't keep any fluids down, you've stopped passing urine, or you "
         "feel dizzy, drowsy, or confused",
         "it's a baby, a young child, or an older adult who isn't improving quickly"],
        ["ors"],
        ["age"]),

    "minor_sprain": CareGuidance(
        "minor_sprain", "a minor sprain",
        ["Rest it and avoid putting weight through it for the first day or so.",
         "Put an ice pack wrapped in a towel on it for about twenty minutes at "
         "a time.",
         "A snug elastic bandage and keeping it raised help with the swelling."],
        ["you can't put any weight on it or move it at all",
         "it's very swollen or badly bruised, looks out of shape, or is numb or "
         "cold",
         "it's not improving after a few days"],
        ["paracetamol", "ibuprofen"]),

    "mild_cough": CareGuidance(
        "mild_cough", "a mild cough",
        ["Warm drinks, and honey in warm water, can soothe the throat.",
         "Keep your fluids up and try to rest.",
         "Sitting propped up a bit can make night-time coughing easier."],
        ["it lasts more than three weeks, or you're coughing up blood",
         "you get short of breath, chest pain, a high fever, or you're wheezing "
         "and struggling"],
        ["throat_lozenge", "honey"]),

    "mild_indigestion": CareGuidance(
        "mild_indigestion", "mild indigestion or heartburn",
        ["Try smaller meals, and avoid lying down for a couple of hours after "
         "eating.",
         "Cut back on rich, spicy, or fatty food, coffee, and alcohol for a "
         "few days.",
         "Propping the head of the bed up a little can help night-time symptoms."],
        ["you get pain spreading to the arm, jaw, or back, or with sweating or "
         "breathlessness",
         "you have trouble or pain swallowing, you're vomiting, losing weight, "
         "or passing black stools",
         "it keeps coming back over more than a couple of weeks"],
        ["antacid"]),
}


# Order matters: earlier patterns win when several could match.
_CARE_MATCH = [
    ("minor_burn", _rx(r"\b(burn(?:t|ed)?|scald(?:ed)?)\b(?!.*\b(chemical|electrical)\b)")),

    # graze / road-rash / skin scraped after a fall (incl. off a bike)
    ("minor_abrasion", _rx(
        r"\broad ?rash\b"
        r"|\bgraze[d]?\b"
        r"|\bscrape[d]?\b"
        r"|\bskin (?:is |got |has )?(?:scraped|grazed|peel\w*|raw|come off|rubbed off|torn)\b"
        r"|\b(?:fell|come|came|crashed|knocked)\s+(?:down\s+|over\s+|off\s+)?"
        r"(?:from|off)?\s*(?:his|her|my|the|their)?\s*(?:bike|scooter|skateboard|"
        r"skates|swing|slide|steps|stairs|ladder)\b"
        r"|\bfell (?:down |over |off )\b"
        r"|\bfell (?:off|from) (?:his|her|my|the|their) bike\b")),

    # minor cut / small wound / minor (not severe) bleeding
    ("minor_cut", _rx(
        r"\b(cut|nick(?:ed)?|sliced?)\b[^.\n]{0,25}\b(finger|hand|arm|leg|knee|"
        r"foot|toe|myself|himself|herself|skin|lip)\b"
        r"|\b(small|minor|little)\s+(?:cut|wound|gash)\b"
        r"|\bsmall (?:open )?wound\b"
        r"|\b(?:he|she|it|they|my (?:son|daughter|child|kid|arm|hand|leg|knee|finger))"
        r"\s+(?:is |are |was |'?s )?bleeding\b"
        r"|\bbleeding (?:a (?:bit|little)|from (?:a|the|his|her)|now)\b"
        r"|\b(?:a )?(?:bit of|little|light|minor) bleeding\b")),

    ("mild_dehydration", _rx(r"\bdehydrat\w+\b|\bnot drinking enough\b|"
                             r"\bhaven'?t had (?:any|much) water\b")),
    ("mild_indigestion", _rx(r"\b(indigestion|heartburn|acid reflux|acidity|"
                             r"burning after (?:eating|meals)|stomach feels acidic)\b")),
    ("minor_sprain", _rx(r"\b(sprain\w*|twist(?:ed)? my (?:ankle|wrist|knee)|"
                         r"rolled my ankle|pulled a muscle)\b")),
    ("sore_throat", _rx(r"\bsore throat\b|\bthroat (?:is|feels) (?:sore|scratchy|raw)\b"
                        r"|\bscratchy throat\b|\bthroat hurts\b")),
    ("cold_congestion", _rx(r"\b(blocked|stuffy|congested|runny|bunged up) nose\b"
                            r"|\bnasal congestion\b|\bhead cold\b|\bcommon cold\b"
                            r"|\bi have a cold\b|\bcaught a cold\b")),
    ("mild_cough", _rx(r"\b(mild|slight|little|dry|tickly) cough\b"
                       r"|\bi have a cough\b|\bbeen coughing\b")),
    ("mild_headache", _rx(r"\b(mild|slight|little|dull|tension) headache\b"
                          r"|\bmy head (?:hurts|aches)\b|\bi have a headache\b"
                          r"|\bheadachey\b")),
    ("mild_fever", _rx(r"\b(mild|slight|low|low-grade|little) fever\b"
                       r"|\bi have a (?:bit of a )?fever\b|\bfeeling feverish\b"
                       r"|\btemperature is (?:up|a bit high)\b|\brunning a fever\b")),
]


def lookup_care(text, context=""):
    """First curated home-care topic that matches the current utterance
    (or, failing that, the recent context). Returns None if nothing matches."""
    blob = (text or "")
    for care_id, rx in _CARE_MATCH:
        if rx.search(blob):
            return _CARE[care_id]
    ctx = context or ""
    for care_id, rx in _CARE_MATCH:
        if rx.search(ctx):
            return _CARE[care_id]
    return None


# ============================================================
# 2. CURATED OTC MEDICATION KNOWLEDGE  (no numeric doses, ever)
# ============================================================

# Adult guidance is deliberately non-numeric: "as directed on the packet".
OTC_ITEMS = {
    "paracetamol": {
        "spoken_name": "paracetamol, also called acetaminophen",
        "for": {"mild_fever", "mild_headache", "sore_throat", "minor_sprain",
                "minor_burn", "minor_cut", "minor_abrasion", "cold_congestion"},
        "adult": "you can take paracetamol as directed on the packet for the "
                 "pain or fever",
        "caveat": "just don't take it more often than the label says, and don't "
                  "double up with other products that also contain paracetamol",
        "avoid_if": {"liver disease", "liver problem", "liver problems",
                     "hepatitis", "cirrhosis"},
        "already_taking": {"paracetamol", "acetaminophen", "co-codamol",
                           "panadol", "calpol", "tylenol"},
        "pregnancy": "ok",
        "child": "defer",
    },
    "ibuprofen": {
        "spoken_name": "ibuprofen",
        "for": {"mild_headache", "minor_sprain", "sore_throat", "mild_fever"},
        "adult": "an anti-inflammatory like ibuprofen, taken with food and as "
                 "directed on the packet, can help",
        "caveat": "skip it if you have a stomach ulcer, kidney problems, or "
                  "asthma that flares with painkillers, or if you're on blood "
                  "thinners",
        "avoid_if": {"stomach ulcer", "peptic ulcer", "kidney disease",
                     "kidney problem", "kidney problems", "asthma",
                     "heart failure", "blood thinner", "blood thinners",
                     "warfarin", "on anticoagulants"},
        "already_taking": {"ibuprofen", "advil", "nurofen", "naproxen",
                           "diclofenac", "aspirin"},
        "pregnancy": "avoid",
        "child": "defer",
    },
    "ors": {
        "spoken_name": "an oral rehydration solution",
        "for": {"mild_dehydration"},
        "adult": "sip an oral rehydration solution, made up exactly as the "
                 "sachet instructions say",
        "caveat": "keep the sips small and frequent rather than a lot at once",
        "avoid_if": set(),
        "already_taking": set(),
        "pregnancy": "ok",
        "child": "ok_general",
        "child_note": "for a child, small frequent sips of an oral rehydration "
                      "solution are fine, but check with a pharmacist if they "
                      "aren't improving",
    },
    "antacid": {
        "spoken_name": "a simple over-the-counter antacid",
        "for": {"mild_indigestion"},
        "adult": "a simple antacid from the pharmacy, taken as directed on the "
                 "packet, can settle it",
        "caveat": "if you need it most days, or it's not helping, see a "
                  "pharmacist or your doctor",
        "avoid_if": {"kidney disease", "kidney problem", "kidney problems"},
        "already_taking": {"antacid", "gaviscon", "rennie", "omeprazole",
                           "ranitidine", "pantoprazole"},
        "pregnancy": "ok",
        "child": "defer",
    },
    "throat_lozenge": {
        "spoken_name": "a throat lozenge",
        "for": {"sore_throat", "mild_cough"},
        "adult": "a throat lozenge or a menthol sweet can keep the throat "
                 "comfortable",
        "caveat": "",
        "avoid_if": set(),
        "already_taking": set(),
        "pregnancy": "ok",
        "child": "defer",
    },
    "saline_nasal": {
        "spoken_name": "a saline nasal spray or rinse",
        "for": {"cold_congestion"},
        "adult": "a saline nasal spray or rinse, used as directed, can clear "
                 "the blockage without any medicine",
        "caveat": "",
        "avoid_if": set(),
        "already_taking": set(),
        "pregnancy": "ok",
        "child": "ok_general",
        "child_note": "saline drops are also fine for children",
    },
    "honey": {
        "spoken_name": "warm honey and water",
        "for": {"mild_cough"},
        "adult": "honey in warm water can soothe a cough",
        "caveat": "",
        "avoid_if": set(),
        "already_taking": set(),
        "pregnancy": "ok",
        "child": "no_infant",
        "child_note": "honey is fine for children over one year old, but never "
                      "give honey to a baby under one",
    },
}


@dataclass
class OtcSuggestion:
    spoken: str
    items: list
    deferred: bool = False


# ============================================================
# 3. PATIENT SAFETY PROFILE  (from the deterministic fact extractor)
# ============================================================

def build_profile(facts):
    facts = facts or {}
    return {
        "age": facts.get("age"),
        "is_child": bool(facts.get("is_child")),
        "pregnant": bool(facts.get("pregnant")),
        "allergies": [a.lower() for a in facts.get("allergies", [])],
        "current_meds": [m.lower() for m in facts.get("medications", [])],
        "conditions": [c.lower() for c in facts.get("conditions", [])],
    }


def _blocked_for_profile(item, profile):
    """Return a reason string if this OTC item is unsafe / unsuitable for the
    profile, else None."""
    name = item["spoken_name"].split(",")[0].lower()

    # allergy
    for a in profile["allergies"]:
        if a and (a in name or name in a):
            return f"you mentioned an allergy to {a}"

    # already taking it (or same class)
    for m in profile["current_meds"]:
        if m in item["already_taking"] or any(m in x or x in m
                                              for x in item["already_taking"]):
            return "you're already taking something like that"

    # medical conditions
    for c in profile["conditions"]:
        for bad in item["avoid_if"]:
            if bad in c or c in bad:
                return f"of your {c}"

    # pregnancy
    if profile["pregnant"] and item["pregnancy"] == "avoid":
        return "you're pregnant"

    # children
    if profile["is_child"] or (profile["age"] is not None and profile["age"] < 12):
        if item["child"] == "defer":
            return "child_defer"
        if item["child"] == "no_infant" and (profile["age"] or 99) < 1:
            return "child_infant"

    return None


def suggest_otc(care_id, profile):
    """Return a single, safety-gated OTC suggestion for a care topic, or a
    'check with a pharmacist' deferral, or None."""
    care = _CARE.get(care_id)
    if not care or not care.otc_keys:
        return None

    picked = None
    deferred_child = False
    for key in care.otc_keys:
        item = OTC_ITEMS.get(key)
        if not item or care_id not in item["for"]:
            continue
        reason = _blocked_for_profile(item, profile)
        if reason is None:
            picked = (key, item, None)
            break
        if reason in ("child_defer",):
            deferred_child = True
        # otherwise: silently skip this item and try the next

    if picked:
        key, item, _ = picked
        parts = [item["adult"].strip().rstrip(".")]
        if item.get("caveat"):
            parts.append(item["caveat"].strip().rstrip("."))
        if profile["pregnant"] and item["pregnancy"] == "ok":
            parts.append("since you're pregnant, just confirm it with your "
                         "midwife or pharmacist first")
        # a child that this item explicitly allows
        if (profile["is_child"] or (profile["age"] or 99) < 12) \
                and item["child"] in ("ok_general", "no_infant"):
            parts = [item.get("child_note", parts[0]).strip().rstrip(".")]
        spoken = ". ".join(p[0].upper() + p[1:] for p in parts if p) + "."
        return OtcSuggestion(spoken=spoken, items=[key], deferred=False)

    if deferred_child:
        return OtcSuggestion(
            spoken="For a child, the right amount of medicine depends on their "
                   "age and weight, so please check with a pharmacist or your "
                   "doctor before giving anything.",
            items=[], deferred=True)

    return None


# ============================================================
# 4. RENDER  — LLM re-words APPROVED text; anything it invents is rejected
# ============================================================

# Common OTC / prescription names the model might hallucinate. If one of these
# shows up in the spoken reply and it isn't in the approved guidance, reject.
_DRUG_LEXICON = _rx(
    r"\b(aspirin|amoxicillin|azithromycin|penicillin|antibiotic?s?|augmentin|"
    r"co-?amoxiclav|prednisolone|prednisone|steroids?|codeine|co-?codamol|"
    r"tramadol|naproxen|diclofenac|dextromethorphan|pseudoephedrine|"
    r"phenylephrine|guaifenesin|loratadine|cetirizine|benadryl|diphenhydramine"
    r"|omeprazole|lansoprazole|ranitidine|pantoprazole|domperidone|"
    r"metoclopramide|loperamide|imodium|hydrocortisone|salbutamol|ventolin|"
    r"nurofen|advil|calpol|tylenol|panadol|disprin|combiflam|dolo|crocin)\b")

_DOSE_RX = _rx(r"\b\d+\s?(?:mg|milligram|mcg|microgram|ml|millilit|gram|g|cc|"
               r"tablet|tablets|pill|pills|capsule|capsules|teaspoon|teaspoons|"
               r"tsp|dose|doses|times a day|x a day|hourly)\b"
               r"|\bevery\s+\d+\s*(?:hour|hr|minute)")

_RX_FORBID_PHRASES = _rx(r"\b(prescription|prescribe|antibiotic|steroid|"
                         r"you should take \d)\b")

# Voxera must give the next step directly, not turn it back into a question for
# the caller. These meta-questions are rejected.
_RX_META_QUESTION = _rx(
    r"\b(should i (?:tell|recommend|advise|say|suggest|have|ask|let|book)"
    r"|shall i\b"
    r"|would you (?:like|want) me to"
    r"|do you want me to"
    r"|do you (?:want|need) (?:me )?to"
    r"|should i recommend"
    r"|is there anything (?:else )?(?:you|i)"
    r"|can i help you with anything else"
    r"|let me know if you (?:need|want)"
    r"|would you like (?:me |any )?(?:to |help )"
    r"|do you have any other)\b")


def _first_sentence(s):
    m = re.search(r"[^.!?]*[.!?]", s or "")
    return (m.group(0) if m else (s or "")).strip()


def _templated(care, otc):
    """Deterministic, always-safe, phone-length spoken fallback."""
    out = [care.steps[0]]
    if otc and otc.spoken:
        out.append(_first_sentence(otc.spoken))
    if care.see_help_if:
        out.append("Get medical help if " + care.see_help_if[0] + ".")
    return " ".join(x.strip() for x in out if x)


def _approved_terms(care, otc):
    terms = set()
    text = " ".join(care.steps + care.see_help_if).lower()
    if otc:
        text += " " + otc.spoken.lower()
    for m in _DRUG_LEXICON.finditer(text):
        terms.add(m.group(0).lower())
    # also allow the generic supportive words we do use
    for t in ("paracetamol", "acetaminophen", "ibuprofen", "antacid",
              "lozenge", "saline", "honey", "oral rehydration solution", "ors"):
        if t in text:
            terms.add(t)
    return terms, text


def validate_spoken(reply, care, otc, patient_text=""):
    """Return (ok, reason). Rejects:
      * invented medicines / doses / prescription talk
      * a reply that drops the approved OTC guidance
      * a reply that just parrots the patient's sentence back
    """
    if not reply or len(reply.split()) < 3:
        return False, "empty"
    if len(reply.split()) > 85:
        return False, "too long"
    low = reply.lower()
    approved_terms, approved_text = _approved_terms(care, otc)

    for m in _DRUG_LEXICON.finditer(low):
        term = m.group(0).lower()
        if term not in approved_terms:
            return False, f"invented medicine '{term}'"

    for m in _DOSE_RX.finditer(low):
        if m.group(0).lower() not in approved_text:
            return False, f"invented dose/frequency '{m.group(0)}'"

    if _RX_FORBID_PHRASES.search(low):
        for m in _RX_FORBID_PHRASES.finditer(low):
            if m.group(0).lower() not in approved_text:
                return False, f"forbidden phrase '{m.group(0)}'"

    # must give the step directly, not ask the caller a meta-question
    mq = _RX_META_QUESTION.search(low)
    if mq:
        return False, f"meta-question to the caller ('{mq.group(0).strip()}')"

    # must actually carry the approved OTC guidance
    if otc and not otc.deferred and otc.items:
        keyword = OTC_ITEMS[otc.items[0]]["spoken_name"].split(",")[0].split()[-1].lower()
        if keyword not in low:
            return False, f"dropped the OTC guidance ('{keyword}')"
    if otc and otc.deferred and "pharmacist" not in low and "doctor" not in low:
        return False, "dropped the 'check with a pharmacist/doctor' guidance"

    # must not just echo the patient
    if patient_text:
        head = " ".join(low.split()[:7])
        pt = re.sub(r"\s+", " ", patient_text.lower())
        if len(head) > 12 and head in pt:
            return False, "parroting the patient"

    return True, "ok"


_RENDER_SYSTEM = """You are Voxera on a phone call. You will be given APPROVED
care guidance as bullet points. Rewrite it as 2 to 4 short, warm spoken
sentences - the way a calm person would say it out loud.

HARD RULES:
- Include EVERY point from the guidance, including any medicine mentioned and
  any 'seek help if' point. Do not drop anything.
- Do NOT add any medicine, dose, number, brand, or instruction that is not in
  the guidance. Do NOT mention prescriptions, antibiotics, or steroids.
- Do NOT repeat or quote what the patient said. Start with the advice.
- Give the steps DIRECTLY as instructions. NEVER ask the caller a question like
  "should I tell them to see a doctor" or "would you like me to". State it:
  "Get medical care if ...".
- No lists, no headings. Just the spoken words."""


def render(care, otc, patient_text, llm=None):
    """Produce the safe spoken response for a care topic.

    llm: optional callable(system_prompt, history, user_text) -> str
         (pass voxera_core.llm_respond). If None or its output fails
         validation, the curated text is spoken verbatim.
    """
    approved = ["Home-care guidance for " + care.label + ":"]
    approved += [f"- {s}" for s in care.steps]
    if otc and otc.spoken:
        approved.append("- Over-the-counter: " + otc.spoken)
    if care.see_help_if:
        approved.append("- Tell them to seek medical help if "
                        + care.see_help_if[0] + ".")
    approved_block = "\n".join(approved)

    safe_fallback = _templated(care, otc)

    if llm is None:
        return safe_fallback

    try:
        reply = llm(_RENDER_SYSTEM, [], approved_block)
    except Exception:
        return safe_fallback

    ok, reason = validate_spoken(reply, care, otc, patient_text)
    if ok:
        return reply.strip()
    print(f"[CARE] LLM rendering rejected ({reason}); speaking curated guidance.")
    return safe_fallback


# ------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        ("I burned my hand on a pan.", {}),
        ("I have a mild fever.", {"age": 34}),
        ("I have a mild fever.", {"is_child": True}),
        ("I have a mild fever.", {"pregnant": True}),
        ("I have a headache.", {"conditions": ["stomach ulcer"]}),
        ("I've got a blocked nose and a sore throat and can't really speak.", {}),
        ("I think I'm a bit dehydrated.", {"is_child": True}),
        ("I have heartburn after meals.", {}),
    ]
    for text, facts in tests:
        care = lookup_care(text)
        if not care:
            print(f"\n{text!r} -> (no care topic)")
            continue
        prof = build_profile(facts)
        otc = suggest_otc(care.care_id, prof)
        print(f"\n{text!r}  facts={facts}")
        print(f"  topic: {care.care_id}")
        print(f"  otc:   {otc.spoken if otc else None}"
              + ("  [deferred]" if otc and otc.deferred else ""))
        print(f"  say:   {_templated(care, otc)}")
