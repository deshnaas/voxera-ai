"""MultiLang: the one object voxera.py talks to.

    audio ──► language ID ─┬─ English ──► existing base.en path (unchanged)
                           └─ Hindi / Marathi ──► Whisper-small int8: native text  ∥  English translation
    native text  -> stored in the transcript, and used for language + emergency words
    English text -> fed to the existing English-only logic (triage, care topics, record intents, facts)
    replies      -> spoken from the reviewed catalog in the caller's language (no LLM writes Hindi/Marathi)
    emergencies  -> the FROZEN detector, fed English renderings (safety.py); only the SPOKEN reply is localised

If anything here is unavailable (model not built, package missing) ``enabled`` is False and Voxera behaves exactly as
before.
"""

from __future__ import annotations

import dataclasses
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from . import catalog as cat
from . import safety
from .lang import (LanguageTracker, NAMES, detect_from_text, explicit_request, family_from_lid)
from .stt import MultiSTT
from .understand import understand
from .tts import MultiTTS

_REV = {v["en"]: k for k, v in cat.PH.items()}                     # English string -> catalog key


@dataclass
class TurnText:
    native: str = ""          # what the caller said, in their script
    english: str = ""         # English rendering for the English-only logic
    lang: str = "en"          # language to REPLY in (after the tracker)
    heard: str = "en"         # language of this turn's audio
    confidence: float = 0.0
    path: str = "none"
    lid: Optional[dict] = None
    ms: int = 0


def _log(msg: str) -> None:
    print(f"[LANG] {msg}")


_EN_COMMON = set("""i me my we you your he she it they the a an is are was were am be been do does did have has had not no yes
and or but so if of to in on at for with from about this that these those what when where how why who which can could would should
will just very really some any there here please thank thanks hello hi okay ok feel feeling pain hurt hurts fever cough head
chest stomach since days day today yesterday""".split())


def english_sanity(text: str) -> bool:
    """Does the base.en output look like English? Hindi/Marathi speech decoded by the English model comes out as
    nonsense with almost no common English words, which is the cue to run the multilingual path."""
    words = [w.strip(".,!?'\"").lower() for w in (text or "").split()]
    if not words:
        return False
    if len(words) <= 2:
        return True                                        # too short to judge; short English answers are common
    hits = sum(1 for w in words if w in _EN_COMMON)
    return hits / len(words) >= 0.25


class MultiLang:
    def __init__(self, core, preferred: Optional[str] = None):
        self.core = core
        self.stt = MultiSTT()
        self.tts = MultiTTS(core)
        self.tracker = LanguageTracker(preferred=preferred)
        self.enabled = os.getenv("VOXERA_MULTILANG", "1").strip() != "0" and self.stt.available
        if not self.enabled and os.getenv("VOXERA_MULTILANG", "1").strip() != "0":
            _log(f"multilingual voice OFF ({self.stt.error}); English only.")
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ml")
        self._warmed: set = set()
        self._boot_thread: Optional[threading.Thread] = None
        self.generic_turns = 0
        self._dev_turns = 0            # counts Hindi/Marathi turns this call, to keep probing both languages early on

    def new_call(self, preferred: Optional[str] = None) -> None:
        """Forget the previous caller (language, generic-reply count) but keep the loaded models (server use)."""
        self.tracker = LanguageTracker(preferred=preferred)
        self.generic_turns = 0
        self._dev_turns = 0
        self._apply("en")

    # ---------------------------------------------------------------- boot ----------------------
    def boot(self) -> None:
        """Install the TTS hook and warm the model in the background (never blocks the greeting)."""
        if not self.enabled:
            return
        self.core.set_speech_language("en", synth=lambda text, lang: self.tts.synth(text, lang, "priya"))
        self._boot_thread = threading.Thread(target=self._warm, daemon=True, name="ml-warm")
        self._boot_thread.start()

    def _warm(self) -> None:
        try:
            t = time.time()
            self.stt.load()
            _log(f"multilingual STT ready ({time.time() - t:.1f}s)")
        except Exception as e:                                       # noqa: BLE001
            _log(f"multilingual STT failed to load ({type(e).__name__}); English only.")
            self.enabled = False

    def greeting_text(self, with_id: bool = True) -> str:
        """The first line is spoken BEFORE the language is known: a short trilingual greeting."""
        if not self.enabled:
            return cat.say("ask_id", "en") if with_id else "Hi, this is Voxera. How can I help you today?"
        if with_id:
            return " ".join(t for _, t in cat.GREETING_PARTS)
        return ("Hi, this is Voxera. How can I help you today? "
                "नमस्ते, मैं वॉक्सेरा हूँ, मैं आपकी क्या मदद कर सकती हूँ? "
                "नमस्कार, मी वॉक्सेरा आहे, मी तुम्हाला कशी मदत करू?")

    def prewarm_greeting(self, with_id: bool = True) -> None:
        """Pre-render the trilingual greeting into the TTS cache so it plays instantly."""
        if not self.enabled:
            return
        text = self.greeting_text(with_id)
        parts = cat.GREETING_PARTS if with_id else [
            ("en", "Hi, this is Voxera. How can I help you today?"),
            ("hi", "नमस्ते, मैं वॉक्सेरा हूँ, मैं आपकी क्या मदद कर सकती हूँ?"),
            ("mr", "नमस्कार, मी वॉक्सेरा आहे, मी तुम्हाला कशी मदत करू?")]
        try:
            gap = np.zeros(int(0.30 * 24000), dtype=np.float32)
            audio = []
            for lang, t in parts:
                a = self.tts.synth(t, lang, "priya")            # instant when build_tts_cache has run
                if a is None:
                    raise RuntimeError("part failed")
                audio += [a, gap]
            self.core._tts_cache[self.core._tts_key(text, "en")] = np.concatenate(audio[:-1])
        except Exception as e:                                       # noqa: BLE001
            _log(f"trilingual greeting unavailable ({type(e).__name__}); using English")
            self.enabled = False

    def followup_sentences(self, lang: str) -> list:
        """Every sentence the everyday-symptom conversation can speak with the standard (adult) profile. Long replies are
        spoken sentence by sentence on the phone, so caching sentences makes them start instantly."""
        import voxera_care as care
        import voxera_followup as fu
        from voxera_telephony.audio import split_sentences
        texts = list(fu.fixed_texts(lang))
        profile = care.build_profile({})
        for cid, cg in care._CARE.items():
            otc = care.suggest_otc(cid, profile)
            if lang == "en":
                texts.append(" ".join(list(cg.steps[:2]) + ([otc.spoken] if otc and otc.spoken else [])))
                texts.append(" ".join(list(cg.steps[:1]) + ([care._first_sentence(otc.spoken)] if otc and otc.spoken else [])))
                if cg.see_help_if:
                    texts.append(f"{fu.T['help_if']['en']} {cg.see_help_if[0]}")
            else:
                for caveat in (True, False):
                    adv = cat.care_reply(cg, otc, profile, lang, with_help=False, with_caveat=caveat)
                    if adv:
                        texts.append(adv)
                help_if = (cat.CARE.get(cid) or {}).get("help", {}).get(lang)
                if help_if:
                    texts.append(f"{fu.T['help_if'][lang]} {help_if}")
        for key in ("didnt_catch", "say_differently", "one_moment", "still_there"):
            texts.append(cat.say(key, lang))
        out, seen = [], set()
        for t in texts:
            for sent in split_sentences(t):
                if sent not in seen:
                    seen.add(sent)
                    out.append(sent)
        return out

    def fixed_phrases_en(self) -> list:
        """Every fixed ENGLISH line (the LLM only writes free conversation): triage questions - alone and with the
        calm / understanding lead-ins - triage conclusions, ID and wrap-up lines, emergency replies, record notices."""
        from voxera_patientfetch import triage as tri
        from voxera_patientfetch.call_integration import PHRASES as PF_LINES
        from voxera_emergency import CANNED_RESPONSES
        lead = ["", cat.PH["calm_prefix"]["en"], cat.PH["understand_prefix"]["en"]]
        out = [p + q for q in tri.Q.values() for p in lead]
        out += list(tri.TEXT_CONCLUSION.values()) + list(PF_LINES) + list(CANNED_RESPONSES)
        out += [v["en"] for v in cat.EMERGENCY.values() if "en" in v]
        out += [v["en"] for v in cat.PH.values() if "en" in v]
        out += self.followup_sentences("en")
        seen, res = set(), []
        for t in out:
            if isinstance(t, str) and t.strip() and t not in seen:
                seen.add(t)
                res.append(t)
        return res

    def load_english_cache(self) -> int:
        n = 0
        for text in self.fixed_phrases_en():
            a = self.tts.load_cached(text, "en")
            if a is not None:
                self.core._tts_cache[self.core._tts_key(text, "en")] = a
                n += 1
        return n

    def fixed_phrases(self, lang: str) -> list:
        """Every fixed line Voxera can speak in `lang` (rendered once by build_tts_cache.py)."""
        out = [v[lang] for v in cat.EMERGENCY.values()]
        out += [v[lang] for k, v in cat.PH.items() if k not in ("ask_id",)]
        out += [v[lang] for v in cat.TRIAGE_Q.values()]
        out += [v[lang] for v in cat.TRIAGE_CONCLUSION.values()]
        try:                                    # the standard (adult, no allergies) guidance for every care topic
            import voxera_care as care
            profile = care.build_profile({})
            for cid in cat.CARE:
                shim = type("C", (), {"care_id": cid})
                t = cat.care_reply(shim, care.suggest_otc(cid, profile), profile, lang)
                if t:
                    out.append(t)
        except Exception:                                            # noqa: BLE001
            pass
        out += self.followup_sentences(lang)
        out += [cat.HEDGE_L[lang], cat.OLD_RX_L[lang], cat.NOT_FOUND_L[lang], cat.CONFLICT_L[lang], cat.DB_DOWN_L[lang]]
        return out

    def prewarm_language(self, lang: str) -> None:
        """Load the pre-rendered fixed lines for `lang` from disk into the TTS cache. No synthesis happens here, so it can
        never compete with live speech recognition; a line that was not pre-rendered is synthesised when spoken."""
        if lang == "en" or not self.enabled or lang in self._warmed:
            return
        self._warmed.add(lang)
        loaded = 0
        for text in self.fixed_phrases(lang):
            a = self.tts.load_cached(text, lang)
            if a is not None:
                self.core._tts_cache[self.core._tts_key(text, lang)] = a
                loaded += 1
        _log(f"{NAMES[lang]}: {loaded}/{len(self.fixed_phrases(lang))} fixed lines ready"
             + ("" if loaded else "  (run: python -m voxera_multilang.build_tts_cache)"))

    # ---------------------------------------------------------------- language state ---------------
    @property
    def lang(self) -> str:
        return self.tracker.current("en")

    def set_language(self, lang: str, reason: str = "") -> None:
        if lang == self.lang and self.tracker.decided:
            return
        self.tracker.force(lang)
        self.core.set_speech_language(lang)
        self.prewarm_language(lang)
        _log(f"reply language -> {NAMES[lang]}{(' (' + reason + ')') if reason else ''}")

    def _apply(self, lang: str) -> None:
        """Push the tracker's language to the speech layer (after each observed turn)."""
        if lang != getattr(self.core, "_speak_lang", "en"):
            self.core.set_speech_language(lang)
            self.prewarm_language(lang)
            _log(f"reply language -> {NAMES[lang]}")

    # ---------------------------------------------------------------- hearing -----------------------
    def transcribe_turn(self, audio, english_fn: Callable, need_translation: bool = True) -> TurnText:
        """One caller turn -> native text, English text, language. `english_fn(audio)` is the existing base.en STT."""
        t0 = time.time()
        core = self.core
        dur = len(audio) / core.SAMPLE_RATE
        if dur < core.MIN_AUDIO_SECONDS or (core.rms(audio) < core.MIN_RMS_FOR_STT and core.peak(audio) < core.MIN_PEAK_FOR_STT):
            return TurnText(lang=self.lang)
        a16 = np.asarray(audio, dtype=np.float32)

        locked_en = self.tracker.lang == "en"
        locked_dev = self.tracker.lang in ("hi", "mr")
        probs: dict = {}
        if locked_en:
            # English call: the existing base.en path, exactly as before. The (slower) language check runs ONLY when
            # its output does not look like English at all, i.e. the caller may have switched to Hindi/Marathi.
            text = english_fn(audio) or ""
            if english_sanity(text):
                self.tracker.observe("en", 0.8, len(text.split()))
                self._apply(self.tracker.current("en"))
                return TurnText(text, text, self.lang, "en", 0.8, "english", probs, int((time.time() - t0) * 1000))
            probs = self.stt.detect(a16)
            fam, fam_conf = family_from_lid(probs)
            if fam == "en" and fam_conf >= 0.55:
                self.tracker.observe("en", fam_conf, len(text.split()))
                self._apply(self.tracker.current("en"))
                return TurnText(text, text, self.lang, "en", fam_conf, "english", probs, int((time.time() - t0) * 1000))
        elif not locked_dev:
            probs = self.stt.detect(a16)                               # first turn(s): decides which single path to run
            fam, fam_conf = family_from_lid(probs)
            if fam == "en" and fam_conf >= 0.55:
                text = english_fn(audio) or ""
                self.tracker.observe("en", fam_conf, len(text.split()))
                self._apply(self.tracker.current("en"))
                return TurnText(text, text, self.lang, "en", fam_conf, "english", probs, int((time.time() - t0) * 1000))

        # Hindi / Marathi. Decode with the language's OWN token (a "hi" token biases the words themselves toward
        # Hindi spellings - see stt.py); when unsure which it is, decode with both and keep the one whose own words
        # agree with it. A settled call (several confident turns of the same language) skips the second decode.
        native, verdict = self._decode_dev(a16)
        heard = verdict.lang or self.tracker.lang or "hi"
        quick = understand(native, "")
        if safety.to_english(native) or quick in ("Yes.", "No.") or not need_translation:
            english = quick or native                                  # an emergency phrase / a plain yes-no: answer NOW
        else:
            english = understand(native, self.stt.translate(a16, heard))
        self.tracker.observe(heard, verdict.confidence, len(native.split()))
        self._dev_turns += 1
        self._apply(self.tracker.current("en"))
        _log(f"heard {NAMES[heard]} (words hi={verdict.hi_score} mr={verdict.mr_score}) -> replying in {NAMES[self.lang]}")
        return TurnText(native, english, self.lang, heard, verdict.confidence, "multilingual", probs, int((time.time() - t0) * 1000))

    PROBE_TURNS = 3   # keep checking both languages for this many Hindi/Marathi turns, even after one looks decided

    def _decode_dev(self, a16):
        settled = (self.tracker.lang in ("hi", "mr") and not self.tracker.tentative
                   and self._dev_turns >= self.PROBE_TURNS)
        if settled:
            tok = self.tracker.lang
            native = self.stt.decode(a16, tok)
            return native, detect_from_text(native, prior=self.tracker.lang, preferred=self.tracker.preferred)
        hi_native = self.stt.decode(a16, "hi")
        hi_verdict = detect_from_text(hi_native, prior=self.tracker.lang, preferred=self.tracker.preferred)
        if hi_verdict.lang == "hi" and hi_verdict.confidence >= 0.75 and hi_verdict.hi_score >= 2:
            return hi_native, hi_verdict                                # clearly Hindi even by its own (Hindi-token) words
        mr_native = self.stt.decode(a16, "mr")
        mr_verdict = detect_from_text(mr_native, prior=self.tracker.lang, preferred=self.tracker.preferred)
        if mr_verdict.lang == "mr" and mr_verdict.confidence >= hi_verdict.confidence:
            return mr_native, mr_verdict
        return hi_native, hi_verdict

    def maybe_switch_on_request(self, turn: TurnText) -> Optional[str]:
        """'Please speak in Marathi' style requests win over detection."""
        want = explicit_request(turn.native) or explicit_request(turn.english)
        if want and want != self.lang:
            self.set_language(want, "requested by the caller")
            return want
        return None

    # ---------------------------------------------------------------- safety -----------------------
    def emergency(self, check_emergency: Callable, turn: TurnText, *, context: str = "", last_assistant: str = ""):
        """The frozen detector over every English rendering; the SPOKEN reply is localised."""
        if turn.path in ("english", "english-fallback", "none") and self.lang == "en":
            return check_emergency(turn.native, context=context, last_assistant=last_assistant)
        r = safety.emergency_probe(check_emergency, turn.native, turn.english, context=context, last_assistant=last_assistant)
        if r is None:
            return None
        lang = turn.heard if turn.heard in ("hi", "mr") else self.lang
        if lang != self.lang:                                        # answer in the language the emergency was spoken in
            self.set_language(lang, "emergency")
        return dataclasses.replace(r, spoken_response=cat.emergency_text(r.spoken_response, lang))

    def localize_emergency(self, result):
        """Localise the spoken reply of an emergency result the frozen detector already produced."""
        if result is None or self.lang == "en":
            return result
        return dataclasses.replace(result, spoken_response=cat.emergency_text(result.spoken_response, self.lang))

    # ---------------------------------------------------------------- wording ----------------------
    def say(self, key: str) -> str:
        return cat.say(key, self.lang)

    def localize(self, english: str) -> str:
        """Any fixed English line Voxera would speak -> the caller's language (unchanged if English / unknown)."""
        if self.lang == "en" or not english:
            return english
        key = _REV.get(english.strip())
        return cat.say(key, self.lang) if key else english

    def triage_text(self, step) -> str:
        if self.lang == "en":
            return step.text
        if step.kind == "ask":
            prefix = ""
            if step.text.startswith(cat.PH["calm_prefix"]["en"]):
                prefix = cat.say("calm_prefix", self.lang)
            elif step.text.startswith(cat.PH["understand_prefix"]["en"]):
                prefix = cat.say("understand_prefix", self.lang)
            return prefix + cat.triage_question(step.key or "", self.lang, step.text)
        if step.kind == "conclude":
            return cat.triage_conclusion(step.conclusion or "", self.lang, step.text)
        return step.text

    def record_text(self, english: str, frame: Optional[dict]) -> str:
        if self.lang == "en":
            return english
        if frame:
            out = cat.render_record(frame, self.lang)
            if out:
                return out
        loc = self.localize(english)                              # a known fixed line (privacy / verification notices)
        return loc if loc != english else cat.NOT_FOUND_L[self.lang]   # never speak English to a Hindi/Marathi caller

    def care_text(self, care, otc, profile: dict) -> Optional[str]:
        return cat.care_reply(care, otc, profile, self.lang)

    def generic_reply(self, english_text: str) -> Optional[str]:
        """Deterministic conversation for turns no curated path handles (replaces free-form LLM text in hi/mr)."""
        self.generic_turns += 1
        n = self.generic_turns
        if n == 1:
            return self.say("tell_more")
        if n == 2:
            return self.say("other_symptoms_q")
        if n == 3:
            return self.say("see_doctor")
        if n == 4:
            return self.say("anything_else")
        return None                       # nothing new to say: the call is wrapped up instead of repeating itself

    def reset_generic(self) -> None:
        self.generic_turns = 0

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
