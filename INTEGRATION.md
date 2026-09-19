# Voxera ⇄ Hospital Dashboard — Integration

The teammate's Next.js hospital dashboard
(`github.com/saisuriya330-oss/voxera_hospital_dashboard`) now lives in
[`dashboard/`](dashboard/) and talks to the **same** Supabase project as the
Voxera Python backend. No second database, no mock data.

## Architecture comparison

| | Voxera (this repo, Python) | Dashboard (`dashboard/`, Next.js 16) |
|---|---|---|
| Role | Real‑time voice agent: STT → frozen emergency layer → care/OTC → Priya TTS; **writes** clinical records | Hospital staff UI: **reads** those records, staff actions (accept referral, appointment status, beds) |
| Supabase key | `SUPABASE_SERVICE_ROLE_KEY` (server‑side, bypasses RLS) | `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (anon, browser) |
| Auth | none (backend) | Supabase Auth email/password → `hospital_users(user_id → facility_id)`; every page is facility‑scoped |
| Realtime | — | `supabase.channel` INSERT listener on `referral_notifications` (popup) |

### Shared tables (all already exist in the project)

`patients · calls · conversation_turns · referrals · referral_events ·
appointments · facilities · facility_beds · hospital_users · emergency_cases ·
ai_assessments · referral_notifications · follow_ups`

### What Voxera writes now (so the dashboard's existing pages light up)

| Event | Rows written | Dashboard view |
|---|---|---|
| Call starts | `patients` (get‑or‑create by phone), `calls` (**`patient_id` NOT NULL**, `facility_id` = receiving facility) | Calls page, Patient → Calls |
| Every turn | `conversation_turns` (patient / ai / system) | Patient → Calls → transcript |
| Emergency (frozen detector) | `ai_assessments` → `referrals` (`status="pending"`, `urgency="high"`, `ai_assessment_id`) → `referral_events` → `emergency_cases` (`status="active"`) → `referral_notifications` | Overview, Emergency, Referrals, realtime popup, Alerts |
| Appointment flow | `appointments` (`status` = `scheduled`→`confirmed`/`rescheduled`/`cancelled`, lowercase) | Appointments, Patient |
| Call ends | `call_summaries` **(new table — run the SQL)**; falls back to a `conversation_turns` `system` row `CALL_SUMMARY {json}` | Patient → Calls → summary card |

**Value casing:** the dashboard's filters are hard‑coded lowercase
(`referrals.status == "pending"`, `appointments.status == "scheduled"`,
`emergency_cases.status == "active"`, `urgency in ("high","emergency")`). The
Supabase project has **no CHECK constraints** on these, so Voxera now writes the
exact lowercase values the dashboard expects.

## One‑time setup

1. **SQL** — run [`sql/2026_voxera_dashboard_integration.sql`](sql/2026_voxera_dashboard_integration.sql)
   once in the Supabase SQL editor. It adds `call_summaries`, `prescriptions`,
   `patient_medications` (additive only). Until then, call summaries persist as a
   `system` conversation turn and the dashboard reads that fallback.
2. **Receiving facility** — the dashboard is facility‑scoped. Create a
   `hospital_users` row for your demo login (`user_id` = the Supabase Auth user,
   `facility_id` = a facility). Voxera auto‑targets the first `hospital_users`
   facility; override with `VOXERA_RECEIVING_FACILITY_ID` in
   [`.env`](.env.example).
3. **Backend `.env`** (`voxera-ai/.env`): `SUPABASE_URL` +
   `SUPABASE_SERVICE_ROLE_KEY`.
4. **Frontend `.env.local`** (`dashboard/.env.local`, see
   [`dashboard/.env.example`](dashboard/.env.example)): `NEXT_PUBLIC_SUPABASE_URL`
   (same project) + `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`. **Never** put the
   service‑role key here.

## Run the demo

```bash
# terminal 1 — dashboard
cd dashboard && npm install && npm run dev        # http://localhost:3000

# terminal 2 — patient call
python voxera.py                                  # talk; Ctrl+C to hang up

# terminal 3 — hospital outbound appointment call
python voxera_hospital.py --seed
```

Staff logs in → sees the call under **Voxera Calls** and on the patient page
(transcript + structured summary + care/OTC block). Say *"my chest is really
tight"* → an **Urgent referral + emergency case + realtime popup** appear.

## Prescriptions (Phase 6)

The dashboard has **no** prescription UI or table today. Voxera's OTC guidance is
**not** a prescription and is never written as one — it lives only inside
`call_summaries.summary_json.otc_guidance`, labelled *"OTC guidance (not a
prescription)"*, with **no numeric dose** ever. The SQL file proposes minimal
`prescriptions` (clinician‑only, `source` fixed to `'clinician'`) and
`patient_medications` (`source in ('patient_reported','clinician','pharmacy')`)
tables for the dashboard team to build a prescribing screen against. Voxera would
only ever *read* clinician prescriptions for future call context, and only
`patient_medications` with `source='patient_reported'` could be Voxera‑written.

## Not done / needs the dashboard team

- Prescribing screen + `prescriptions` writes (schema proposed, not built).
- `ai_assessments` is written per emergency; the referral‑detail page reads it —
  non‑emergency assessments are not generated.
- `facility_beds` / `follow_ups` are staff‑entered; Voxera doesn't touch them.
- Dashboard metrics on the Overview page are already Supabase‑derived; the new
  **Calls** page adds call‑analytics tiles (total, emergencies, completed, avg AI
  replies) from real `calls` rows.

## Dashboard v2 (redesign, patient editing, prescriptions)

Run **`sql/2026_dashboard_v2.sql`** once in the Supabase SQL Editor. It supersedes
`sql/2026_voxera_dashboard_integration.sql` and `sql/2026_dashboard_read_policies.sql` and adds:

- staff read access to calls, transcripts, summaries and events
- `prescriptions` (clinician-only, `source = 'clinician'`) and `patient_medications`
- clinical columns on `patients` (blood group, allergies, chronic conditions, emergency contact, notes)
- staff permission to edit patients, resolve emergency cases and write audit events

Dashboard pages: Command Center (prioritised "needs action now" queue), Emergency board,
Patients (search + filters), Patient record (edit details, calls & transcripts, prescriptions,
referrals, appointments), Calls + call detail, Referral triage queue.
Voxera OTC guidance, patient-reported medicines and clinician prescriptions are always shown separately.
