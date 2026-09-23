# voxera_telephony — Voxera on a real phone line (Exotel)

The same Voxera call (Hindi / Marathi / English, emergency safety, triage, record questions, care guidance, patient-ID
filing) answering a real phone call. Only the audio channel changes: `voxera.Call.serve()` runs over a `PhoneChannel`
instead of your laptop mic and speakers.

```
caller's phone ──PSTN──► Exotel ──wss (8 kHz PCM)──► /exotel/stream ──► PhoneChannel ──► Voxera Call
                                                       (this server)     hear · speak · barge-in · filler
```

## The order to do things (cheapest first)

1. **Prove the whole call locally — costs nothing.** Start the server, then dial it with the simulator (below). It uses
   Exotel's exact protocol, streams audio in real time, and reports what a caller would experience.
2. **Expose the server** with a tunnel (Exotel needs a public `wss://` URL): `cloudflared tunnel --url http://localhost:8200`
   (or ngrok). Copy the `https://…` host it prints.
3. **Exotel dashboard:** create a Flow (App) `Voicebot` applet → URL `wss://<host>/exotel/stream?token=<secret>`
   (or Basic-auth form `wss://KEY:SECRET@<host>/exotel/stream`), bidirectional, sample rate 8000, followed by a
   `Hangup` applet. Attach the flow to your Exophone.
4. **One controlled call** from a phone you own. Watch the server log.

## Run it

```bash
.venv312\Scripts\python.exe -m voxera_telephony.server            # loads models (~30-60 s), then serves :8200
```
`GET /health` → `{"ready": true}` when it can take calls. Set `VOXERA_STREAM_TOKEN=<long random string>` in `.env`
**before exposing it to the internet** (without it the endpoint is open — fine for localhost only).

### Dial it without a phone

```bash
.venv312\Scripts\python.exe -m voxera_telephony.sim --from +919876543210 ^
   --say "en:I have a mild fever and a headache since yesterday." ^
   --say "en:No, that's all. Thank you." --say "en:VX 421"
```
Other languages: `--say "hi:मुझे कल से बुखार है"`, `--say "mr:मला कालपासून ताप आहे"`. `--hangup-after 1` drops the line
mid-call. Replies are saved as `.wav` in `voxera_telephony/.sim_out/` — listen to them.

## What makes it feel like a call, not a demo

| behaviour | how |
|---|---|
| answers at once, greeting can be interrupted | greeting is pre-rendered; talking over it cuts it and keeps your words |
| caller talks over Voxera → it stops | sustained speech ≥ 0.36 s ⇒ Exotel `clear`; a cough doesn't count; emergency instructions can't be interrupted |
| never dead air | if the reply isn't ready after ~1.6 s it says "one moment" / "एक क्षण" in the caller's language |
| silence | "Hello, are you still there?" after 9 s, then a goodbye and hang-up |
| caller hangs up any time | `stop` event unblocks everything; the call summary is still written |
| real caller number | used for the call record and to raise identity assurance when it matches the patient's phone |
| goodbye + hang-up | after "that's all", Voxera asks for the patient ID once to file the call, says goodbye, closes the stream |

## Exotel protocol notes (from Exotel's Stream/Voicebot docs)

* audio is base64 raw 16-bit little-endian mono PCM, 8 kHz by default (`?sample-rate=16000|24000` supported by the server);
* events in: `connected`, `start` (has `from`, `to`, `call_sid`), `media`, `dtmf`, `stop`; out: `media`, `mark`, `clear`;
* audio we send must be in chunks that are a multiple of 320 bytes, at least 3,200 bytes (the server enforces this);
* closing the WebSocket ends the Voicebot applet; the flow continues to the next applet (put a **Hangup** applet after it).

## Limits — read before a real deployment

* **One call at a time.** The models are shared and CPU-bound; a second simultaneous caller is refused. Real
  concurrency needs a GPU host or several instances behind a router.
* **Speed.** Each reply must be produced on this machine. Hindi/Marathi turns were 4–14 s on the current (slow, OneDrive-
  synced) laptop in earlier measurements; the "one moment" filler hides part of it but it is not a substitute for a
  faster machine. Run the server somewhere with more CPU (or a GPU) for a real pilot.
* **8 kHz phone audio is harder for speech recognition** than the studio-quality audio used in earlier tests, especially Marathi.
* **Emergencies:** Voxera tells the caller to call 112/108 and to get to a hospital; it does **not** transfer the call
  or dispatch anything. A warm transfer needs Exotel's Connect applet and a staffed number — not built.
* **Unknown callers** get a placeholder patient record ("Caller 1234") because `calls.patient_id` is NOT NULL; the call is
  re-attached to the real patient when they give a verified patient ID.
* The Hindi/Marathi wording is unreviewed by a clinician (see `voxera_multilang/README.md`).
* Keypad (DTMF) digits are logged but not used yet.

## How the conversation works on the phone (curated, no free-form LLM)

`VOXERA_CURATED` is switched on for phone calls: the small local language model is **not** used, because on real phone audio it
guessed and invented ("shimbar is a typo for surgery") and took 7-9 s. Instead (`voxera_followup.py`):

1. the caller names a symptom -> a warm acknowledgement, **first aid / home remedy**, then the safety-gated **over-the-counter** option;
2. **3-4 short follow-up questions**, one at a time - red flags first, then how bad, how long, anything else;
3. only then a conclusion: manage at home / see a doctor in a day or so / be seen today (never a diagnosis, never a dose).

The frozen emergency detector still runs on every turn first. "Chest pain" is an emergency by design; softer phrasings
("burning after dinner", "gas") go through the question-first chest triage instead.
Unclear speech gets a kind "could you say that differently?" and, after two tries, a polite wrap-up.

`VOXERA_PHONE_FILLER=1` re-enables the "one moment" clip (off by default). `VOXERA_TTS_SPEED=1.0` slows Priya slightly.
