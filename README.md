# Voxera

Real-time AI healthcare voice agent. A patient talks to Voxera as if it were a
phone call: Voxera listens, understands, answers in a natural human voice
(**Priya / Kokoro**), remembers what was said during the call, escalates
emergencies, and writes everything to Supabase so hospital staff see it on the
dashboard.

```
PATIENT  <->  VOXERA  <->  HOSPITAL
 speech      STT -> reasoning -> Priya TTS      Supabase (patients, calls,
             + deterministic emergency layer    conversation_turns, referrals,
                                                referral_events, appointments)
```

## What runs where

| Stage        | Component                        | Notes |
|--------------|---------------------------------|-------|
| STT          | OpenAI Whisper `base.en` (CPU)  | `small.en` optional, ~2.5s slower |
| Endpointing  | `voxera_core.MicCapture.record_turn` | 0.8 s silence, anchored to the measured noise floor (`* 2.2`) + relative-drop (`* 0.32`) + a soft endpoint; ends promptly even when the room noise floor is above the fixed threshold |
| Safety       | `voxera_emergency.py` — 2-stage deterministic, no LLM | **FROZEN.** candidate regex + context validation (negation, temporal, echo-strip, symptom combination); runs first, every turn |
| Care/OTC     | `voxera_care.py` — curated rules, no LLM knowledge | home-care + safety-gated OTC; LLM only re-words approved text, invented meds/doses rejected |
| Reasoning    | Ollama `qwen3:1.7b`, **one** streamed call, `think=False` | fallback conversational path when no emergency and no care topic |
| Facts        | `voxera_core.extract_facts` — regex | name / symptoms / duration / temp / meds + age / child / pregnancy / allergies / conditions; persisted as a `system` turn |
| Voice        | Kokoro 82M + `en_priya` voice pack | model + phonemizer warmed once; fixed lines pre-rendered |
| Persistence  | `voxera_supabase.py` + async writer | DB never blocks the spoken path |

### Per-turn pipeline

```
capture (0.8s endpoint)  ->  transcribe
  ->  check_emergency()          [FROZEN]   -- fires? speak canned line + escalate, done
  ->  voxera_care.lookup_care()             -- matches? curated guidance, LLM re-words, validate
  ->  vx.llm_respond()                      -- otherwise: normal conversation
  ->  Priya TTS  (+ AEC barge-in)
```

**Not wired:** PSTN telephony — there is no telephony provider or credentials in
this environment. The local microphone/speaker is the stand-in for the call.
Everything else (STT, reasoning, Priya, Supabase referrals/appointments,
latency measurement) is real.

## Setup

```bash
python -m venv .venv312 && .venv312\Scripts\activate      # Python 3.12
pip install -r requirements.txt                            # see note below
ollama pull qwen3:1.7b
copy .env.example .env      # set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (server-side only)
```

`.env` is git-ignored. The Supabase **service-role** key stays server-side —
never ship it to a browser/frontend. `voxera_supabase.py` reads
`SUPABASE_SERVICE_ROLE_KEY` first, then falls back to `SUPABASE_KEY` /
`SUPABASE_ANON_KEY` so an older `.env` keeps working.

Dependencies are already installed in `.venv312` on the demo machine
(whisper, kokoro, torch, sounddevice, soundfile, supabase, pywebrtc-audio,
python-dotenv, requests, numpy). `models/goonj/` holds the Priya voice pack and
Kokoro weights; `models/` is git-ignored.

## Run

```bash
# 0. one-time per machine / room: check the mic
python mic_check.py            # prints VOXERA_START_THRESHOLD / _END_THRESHOLD for .env

# 1. Patient inbound call
python voxera.py               # speak naturally, no ENTER, Ctrl+C hangs up

# 2. Hospital outbound appointment call
python voxera_hospital.py --seed          # creates a demo appointment, then calls
python voxera_hospital.py --appointment <id>
```

### Demo script that works end-to-end

1. **Normal call** — "I've had a fever since yesterday." / "About 102." /
   "I'm also really tired." Voxera keeps context, asks one question at a time,
   never repeats itself. Turns are saved to `conversation_turns`.
2. **Emergency** — "Actually, my chest is really tight and heavy." →
   deterministic layer fires instantly (no LLM wait), Priya says a short safety
   line, a **referral** + **referral_event** are created in Supabase, the call
   is marked `emergency_escalated`. Chest *tightness / pressure / heaviness /
   squeezing* all count — "chest pain" is not required. Negated symptoms
   ("no chest pain", "it went away") do **not** trigger.
3. **Appointment** — `python voxera_hospital.py --seed`, answer "Yes, that
   works." → `appointments.status` becomes `Confirmed`. Answer "Can I do
   Friday?" → `Rescheduled` with the new date.

## Latency

Every turn prints:

```
[STT] 0.5s   [AI first] 2.3s  [AI] 2.6s   [TTS first] 1.7s  [TTS] 1.7s   [EOS->AUDIO] 4.5s   [TURN] 4.5s
```

and the call ends with an average / min / max summary. On this CPU-only local
stack, end-of-speech → first audio is ~4–6s for generated replies; fixed lines
(greeting, every emergency response) are **pre-rendered at boot** so they start
instantly. The most important number, *time to first spoken audio*, is
`EOS->AUDIO`.

Knobs in `.env`: `VOXERA_WHISPER_MODEL`, `VOXERA_LLM_MAX_TOKENS`,
`VOXERA_END_SILENCE`, `VOXERA_LLM_KEEP_ALIVE`.

## Barge-in

Default **`VOXERA_BARGEIN=aec`** — true full-duplex. Priya plays through a
`sounddevice` duplex stream; the exact frame sent to the speaker is the far-end
reference for WebRTC AEC3. The near-end mic frame is echo-cancelled and only
stops Priya when **all** of these hold (pure gate `voxera_core.barge_in_decision`,
unit-tested in `tests/test_barge_in.py`):

**Per-frame gate** — all three must hold:

| signal | threshold |
|---|---|
| spectral speech probability (post-AEC) | ≥ 0.85 |
| cleaned near-end RMS | ≥ max(0.005, **3× measured echo residual**) |
| raw near/far correlation | ≤ 0.45 (higher = it's just Priya) |

**Then**: at least one unbroken run of ≥ 3 qualifying frames (rejects clicks)
**and** ≥ 0.32 s of *net* qualifying speech on a leaky accumulator — gaps up to
80 ms (between phonemes/words) don't erode it, only a real pause does. Requiring
0.35 s of *unbroken* frames was too strict — a normal "Wait" only holds a run of
~15–18 frames; the offline integration harness (`tests/test_barge_in_integration.py`)
caught that. First 300 ms of each reply = blind window (AEC convergence); next
400 ms measures this call's echo-residual floor. A single high-probability frame,
echo, a fan, or a short cough do **not** interrupt. On confirm, Priya stops and a
400 ms pre-roll of the interruption is carried into the next turn.

Every non-interrupted reply prints a one-line reason
(`[BARGE] no near-end speech …` or `[BARGE] rejected — <signal that failed> …`),
so a missed manual "WAIT" is debuggable instead of a mystery.

While Priya speaks, the normal capture mic is **paused** so her audio can never
enter the transcription queue (that was the earlier bug where Whisper
transcribed Priya's own line and it looked like the patient). Also passes:
`VOXERA_BARGEIN=off` = strict turn-taking.

## Tests

```bash
python tests/test_emergency.py             # 46 cases (FROZEN detector): chest/stroke/breathing/bleed/fever, negation, past, echo-strip, throat-context
python tests/test_barge_in.py              # pure multi-signal gate: echo / noise / cough rejected, real speech accepted
python tests/test_barge_in_integration.py  # drives the REAL WebRTC AEC + gate on synthetic near-end audio (echo/noise/blip rejected)
python tests/test_endpoint.py              # end-of-turn latency: quiet/moderate/very-noisy rooms -> ~0.8s, mid-sentence pause not clipped
python tests/test_facts.py                 # structured-fact extraction (name/symptoms/age/child/pregnancy/allergies/conditions) + negation
python tests/test_care.py                  # home-care + OTC: fall/graze/minor-bleed, child defers, emergency wins, meta-questions rejected
python tests/test_supabase_layer.py        # connection, calls.patient_id link, turns, escalation, appointment (self-cleans)
python tests/test_full_call.py             # scripted 4-turn call: memory + persistence + escalation + latency
python voxera_emergency.py                 # quick inline smoke test
```

The one thing no automated test can cover is **a human speaking over Priya**.
Run `python voxera.py`, let Priya talk, say **"WAIT"** normally — expect
`[BARGE] candidate …` → `[BARGE] CONFIRMED …`. Then run again and stay silent —
expect `[BARGE] no near-end speech …`. If "WAIT" is missed, the `[BARGE] rejected`
line names the signal that fell short (don't lower thresholds blindly — tune from
that number).

## Hospital dashboard

The teammate's Next.js dashboard is vendored in [`dashboard/`](dashboard/) and
reads the **same** Supabase project. Voxera writes `ai_assessments`, `referrals`,
`referral_events`, `emergency_cases`, `referral_notifications` (realtime popup),
`appointments`, and an end-of-call **structured summary** (`call_summaries`, or a
`system` conversation‑turn fallback). Setup, the architecture comparison, and the
one SQL migration are in [`INTEGRATION.md`](INTEGRATION.md).

```bash
cd dashboard && npm install && npm run dev     # frontend uses the PUBLISHABLE key only
```

## Files

```
voxera.py              patient inbound call (entrypoint)
voxera_hospital.py     hospital outbound appointment call (entrypoint)
voxera_core.py         shared engine: STT, LLM, Priya TTS, mic/VAD, latency, async DB
voxera_emergency.py    deterministic emergency / safety layer  (FROZEN)
voxera_care.py         curated home-care + OTC knowledge layer
voxera_summary.py      deterministic end-of-call structured summary (no LLM)
voxera_supabase.py     Supabase data layer (patients, calls, turns, referrals,
                       ai_assessments, emergency_cases, referral_notifications, appointments)
mic_check.py           microphone diagnostic
sql/                   additive schema migration for the dashboard integration
dashboard/             vendored Next.js hospital dashboard (same Supabase project)
models/goonj/          Priya voice pack + Kokoro (kokoro_generate.py = "goonj")
```
