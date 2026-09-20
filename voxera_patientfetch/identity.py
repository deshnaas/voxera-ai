"""Patient identity: spoken patient-ID normalisation + call-time verification.

Rules (all deterministic, no LLM):
  * The human patient ID (``VX-000123``) is the primary key a caller proves.
  * Phone number is only a SECONDARY signal; it never verifies on its own.
  * A patient UUID is accepted (staff/API use) but is never guessable by voice.
  * Failed lookups never reveal whether/which other patients exist.
  * Max attempts, then the call continues WITHOUT record access.
"""

from __future__ import annotations

import re
import uuid
from typing import Optional

from .models import VerificationResult

PREFIX = "VX"
DIGITS = 6           # canonical zero-padded width: VX-000123
MAX_ATTEMPTS = 3

_UUID_RX = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)

_NUM_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "nil": "0",
    "one": "1", "two": "2", "to": "2", "too": "2", "three": "3", "four": "4", "for": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "ate": "8", "nine": "9",
    # Hindi/Hinglish numerals as Whisper commonly romanises them
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4", "chaar": "4",
    "paanch": "5", "panch": "5", "chhe": "6", "che": "6", "saat": "7", "aath": "8", "nau": "9",
}
_MULT = {"double": 2, "triple": 3, "dabal": 2}
_DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")
_FILLER = re.compile(
    r"\b(my|the|patient|id|is|it'?s|its|number|no|please|yes|yeah|okay|ok|sure|"
    r"i|am|that|would|be|uh|um|hmm|and|hi|hello)\b", re.I)


def normalize_patient_id(raw: Optional[str]) -> Optional[str]:
    """Return canonical ``VX-000123`` or None if nothing ID-like is present.

    Handles: "vx 421", "V X zero zero four two one", "VX-00421", "vx double
    zero four two one", stray punctuation/case/whitespace, Devanagari digits.
    """
    if not raw:
        return None
    text = str(raw).translate(_DEVANAGARI).strip().lower()
    text = re.sub(r"[.,;:!?\"'()\[\]]", " ", text)
    # "v x" / "v.x" / "vx" / "v-x" -> "vx"
    text = re.sub(r"\bv[\s\-_.]*x\b", "vx", text)
    m = re.search(r"\bvx\b", text)
    if m:
        tail = text[m.end():]
    else:
        # accept a bare number only when the whole utterance is ID-like
        stripped = _FILLER.sub(" ", text)
        if not re.fullmatch(r"[\s\d\-]*(?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|double|triple)[\s\-]*)*[\s\d\-]*", stripped):
            return None
        tail = stripped
    digits: list[str] = []
    mult = 1
    for tok in re.findall(r"[a-z]+|\d", tail):
        if tok.isdigit():
            digits.append(tok * mult)
            mult = 1
        elif tok in _MULT:
            mult = _MULT[tok]
        elif tok in _NUM_WORDS:
            digits.append(_NUM_WORDS[tok] * mult)
            mult = 1
        elif _FILLER.fullmatch(tok):
            continue
        else:
            # any other word after the prefix ends the ID
            if digits:
                break
    num = "".join(digits)
    if not num or len(num) > 10:
        return None
    return f"{PREFIX}-{num.zfill(DIGITS)[-DIGITS:] if len(num) <= DIGITS else num}"


def extract_uuid(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    m = _UUID_RX.search(str(raw))
    if not m:
        return None
    try:
        return str(uuid.UUID(m.group(0)))
    except ValueError:
        return None


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Digits only, last 10 (Indian mobile convention). None if too short."""
    if not raw:
        return None
    d = re.sub(r"\D", "", str(raw).translate(_DEVANAGARI))
    return d[-10:] if len(d) >= 10 else (d if len(d) >= 7 else None)


def phones_match(a: Optional[str], b: Optional[str]) -> Optional[bool]:
    na, nb = normalize_phone(a), normalize_phone(b)
    if not na or not nb:
        return None
    return na == nb


# ------------------------------------------------------------------
# Spoken lines (kept here so they are testable and consistent)
# ------------------------------------------------------------------

LOW_ASSURANCE = ("For your privacy I can't read out record details on this call, but I can still help "
                 "with what you're feeling. Your care team can go through your records with you.")
ASK_ID = "Hi, I'm Priya from Voxera. Before we begin, could you please tell me your patient ID?"
RETRY_ID = "Sorry, I couldn't find that patient ID. Could you please repeat it?"
RETRY_FORMAT = "Sorry, I didn't catch a patient ID there. Could you say it again, one digit at a time?"
VERIFIED = "Thank you. I have your record. What can I help you with today?"
GIVE_UP = ("I'm unable to verify your patient record right now. I can still help with "
           "general information, but I won't access a medical record without verification.")
DB_DOWN = "I'm unable to access the patient record right now. I can still help with general information."


class IdentityVerifier:
    """Stateful per-call verifier. ``repo`` needs ``find_patient(patient_id=..|uuid=..)``.

    ``caller_phone`` (optional, from telephony/demo config) is compared AFTER the
    ID is found and only recorded as a secondary signal.
    """

    def __init__(self, repo, caller_phone: Optional[str] = None, max_attempts: int = MAX_ATTEMPTS):
        self.repo = repo
        self.caller_phone = caller_phone
        self.max_attempts = max_attempts
        self.attempts = 0
        self.patient: Optional[dict] = None
        self.locked = False
        self.assurance = "none"

    @property
    def verified(self) -> bool:
        return self.patient is not None

    def attempt(self, utterance: str) -> VerificationResult:
        if self.verified:
            return VerificationResult(True, "ok", self.patient, self.attempts, 0, assurance=self.assurance, spoken="")
        if self.locked:
            return VerificationResult(False, "locked", None, self.attempts, 0, spoken=GIVE_UP)

        pid = normalize_patient_id(utterance)
        uid = extract_uuid(utterance)
        if not pid and not uid:
            return self._fail("bad_format", RETRY_FORMAT)

        try:
            rows = self.repo.find_patient(patient_id=pid, uuid=uid)
        except Exception:
            # Do NOT burn an attempt or hallucinate; the caller should say DB_DOWN.
            return VerificationResult(False, "error", None, self.attempts,
                                      self.max_attempts - self.attempts, spoken=DB_DOWN)

        if isinstance(rows, dict):
            rows = [rows]
        rows = rows or []
        if len(rows) == 0:
            return self._fail("not_found", RETRY_ID)
        if len(rows) > 1:
            # duplicate human IDs must never silently pick one
            return self._fail("ambiguous", RETRY_ID)

        self.patient = rows[0]
        self.attempts += 1
        pm = phones_match(self.caller_phone, self.patient.get("phone"))
        assurance = "high" if pm is True else "low" if pm is False else "standard"
        self.assurance = assurance
        return VerificationResult(True, "ok", self.patient, self.attempts, 0,
                                  phone_matched=pm, assurance=assurance, spoken=VERIFIED)

    def _fail(self, reason: str, spoken: str) -> VerificationResult:
        self.attempts += 1
        left = max(0, self.max_attempts - self.attempts)
        if left == 0:
            self.locked = True
            spoken = GIVE_UP
        return VerificationResult(False, reason, None, self.attempts, left, spoken=spoken)
