"""Has the caller finished the call? (English understanding + native words, any of the three languages.)"""

from __future__ import annotations

import re

_EN = re.compile(r"\b(bye|good ?bye|that'?s (?:all|it)|that will be all|nothing (?:else|more)|no more|"
                 r"i'?m (?:done|good|fine|okay)|i am (?:done|fine)|no,? thank(?:s| you)|all good)\b", re.I)
_THANKS = re.compile(r"^\W*(?:ok(?:ay)?[,. ]+)?(?:thanks|thank you)(?: so much| very much)?\W*$", re.I)
_NATIVE = re.compile(r"धन्यवाद|शुक्रिया|अलविदा|बाय\b|आभार|बस\s*इतना|बस\s*(?:यही|अभी)|बस\.?$|और\s*कुछ\s*नहीं|आणखी\s*काही\s*नाही|"
                     r"इतकेच|एवढेच|येतो\b|झाले\b|काही\s*नाही")
_ASKED_ELSE = re.compile(r"anything else|else i can|और कुछ मदद|आणखी काही मदत|और कोई|आणखी कोणती", re.I)
_STRICT = re.compile(r"\b(bye|good ?bye)\b", re.I)
_STRICT_NATIVE = re.compile(r"अलविदा|बाय\b|येतो\b")
_PLAIN_NO = re.compile(r"^\W*(?:no|nope|nothing|नहीं|नही|नाही|नको)\W*$", re.I)


def is_closing(text_en: str, native: str = "", last_assistant: str = "", strict: bool = False) -> bool:
    """strict=True (a question is waiting for an answer): only an unmistakable goodbye ends the call."""
    en = (text_en or "").strip()
    if strict:
        return bool(_STRICT.search(en)) or bool(_STRICT_NATIVE.search(native or ""))
    nat = (native or "").strip()
    if _EN.search(en) and len(en.split()) <= 8:
        return True
    if _THANKS.match(en):
        return True
    if nat and len(nat.split()) <= 5 and _NATIVE.search(nat):
        return True
    if _ASKED_ELSE.search(last_assistant or "") and (_PLAIN_NO.match(en) or _PLAIN_NO.match(nat)):
        return True
    return False


_DECLINE = re.compile(r"^\W*(?:no|nope|skip|not now|i (?:don'?t|do not) (?:have|know|remember)\b.*|नहीं|नही|नाही|नको|मेरे पास नहीं.*|माझ्याकडे नाही.*)\W*$", re.I)


def declines_id(text: str) -> bool:
    return bool(_DECLINE.match((text or "").strip()))
