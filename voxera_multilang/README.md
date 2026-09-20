# voxera_multilang — Hindi / Marathi / English voice

Voxera detects the caller's language, then answers in it. English is unchanged; Hindi and Marathi were added
without touching the frozen emergency detector.

```
audio ─► language ID (Whisper, ~0.3 s)
          ├─ English ─► existing base.en path  (same as before)
          └─ Hindi/Marathi ─► Whisper-small int8: native text  ∥  English translation
native text   -> stored in the transcript; its WORDS decide Hindi vs Marathi and feed the emergency lexicon
English text  -> the existing English-only logic (triage, care topics, record questions)
replies       -> reviewed wording from catalog.py, spoken by the SAME Priya voice (Kokoro, per-language phonemes)
emergencies   -> the frozen detector, fed English renderings; only the SPOKEN reply is translated
```

## Set-up (once)

```
.venv312\Scripts\python.exe -m pip install faster-whisper          # already in requirements.txt
.venv312\Scripts\python.exe -m voxera_multilang.build_fast_stt     # builds the int8 model from your local small.pt (~1 min)
```
`VOXERA_MULTILANG=0` turns it off. If the model isn't built or the package is missing, Voxera says so at boot and runs
English-only. Nothing is downloaded at run time.

## How a call goes

1. The first line is a short trilingual greeting (the language isn't known yet).
2. The first thing the caller says decides the language. Hindi vs Marathi is decided by *words* (Whisper's own
   language ID confuses them: measured 85 % Hindi on Marathi audio). A language chosen from weak evidence (e.g. a spoken
   ID of digits) is corrected by the next clear turn; one English word can't flip a Hindi call.
3. The caller can also say "please speak in Marathi" / "हिंदी में बात कीजिए" at any time.

## Why Hindi/Marathi replies are not written by the LLM

`qwen3:1.7b` was tested: its Hindi is unreliable (it asked "what illness do you think you have?") and its Marathi
just echoed the patient. For a medical assistant that is not acceptable. So in Hindi/Marathi Voxera speaks:
the patient-ID flow, triage questions and conclusions, record answers (from structured facts), the curated home-care
guidance (with the existing allergy / child / pregnancy safety gates), and the emergency replies. Any other turn gets a
short reviewed prompt ("tell me what you're feeling and for how long" → "any other symptoms?" → "see a doctor…").
English still uses the LLM for open conversation.

## Emergencies in Hindi/Marathi

`safety.py` maps emergency phrases (Devanagari and romanised) to plain English sentences and gives them to the
**original** `check_emergency`, alongside Whisper's English translation. Either can trigger it. Plain denials
("सीने में दर्द नहीं है") don't; "bleeding that won't stop" isn't mistaken for a denial. The spoken reply comes from
`catalog.EMERGENCY`, keyed by the detector's own constants so it cannot drift.

## Please read before real patient use

* **The Hindi and Marathi wording has not been reviewed by a native-speaking clinician.** Everything is in
  `catalog.py` so it can be corrected in one place. Medical safety lines especially (emergency replies, triage
  conclusions, care guidance, medicine cautions) need a proper review.
* **Marathi speech recognition is weaker than Hindi** with Whisper *small* (on clean synthetic speech it often
  mis-spells words, and its English translation of Marathi can be wrong). The symptom/answer lexicon
  (`understand.py`) and the emergency lexicon cover the common words; unusual phrasing may fall through to a
  reviewed generic reply or be missed. Use `medium` (`python -m voxera_multilang.build_fast_stt medium`,
  then `VOXERA_MULTI_STT_DIR`) for better accuracy at a latency cost.
* **Everything was tested on synthesised speech, not real callers.** Real accents, noise and code-mixing will be harder.
* The Marathi voice is the Hindi-trained Kokoro model reading Marathi phonemes: it is intelligible (a synthesise →
  transcribe round trip recovers the sentence) but has a Hindi accent. Listen before deploying.
* Medicine names are spoken by the English voice inside Hindi/Marathi sentences.

## Files

| file | job |
|---|---|
| `lang.py` | script + word-level Hindi/Marathi/English detection, call-level tracker with hysteresis, "speak in X" requests |
| `stt.py`, `build_fast_stt.py` | int8 multilingual Whisper (built locally from `small.pt`): language ID, decode, translate |
| `tts.py` | Priya voice for en / hi / mr (per-script phonemizers, mixed-script sentences) |
| `catalog.py` | every Hindi/Marathi phrase, triage line, emergency reply, care step, record-answer frame |
| `safety.py` | emergency lexicon → English → frozen detector; spoken Hindi/Marathi patient IDs |
| `understand.py` | English "understanding" = translation + native symptom/yes-no lexicon |
| `integration.py` | `MultiLang`: the one object `voxera.py` uses |
