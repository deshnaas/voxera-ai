"""Grounded question answering over ONE verified patient's records.

    question -> intent -> targeted retrieval -> deterministic grounded answer
                                              -> (optional) LLM rewording, validated

Guarantees
  * Never answers from model memory: every fact in the answer comes from a
    retrieved record and is listed in ``sources``.
  * Nothing found -> the fixed "couldn't find" line. Absence is never turned
    into "you don't have X".
  * Conflicting current instructions -> says so, does not pick one.
  * Old prescriptions are described as past ("was prescribed"), never as advice.
  * "Should I take it now?" -> fixed hedge, no medication decision.
  * The LLM (if enabled) may only reword: its output is rejected if it adds
    numbers, medicine-like words or advice phrases not present in the grounded
    answer.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Optional

from .models import RecordAnswer, Source

NOT_FOUND = "I couldn't find that information in your available records."
CONFLICT = ("The records I found contain different instructions. I don't want to guess "
            "which one is current. Please confirm with your doctor or pharmacist.")
HEDGE = ("I can tell you what your record says, but I can't confirm that it is appropriate for "
         "your current symptoms. Please confirm with your doctor or pharmacist.")
OLD_RX_NOW = ("This prescription is from your previous record. I can't confirm that it is "
              "appropriate for your current symptoms. Please confirm with your doctor or pharmacist.")
DB_DOWN = "I'm unable to access the patient record right now."

_SHOULD_I = re.compile(r"\b(should i|can i|may i|is it (ok|okay|safe|fine)|do i need to|must i)\b.*\b(take|use|have|start|continue|stop)\b", re.I)

ROUTE_WORDS = {
    "nebulization": r"nebuli[sz]|neb\b|nebuliser|nebulizer",
    "inhalation": r"inhaler|inhal|puffer|rotacap",
    "injection": r"injection|inject|shot|\binj\b",
    "topical": r"cream|ointment|gel|lotion|topical|apply",
    "eye/ear": r"eye drop|ear drop|drops",
    "oral": r"tablet|capsule|syrup|oral|swallow",
}

TOPIC_WORDS = ["cough", "fever", "cold", "headache", "pain", "chest", "stomach", "acidity", "gas", "diarrhea",
               "vomiting", "asthma", "breathing", "rash", "allergy", "sore throat", "diabetes", "bp", "blood pressure",
               "hypertension", "thyroid", "infection", "wheez"]
CONDITION_ALIASES = {
    "diabetes": ["diabet", "sugar"], "hypertension": ["hypertens", "high blood pressure", "bp"],
    "asthma": ["asthma", "wheez"], "thyroid": ["thyroid"], "heart": ["heart", "cardiac", "angina"],
    "kidney": ["kidney", "renal"], "epilepsy": ["epilep", "seizure"],
}

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]


def spoken_date(iso: Optional[str]) -> str:
    if not iso:
        return "an earlier date"
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00")[:19])
    except ValueError:
        return "an earlier date"
    return f"{d.day} {_MONTHS[d.month - 1]}"


# ------------------------------------------------------------------
# Intent classification (deterministic)
# ------------------------------------------------------------------

def classify_intent(q: str) -> dict:
    t = (q or "").lower().strip()
    out = {"intent": "not_record", "route": None, "topic": None, "should_i": bool(_SHOULD_I.search(t))}
    for route, rx in ROUTE_WORDS.items():
        if re.search(rx, t):
            out["route"] = route
            break
    for w in TOPIC_WORDS:
        if w in t:
            out["topic"] = w
            break
    if re.search(r"\b(allerg)", t) and re.search(r"\b(what|which|am i|do i)\b", t):
        out["intent"] = "allergies"
    elif re.search(r"\b(do|did|have|had|am i|was i)\b.*\b(diabet|hypertens|asthma|thyroid|heart|kidney|epilep|blood pressure|sugar)", t) \
            and not re.search(r"\b(prescri|medicin|tablet)\b", t):
        out["intent"] = "condition_check"
    elif re.search(r"\b(scan|scans|x-?rays?|mri|ultrasound|sonograph\w*|lab reports?|blood tests?|test results?|reports?)\b", t) \
            and re.search(r"\b(my|mine|did|has|have|was|were|ordered|result|results)\b", t):
        out["intent"] = "test_reports"                  # scans / reports: needs the record (none stored -> honest not-found)
    elif re.search(r"\b(refer|referred)\b", t):
        out["intent"] = "referral"
    elif re.search(r"\b(next|upcoming|when is my|any)\b.*\bappointment|\bappointment\b", t):
        out["intent"] = "appointment"
    elif re.search(r"\b(which|what)\b.*\b(hospital|clinic|facility)\b|\bwhere did i (go|visit)\b|\bhospital did i\b", t):
        out["intent"] = "facility"
    elif not out["route"] and re.search(r"\b(prescri\w*|medicine|medicines|medication|medications|tablet|tablets|drug|drugs)\b", t) \
            and re.search(r"\b(consultation|consultations|visit|checkup|appointment|call)\b", t):
        out["intent"] = "consultation_prescription"
    elif re.search(r"\b(when|what date)\b.*\b(last|previous|recent)\b.*\b(consult|visit|call|appointment|checkup)", t) \
            or re.search(r"\blast (consultation|visit|call)\b", t):
        out["intent"] = "last_consultation"
    elif re.search(r"\b(did i|have i)\b.*\b(this|that|it)\b.*\bbefore\b|\bhad this (problem|issue|symptom)", t) \
            or re.search(r"\bbefore\b.*\b(problem|issue|symptom)", t):
        out["intent"] = "had_before"
    elif re.search(r"\b(what did|what has|what was)\b.*\b(doctor|dr|hospital|voxera|you)\b.*\b(say|said|tell|told|advise|advice)", t) \
            or re.search(r"\b(doctor|dr)\b.*\b(said|say|told|advice|advise)\b", t):
        out["intent"] = "doctor_said"
    elif out["route"] and re.search(r"\b(medic|medicine|tablet|drug|prescri|use|take|taking|using|given|give|gave)\b", t):
        out["intent"] = "medication_by_route"          # a named route wins over "current"
    elif (re.search(r"\b(currently|current|right now|active)\b", t) and re.search(r"\b(medicat|medicine|tablet|drug)", t)) \
            or re.search(r"\bwhat medic\w+ (am i|do i) (on|take|taking)\b", t) \
            or re.search(r"\b(my|list)\b.*\b(medications?|medicines)\b", t):
        out["intent"] = "current_medications"
    elif re.search(r"\b(previous|last|old|earlier|past)\b.*\b(prescription)", t) or "prescription" in t:
        out["intent"] = "previous_prescription"
    elif re.search(r"\b(medicine|medication|tablet|drug|syrup)\b.*\b(for|used|use|took|take|prescribed|given|gave)\b", t) \
            or re.search(r"\b(what|which)\b.*\b(medicine|medication|tablet|drug)", t) \
            or re.search(r"\bprescribed\b", t):
        out["intent"] = "medication_for_topic" if out["topic"] else "last_medicine"
    return out


# ------------------------------------------------------------------
# Answer builders
# ------------------------------------------------------------------

def _src(kind: str, rec: dict, source: Optional[str] = None) -> dict:
    return {"type": kind, "id": rec.get("id"), "date": rec.get("date") or rec.get("created_at") or rec.get("prescribed_at"),
            "source": source or rec.get("source")}


def _route_phrase(m: dict) -> str:
    r = (m.get("route") or "").lower()
    return {"nebulization": "for nebulization", "oral": "by mouth", "inhalation": "as an inhaler",
            "topical": "for external use", "injection": "as an injection"}.get(r, f"({r})" if r else "")


def _describe_med(m: dict) -> str:
    name = m.get("medicine_name") or "an unnamed medicine"
    strength = m.get("strength")
    core = f"{name} {strength}".strip() if strength else name
    route = _route_phrase(m)
    route_s = f" {route}" if route else ""
    when = spoken_date(m.get("date"))
    src = m.get("source")
    freq = m.get("frequency")
    if src == Source.OCR.value:
        if m.get("status") == "rejected":
            return f"A prescription document listed {core}, but a clinician rejected that extraction."
        listed = f", listed as {freq}" if freq else ""
        return (f"A prescription document uploaded on {when} lists {core}{route_s}{listed}, "
                "but a clinician hasn't verified it yet.")
    if src == Source.PATIENT_REPORTED.value:
        return f"You told us earlier that you take {core}. A doctor hasn't verified that."
    freq_s = f" The record lists it as {freq}." if freq else ""
    tail = " It's marked as active in your record." if m.get("is_current") else f" That prescription is from {when}."
    return f"Your record shows that {core} was prescribed{route_s} on {when}.{freq_s}{tail}"


def _mframe(m: dict) -> dict:
    """The facts of one medicine, for wording in any language."""
    return {"name": m.get("medicine_name"), "strength": m.get("strength"), "route": m.get("route"),
            "freq": m.get("frequency"), "date": m.get("date"), "source": m.get("source"),
            "status": m.get("status"), "current": bool(m.get("is_current")), "rejected": m.get("status") == "rejected"}


def _topic(c: dict) -> str:
    """A short spoken topic for a consultation (never the caller's whole raw sentence)."""
    syms = [x for x in (c.get("symptoms") or []) if x]
    if syms:
        return ", ".join(syms[:3])
    raw = str(c.get("chief_complaint") or "").strip()
    raw = re.sub(r"^(so|well|um|uh|hi|hello|okay|ok|yeah)[\s,]+", "", raw, flags=re.I)
    first = re.split(r"[.,;!?]", raw, maxsplit=1)[0].strip()
    return (first[:70].rsplit(" ", 1)[0] + "…") if len(first) > 70 else first


def _rank(pool: list) -> list:
    """Clinician-authored first (newest first), then unverified (newest first)."""
    return sorted(pool, key=lambda m: (bool(m.get("is_clinician_confirmed")), m.get("date") or ""), reverse=True)


def _conflict(meds: list) -> bool:
    """Same medicine, different instructions, both clinician-authored & current."""
    by: dict = {}
    for m in meds:
        if m.get("is_clinician_confirmed") and m.get("status") == "active":
            by.setdefault((m.get("medicine_name") or "").lower(), set()).add(
                ((m.get("strength") or "").lower().replace(" ", ""), (m.get("frequency") or "").lower(), (m.get("route") or "").lower()))
    return any(len(v) > 1 for v in by.values())


def _matches_route(m: dict, route: str) -> bool:
    blob = " ".join(str(m.get(k) or "") for k in ("route", "form", "instructions", "medicine_name")).lower()
    return bool(re.search(ROUTE_WORDS[route], blob)) or (m.get("route") or "").lower() == route


def _topic_terms(topic: Optional[str]) -> list:
    if not topic:
        return []
    return [topic] + CONDITION_ALIASES.get(topic, [])


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def answer_patient_history_question(svc, patient_uuid: str, question: str, *, use_llm: bool = False,
                                    llm: Optional[Callable[[str, str], str]] = None,
                                    topic_hint: Optional[str] = None) -> RecordAnswer:
    info = classify_intent(question)
    intent = info["intent"]
    if intent == "not_record":
        return RecordAnswer("", "not_record", found=False)

    try:
        ctx = svc.build_clinical_context(patient_uuid)
        meds = svc.get_medications(patient_uuid)
    except Exception:                                # RepoError etc. -> never guess
        return RecordAnswer(DB_DOWN, intent, found=False, notes=["record_unavailable"], frame={"k": "dbdown"})

    print(f"[PATIENT_AI] record question intent={intent}")
    ans = _dispatch(svc, patient_uuid, ctx, meds, question, info, topic_hint)

    if info["should_i"] and ans.found and intent in ("medication_by_route", "last_medicine", "previous_prescription",
                                                      "medication_for_topic", "current_medications"):
        ans.answer = ans.answer.replace(HEDGE, "").strip() + " " + OLD_RX_NOW
        ans.notes.append("should_i_hedge")
        ans.frame = dict(ans.frame, old_rx=True, hedge=False)

    if use_llm and llm and ans.found and not ans.conflict:
        polished = _llm_polish(llm, ans.answer)
        if polished:
            ans.answer, ans.used_llm = polished, True
    return ans


def _dispatch(svc, pid, ctx, meds, question, info, topic_hint) -> RecordAnswer:
    intent = info["intent"]
    consults = ctx.previous_consultations

    # ---------- medications -------------------------------------------
    if intent in ("current_medications", "medication_by_route", "last_medicine", "previous_prescription", "medication_for_topic"):
        pool = [m for m in meds if m.get("status") != "rejected"]
        if intent == "current_medications":
            cur = [m for m in pool if m.get("is_current")]
            unv = [m for m in pool if not m.get("is_clinician_confirmed") and m["source"] in (Source.OCR.value, Source.PATIENT_REPORTED.value)]
            if not cur and not unv:
                return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})
            if _conflict(cur):
                return RecordAnswer(CONFLICT, intent, [_src("medication", m) for m in cur[:4]], 0.5, True, True, frame={"k": "conflict"})
            parts = []
            if cur:
                names = ", ".join(f"{m['medicine_name']}{(' ' + m['strength']) if m.get('strength') else ''}" for m in cur[:4])
                parts.append(f"Your record lists these as active: {names}.")
            if unv:
                parts.append("There are also " + str(len(unv)) + " item(s) that a clinician hasn't verified yet.")
            return RecordAnswer(" ".join(parts) + " " + HEDGE, intent, [_src("medication", m) for m in (cur + unv)[:5]],
                                0.9 if cur else 0.6, True, _conflict(cur),
                                frame={"k": "meds_current", "items": [[m["medicine_name"], m.get("strength")] for m in cur[:4]],
                                       "unverified": len(unv), "hedge": True})
        if intent == "medication_by_route":
            pool = [m for m in pool if _matches_route(m, info["route"])]
        elif intent == "medication_for_topic":
            terms = _topic_terms(info["topic"])
            pool2 = [m for m in pool if any(t in (str(m.get("instructions") or "") + str(m.get("medicine_name") or "")).lower() for t in terms)]
            if not pool2:
                # fall back: consultations about that topic that mention OTC guidance (NOT a prescription)
                for c in consults:
                    blob = (str(c.get("chief_complaint")) + " ".join(c.get("symptoms") or [])).lower()
                    if any(t in blob for t in terms) and c.get("otc_guidance"):
                        items = ", ".join(c["otc_guidance"][:3])
                        return RecordAnswer(
                            f"On {spoken_date(c['date'])}, Voxera's general over-the-counter guidance for your "
                            f"{info['topic']} mentioned {items}. That was general guidance, not a prescription from a doctor. "
                            "Please confirm with your doctor or pharmacist.",
                            intent, [{"type": "consultation", "id": c["id"], "date": c["date"], "source": Source.AI_SUMMARY.value}],
                            0.7, True, frame={"k": "otc", "date": c["date"], "topic": info["topic"], "items": list(c["otc_guidance"][:3])})
            pool = pool2
        if not pool:
            return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})
        pool = _rank(pool)
        if _conflict(pool):
            return RecordAnswer(CONFLICT, intent, [_src("medication", m) for m in pool[:4]], 0.5, True, True, frame={"k": "conflict"})
        top = pool[0] if intent != "previous_prescription" else next((m for m in pool if m["origin"] == "prescriptions"), pool[0])
        text = _describe_med(top)
        if len(pool) > 1 and intent in ("previous_prescription", "medication_by_route", "last_medicine"):
            others = ", ".join(sorted({m["medicine_name"] for m in pool[1:4] if m.get("medicine_name") and m["medicine_name"] != top.get("medicine_name")}))
            if others:
                text += f" Your record also lists {others}."
        conf = 0.9 if top.get("is_clinician_confirmed") else 0.65
        others_l = [m["medicine_name"] for m in pool[1:4] if m.get("medicine_name") and m["medicine_name"] != top.get("medicine_name")]
        return RecordAnswer(text + " " + HEDGE, intent, [_src("medication", m) for m in pool[:4]], conf, True,
                            frame={"k": "med", "m": _mframe(top), "others": sorted(set(others_l)) if intent in
                                   ("previous_prescription", "medication_by_route", "last_medicine") else [], "hedge": True})

    # ---------- consultations -----------------------------------------
    if intent == "consultation_prescription":
        clin = _rank([m for m in meds if m.get("is_clinician_confirmed") and m.get("status") != "rejected"])
        if clin:
            top = clin[0]
            return RecordAnswer(_describe_med(top) + " Prescriptions aren't linked to a specific consultation, so this is the most "
                                "recent one on record. " + HEDGE, intent, [_src("medication", top)], 0.7, True,
                                frame={"k": "med", "m": _mframe(top), "linked_note": True, "hedge": True})
        c = consults[0] if consults else None
        if c and c.get("otc_guidance"):
            return RecordAnswer(
                f"No prescription is recorded. On {spoken_date(c['date'])}, Voxera's general over-the-counter guidance mentioned "
                f"{', '.join(c['otc_guidance'][:3])}. That was general guidance, not a prescription from a doctor.",
                intent, [{"type": "consultation", "id": c["id"], "date": c["date"], "source": Source.AI_SUMMARY.value}], 0.6, True,
                frame={"k": "otc_none", "date": c["date"], "items": list(c["otc_guidance"][:3])})
        if consults:
            return RecordAnswer("No prescription is recorded for the last consultation.", intent, found=False, frame={"k": "no_rx"})
        return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})

    if intent == "last_consultation":
        if not consults:
            return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})
        c = consults[0]
        topic = _topic(c)
        what = f" It was about: {topic}." if topic else ""
        return RecordAnswer(f"Your last consultation on record was on {spoken_date(c['date'])}.{what}", intent,
                            [{"type": "consultation", "id": c["id"], "date": c["date"], "source": Source.AI_SUMMARY.value}], 0.9, True,
                            frame={"k": "last_consult", "date": c["date"], "topic": topic})

    if intent in ("doctor_said", "had_before"):
        topic = info["topic"] or topic_hint
        terms = _topic_terms(topic)
        if not terms:
            return RecordAnswer("Which symptom or problem do you mean? Then I can check your records.", intent, found=False,
                                notes=["needs_topic"], frame={"k": "needs_topic"})
        hits = [c for c in consults if any(t in (str(c.get("chief_complaint")) + " " + " ".join(c.get("symptoms") or []) + " " + str(c.get("assessment")) + " " + " ".join(c.get("guidance") or [])).lower() for t in terms)]
        if not hits:
            return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})
        c = hits[0]
        if intent == "had_before":
            return RecordAnswer(f"Your record shows you discussed {topic} before, on {spoken_date(c['date'])}.", intent,
                                [{"type": "consultation", "id": c["id"], "date": c["date"], "source": Source.AI_SUMMARY.value}], 0.8, True,
                                frame={"k": "had_before", "topic": topic, "date": c["date"]})
        bits = []
        if c.get("assessment"):
            bits.append(str(c["assessment"]))
        if c.get("guidance"):
            bits.append("Guidance given: " + ", ".join(c["guidance"][:3]) + ".")
        if not bits:
            return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})
        return RecordAnswer(f"On {spoken_date(c['date'])}, your record says: " + " ".join(bits), intent,
                            [{"type": "consultation", "id": c["id"], "date": c["date"], "source": Source.AI_SUMMARY.value}], 0.75, True,
                            frame={"k": "doctor_said", "date": c["date"]})

    if intent == "condition_check":
        t = question.lower()
        for cond, keys in CONDITION_ALIASES.items():
            if any(k in t for k in keys):
                in_rec = [x for x in ctx.conditions if any(k in x.lower() for k in keys)]
                if in_rec:
                    return RecordAnswer(f"Your record lists {in_rec[0]}. Please confirm the details with your doctor.", intent,
                                        [{"type": "patient", "id": pid, "date": None, "source": "record"}], 0.9, True,
                                        frame={"k": "cond", "name": in_rec[0]})
                for c in consults:
                    blob = (str(c.get("chief_complaint")) + str(c.get("assessment"))).lower()
                    if any(k in blob for k in keys):
                        return RecordAnswer(f"Your record mentions {cond} in a consultation on {spoken_date(c['date'])}.", intent,
                                            [{"type": "consultation", "id": c["id"], "date": c["date"], "source": Source.AI_SUMMARY.value}], 0.6, True,
                                            frame={"k": "cond_mention", "name": cond, "date": c["date"]})
        return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})

    if intent == "allergies":
        if not ctx.allergies:
            return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})
        return RecordAnswer("Your record lists these allergies: " + ", ".join(ctx.allergies[:5]) + ".", intent,
                            [{"type": "patient", "id": pid, "date": None, "source": "record"}], 0.9, True,
                            frame={"k": "allergies", "items": list(ctx.allergies[:5])})

    if intent == "referral":
        refs = ctx.recent_referrals
        if not refs:
            return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})
        r = refs[0]
        dept = r.get("recommended_department") or r.get("required_service") or "a hospital department"
        return RecordAnswer(f"Yes. Your record shows a referral on {spoken_date(r.get('created_at'))} to {dept}, currently marked {r.get('status', 'unknown')}.",
                            intent, [{"type": "referral", "id": r.get("id"), "date": r.get("created_at"), "source": "record"}], 0.9, True,
                            frame={"k": "referral", "date": r.get("created_at"), "dept": r.get("recommended_department") or r.get("required_service"),
                                   "status": r.get("status")})

    if intent == "appointment":
        ap = ctx.appointments
        if not ap:
            return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})
        a = ap[0]
        return RecordAnswer(f"Your latest appointment on record is on {spoken_date(a.get('appointment_date'))}"
                            f"{(' with ' + a['doctor_name']) if a.get('doctor_name') else ''}, marked {a.get('status', 'unknown')}.",
                            intent, [{"type": "appointment", "id": a.get("id"), "date": a.get("appointment_date"), "source": "record"}], 0.85, True,
                            frame={"k": "appt", "date": a.get("appointment_date"), "doctor": a.get("doctor_name"), "status": a.get("status")})

    if intent == "facility":
        ids = {c.get("facility_id") for c in consults} | {a.get("facility_id") for a in ctx.appointments} | \
              {r.get("receiving_facility_id") for r in ctx.recent_referrals}
        names = list(svc.repo.get_facility_names([i for i in ids if i]).values())
        if not names:
            return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})
        return RecordAnswer("Your record shows visits or referrals at " + ", ".join(sorted(set(names))[:3]) + ".", intent,
                            [{"type": "facility", "id": None, "date": None, "source": "record"}], 0.8, True,
                            frame={"k": "facility", "names": sorted(set(names))[:3]})

    return RecordAnswer(NOT_FOUND, intent, frame={"k": "notfound"})


# ------------------------------------------------------------------
# Staff-facing wording (dashboard): same grounded facts, third person, no patient hedges
# ------------------------------------------------------------------

_STAFF_SUBS = [
    (r"You told us earlier that you take", "The patient told Voxera earlier that they take"),
    (r"\bYour record\b", "The record"), (r"\byour record\b", "the record"),
    (r"\bYour last consultation\b", "The last consultation"),
    (r"\byour available records\b", "the available records"),
    (r"\bYour latest appointment\b", "The latest appointment"),
    (r"\byou discussed\b", "the patient discussed"), (r"\byou\b", "the patient"), (r"\bYou\b", "The patient"),
    (r"\byour\b", "the patient's"), (r"\bYour\b", "The patient's"),
]


def staff_view(text: str) -> str:
    """Reword a patient-directed answer for hospital staff. Facts are untouched."""
    t = (text or "")
    for hedge in (HEDGE, OLD_RX_NOW):
        t = t.replace(hedge, "")
    for rx, rep in _STAFF_SUBS:
        t = re.sub(rx, rep, t)
    return re.sub(r"\s{2,}", " ", t).strip()


# ------------------------------------------------------------------
# LLM polish (optional) with a strict validator
# ------------------------------------------------------------------

_ADVICE = re.compile(r"\b(you should|you must|i recommend|i suggest|take it|start taking|stop taking|increase|decrease|diagnos)", re.I)
_STOP = set("""about above after again also always another because before being between both could does doing during each
either every first from further having here itself just later might more most much never other over should since some still
such than that their them then there these they this those through under until very were what when where which while
whose will with would your yours record records lists listed shows showed prescribed previous previously doctor please
confirm information available current currently earlier notes note pharmacist verified verify unverified hasn't""".split())


def _words(s: str) -> set:
    return set(re.findall(r"[a-z][a-z'\-]{4,}", s.lower()))


def validate_llm_output(candidate: str, base: str) -> bool:
    if not candidate or len(candidate) > len(base) * 2 + 80:
        return False
    if _ADVICE.search(candidate) and not _ADVICE.search(base):
        return False
    nums_c, nums_b = set(re.findall(r"\d+(?:\.\d+)?", candidate)), set(re.findall(r"\d+(?:\.\d+)?", base))
    if nums_c - nums_b or nums_b - nums_c:          # nothing added AND nothing dropped (strengths, dates)
        return False
    if re.search(r"verif", base, re.I) and not re.search(r"verif", candidate, re.I):
        return False                                # an "unverified" caveat may never be dropped
    for month in _MONTHS:
        if month in base and month not in candidate:
            return False
    novel = _words(candidate) - _words(base) - _STOP
    return len(novel) == 0          # strict: a rewording may not introduce ANY new content word


def _llm_polish(llm, base: str) -> Optional[str]:
    system = ("Rewrite the text below to sound natural on a phone call, in at most 2 short sentences. "
              "Do NOT add, remove or change any medicine, number, date or instruction. Do NOT give advice.")
    try:
        out = (llm(system, base) or "").strip()
    except Exception:
        return None
    return out if validate_llm_output(out, base) else None
