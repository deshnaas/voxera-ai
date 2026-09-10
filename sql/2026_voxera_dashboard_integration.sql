-- ============================================================
-- VOXERA <-> HOSPITAL DASHBOARD  — schema additions
-- ============================================================
-- Run this ONCE in the Supabase SQL editor of the shared project.
-- It is additive only. It does NOT modify any table Voxera or the
-- dashboard already use.
--
-- Tables the dashboard already expects and that ALREADY EXIST in the
-- shared project (no action needed): patients, calls, conversation_turns,
-- referrals, referral_events, appointments, facilities, facility_beds,
-- hospital_users, emergency_cases, ai_assessments, referral_notifications,
-- follow_ups.
--
-- New here:
--   call_summaries        - one structured summary per finished call
--   prescriptions         - CLINICIAN e-prescriptions (dashboard-created)
--   patient_medications   - medication history (multiple sources)
-- ============================================================


-- ------------------------------------------------------------
-- 1. call_summaries
--    Written by voxera_summary.persist_summary() at the end of every call.
--    If this table is absent, Voxera falls back to a conversation_turns
--    row (speaker='system', message starts with 'CALL_SUMMARY ') so the
--    dashboard can still read it — but this table is cleaner.
-- ------------------------------------------------------------
create table if not exists public.call_summaries (
    id                uuid primary key default gen_random_uuid(),
    call_id           uuid not null references public.calls(id) on delete cascade,
    patient_id        uuid references public.patients(id) on delete set null,
    chief_concern     text,
    summary_json      jsonb not null default '{}'::jsonb,
    summary_text      text,
    emergency_detected boolean not null default false,
    call_outcome      text,
    created_at        timestamptz not null default now()
);

create unique index if not exists call_summaries_call_id_key
    on public.call_summaries(call_id);
create index if not exists call_summaries_patient_id_idx
    on public.call_summaries(patient_id);


-- ------------------------------------------------------------
-- 2. prescriptions   (CLINICIAN-created only)
--    Voxera NEVER writes here. Voxera's OTC guidance is NOT a
--    prescription and lives only inside call_summaries.otc_guidance.
--    `source` is fixed to 'clinician' as a guard.
-- ------------------------------------------------------------
create table if not exists public.prescriptions (
    id               uuid primary key default gen_random_uuid(),
    patient_id       uuid not null references public.patients(id) on delete cascade,
    facility_id      uuid references public.facilities(id) on delete set null,
    referral_id      uuid references public.referrals(id) on delete set null,
    prescribed_by    text,                       -- doctor name / staff id
    prescribed_by_user uuid,                     -- auth.users id (dashboard session)
    medication_name  text not null,
    dosage           text,                       -- e.g. "500 mg"
    frequency        text,                       -- e.g. "twice daily"
    duration         text,                       -- e.g. "5 days"
    instructions     text,
    status           text not null default 'active',   -- active | completed | cancelled
    source           text not null default 'clinician'
                     check (source = 'clinician'),
    prescribed_at    timestamptz not null default now(),
    created_at       timestamptz not null default now()
);

create index if not exists prescriptions_patient_id_idx
    on public.prescriptions(patient_id);


-- ------------------------------------------------------------
-- 3. patient_medications   (longitudinal medication list)
--    `source` distinguishes what Voxera heard from what a clinician set.
--      patient_reported  - captured by Voxera during a call (facts.medications)
--      clinician         - added via the dashboard
--      pharmacy          - dispensing record
--    Voxera MAY write rows with source='patient_reported' in future;
--    it must never write 'clinician'.
-- ------------------------------------------------------------
create table if not exists public.patient_medications (
    id               uuid primary key default gen_random_uuid(),
    patient_id       uuid not null references public.patients(id) on delete cascade,
    call_id          uuid references public.calls(id) on delete set null,
    medication_name  text not null,
    source           text not null default 'patient_reported'
                     check (source in ('patient_reported','clinician','pharmacy')),
    notes            text,
    active           boolean not null default true,
    recorded_at      timestamptz not null default now()
);

create index if not exists patient_medications_patient_id_idx
    on public.patient_medications(patient_id);


-- ------------------------------------------------------------
-- 4. Row Level Security  (uncomment to match your other tables)
-- ------------------------------------------------------------
-- alter table public.call_summaries      enable row level security;
-- alter table public.prescriptions       enable row level security;
-- alter table public.patient_medications enable row level security;
--
-- -- dashboard (publishable key / signed-in staff) may read everything:
-- create policy "staff read call_summaries" on public.call_summaries
--   for select using (auth.role() = 'authenticated');
-- create policy "staff read prescriptions" on public.prescriptions
--   for select using (auth.role() = 'authenticated');
-- create policy "staff write prescriptions" on public.prescriptions
--   for insert with check (auth.role() = 'authenticated');
-- create policy "staff read patient_medications" on public.patient_medications
--   for select using (auth.role() = 'authenticated');
--
-- The Voxera backend uses the SERVICE-ROLE key and bypasses RLS, so no
-- write policy is needed for it.
