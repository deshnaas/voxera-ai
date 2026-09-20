# voxera_patientfetch — Voxera Patient Intelligence

Retrieval-first patient identity, record Q&A, prescription understanding, access control + audit,
voice-signal analysis and adaptive triage. It is an **add-on** to the existing call flow:
`voxera_emergency.py` (frozen) stays the only emergency authority, and nothing here diagnoses,
prescribes, or changes a clinician's record.

```
CALL ─► frozen emergency detector (always first, even before ID)
    └─► patient ID verification ─► PatientContext (loaded once, cached)
            ├─ record question?  ─► retrieval ─► grounded answer (no LLM by default)
            ├─ symptom?          ─► adaptive triage (deterministic; asks, never diagnoses)
            └─ otherwise         ─► existing care / OTC / LLM path (now told the verified allergies)
    voice signal: turn audio ─► isolated SpeechBrain worker (async, low priority) ─► hint only
DASHBOARD ─► signed-in JWT ─► API ─► role + facility policy ─► (reason) ─► audit ─► minimum-necessary sections
```

## One-time setup

1. **Database** – paste `sql/2026_voxera_patient_intelligence.sql` into the Supabase SQL Editor and run it
   (idempotent; supersedes the three earlier dashboard SQL files). It adds `patients.patient_id` (`VX-000123`),
   `medical_documents`, `prescription_items`, `patient_medications` (with provenance), the append-only
   `patient_record_access_logs`, facility+role-scoped RLS and a private storage bucket.
2. **Give your demo patient a memorable ID** (optional):
   `update public.patients set patient_id = 'VX-000421' where phone = '9990001111';`
3. **Real roles** (optional): `hospital_users.role` should be `doctor | nurse | receptionist | admin | operator`.
   Legacy `staff` is treated as `doctor` so today's logins keep working (`VOXERA_LEGACY_STAFF_ROLE`).
4. **Voice model** (optional, ~1.5 GB, isolated venv):
   ```
   .venv312\Scripts\python.exe -m venv voxera_patientfetch\.venv_voice
   voxera_patientfetch\.venv_voice\Scripts\python.exe -m pip install -r voxera_patientfetch\requirements_voice.txt
   ```
   The weights download on first start. Without it the call runs normally and the signal is `unavailable`.
5. **Record-grounding model** (optional): `ollama create voxera-patientfetch -f voxera_patientfetch/Modelfile`
   and set `VOXERA_RECORD_LLM=1`. Off by default (3–5 s per reword on CPU).

## Run

```
# terminal 1 – patient-records API (OCR, search, record Q&A, audit). Uses SUPABASE_SERVICE_ROLE_KEY from .env
.venv312\Scripts\python.exe -m uvicorn voxera_patientfetch.service:app --port 8100

# terminal 2 – dashboard (already set up)             cd dashboard ; npm run dev
# terminal 3 – the voice agent (unchanged command)    .venv312\Scripts\python.exe voxera.py
```

`VOXERA_PATIENTFETCH=0` restores the classic call flow. If the migration has not been run the layer
turns itself off and says so at boot.

## Modules

| file | job |
|---|---|
| `identity.py` | spoken-ID normalisation (`V X zero zero four two one` → `VX-000421`), 3-attempt verifier, phone as a *secondary* signal only |
| `patient_ai.py` | `PatientIntelligenceAI`: context, unified medication view with provenance, stage/confirm/reject, audit |
| `record_qa.py` | intent → targeted retrieval → grounded answer; conflict + "not found" + "should I take it now?" rules; validated optional reword |
| `prescription_ai.py` | PDF/image → OCR (RapidOCR = PaddleOCR models on ONNX) → parser → *pending* candidates. Never active |
| `access_policy.py` | role × home/external matrix, reasons, redaction, audit rows (data, not code paths) |
| `triage.py` | adaptive follow-up questions; conclusions from a fixed vocabulary; defers to the frozen detector |
| `voice_signal.py`, `voice_worker.py` | acoustic features + SpeechBrain wav2vec2-IEMOCAP posteriors in a separate process |
| `call_integration.py` | the small glue `voxera.py` calls |
| `service.py` | FastAPI: the only place the service-role key is used for staff requests |
| `repository.py`, `memory_repo.py` | Supabase access (existing client) and an in-memory twin for tests |

## API (all require `Authorization: Bearer <staff session JWT>`)

`GET /api/patients/search?q=` · `POST /api/patients/{id}/record` · `GET /api/patients/{id}/medications|prescriptions|consultations|audit`
· `POST /api/patients/{id}/record-question` · `POST /api/patients/{id}/prescriptions/upload`
· `POST /api/prescription-items/{item}/verify` · `GET /api/patients/{id}/documents/{doc}` · `POST /api/patient-access-log`

## Safety rules that are enforced and tested

- OCR output is a **candidate**; only a doctor's confirmation makes it `ocr_verified`, and the database refuses
  an `ocr` row in `patient_medications` or an `ocr_verified` row without a verifier.
- Patient ID alone authorises nothing on the dashboard; external access needs a role, a reason and is always audited
  (if it cannot be audited it is refused).
- Voice signal labels are limited to `neutral | calm | sad | high_arousal | uncertain`; it can soften tone or move the
  safety question earlier, and can never trigger or suppress an emergency.
- Absence from the record is answered "I couldn't find that…", never "you don't have…".

## Known limits (read before a demo)

- IEMOCAP has four classes (neutral/angry/happy/sad) trained on acted US English. It cannot detect fear and is unvalidated
  on Indian English/Hindi, so the signal is deliberately weak and never used for decisions.
- Voice-model latency is ~1 s per turn on CPU (async, so it never blocks a reply); the first call after boot is slower.
- The frozen detector treats *"I had chest discomfort last week and feel completely normal now"* as `cardiac_chest`
  (over-triage). It is frozen, so triage defers to it.
- Cross-facility rules live in `access_policy.py` (Python) and RLS (SQL); keep them in sync when you tighten either.
