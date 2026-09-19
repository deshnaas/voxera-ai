-- ============================================================
-- VOXERA HOSPITAL DASHBOARD  — v2 database setup  (RUN THIS ONE)
-- ============================================================
-- Paste the whole file into Supabase -> SQL Editor -> Run.
-- Idempotent: safe to run more than once. Additive only: it never drops
-- or rewrites your data.
--
-- It supersedes sql/2026_voxera_dashboard_integration.sql and
-- sql/2026_dashboard_read_policies.sql (you can skip those if you run this).
--
-- What it fixes / adds
--   1. Staff could not INSERT/READ referral_events   -> audit trail was lost
--   2. Staff could not edit patients                 -> "Edit details" blocked
--   3. Staff could not resolve emergencies           -> "Mark resolved" blocked
--   4. New tables: call_summaries, prescriptions, patient_medications
--   5. Extra clinical fields on patients (allergies, blood group, ...)
--   6. Realtime publication for live dashboard updates
--
-- Security model: everything below is granted to *hospital staff only*
-- (an authenticated user that has a row in public.hospital_users).
-- Voxera writes with the SERVICE-ROLE key (bypasses RLS) and is unaffected.
-- ============================================================


-- ------------------------------------------------------------
-- 0. helper: is the current user hospital staff?
-- ------------------------------------------------------------
create or replace function public.is_hospital_staff()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.hospital_users hu where hu.user_id = auth.uid()
  );
$$;

grant execute on function public.is_hospital_staff() to authenticated;


-- ------------------------------------------------------------
-- 1. New tables
-- ------------------------------------------------------------

-- 1a. one structured summary per finished Voxera call
create table if not exists public.call_summaries (
    id                 uuid primary key default gen_random_uuid(),
    call_id            uuid not null references public.calls(id) on delete cascade,
    patient_id         uuid references public.patients(id) on delete set null,
    chief_concern      text,
    summary_json       jsonb not null default '{}'::jsonb,
    summary_text       text,
    emergency_detected boolean not null default false,
    call_outcome       text,
    created_at         timestamptz not null default now()
);
create unique index if not exists call_summaries_call_id_key
    on public.call_summaries(call_id);
create index if not exists call_summaries_patient_id_idx
    on public.call_summaries(patient_id);

-- 1b. CLINICIAN prescriptions (created from the dashboard only).
--     Voxera never writes here. Voxera's OTC guidance is NOT a prescription.
create table if not exists public.prescriptions (
    id                 uuid primary key default gen_random_uuid(),
    patient_id         uuid not null references public.patients(id) on delete cascade,
    facility_id        uuid references public.facilities(id) on delete set null,
    referral_id        uuid references public.referrals(id) on delete set null,
    call_id            uuid references public.calls(id) on delete set null,
    medication_name    text not null,
    dosage             text,
    route              text,
    frequency          text,
    duration           text,
    instructions       text,
    prescribed_by      text,
    prescribed_by_user uuid,
    status             text not null default 'active',   -- active | completed | stopped
    source             text not null default 'clinician' check (source = 'clinician'),
    prescribed_at      timestamptz not null default now(),
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);
create index if not exists prescriptions_patient_id_idx
    on public.prescriptions(patient_id, prescribed_at desc);

-- 1c. medication history (patient-reported / clinician / pharmacy)
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
-- 2. Extra clinical fields on patients  (all optional)
-- ------------------------------------------------------------
alter table public.patients add column if not exists blood_group             text;
alter table public.patients add column if not exists allergies               text;
alter table public.patients add column if not exists chronic_conditions      text;
alter table public.patients add column if not exists emergency_contact_name  text;
alter table public.patients add column if not exists emergency_contact_phone text;
alter table public.patients add column if not exists clinical_notes          text;


-- ------------------------------------------------------------
-- 3. Row Level Security — hospital staff only
--    (policies are additive; they never remove an existing policy)
-- ------------------------------------------------------------

-- read access to everything Voxera writes ------------------------------
do $$
declare t text;
begin
  foreach t in array array[
    'calls','conversation_turns','call_summaries','referral_events',
    'prescriptions','patient_medications','patients','ai_assessments'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists "staff read %s" on public.%I', t, t);
    execute format(
      'create policy "staff read %s" on public.%I for select to authenticated using (public.is_hospital_staff())',
      t, t);
  end loop;
end $$;

-- audit trail: staff may append referral events -------------------------
drop policy if exists "staff insert referral_events" on public.referral_events;
create policy "staff insert referral_events" on public.referral_events
  for insert to authenticated with check (public.is_hospital_staff());

-- edit patient details ---------------------------------------------------
drop policy if exists "staff update patients" on public.patients;
create policy "staff update patients" on public.patients
  for update to authenticated
  using (public.is_hospital_staff()) with check (public.is_hospital_staff());

-- resolve / update emergency cases --------------------------------------
alter table public.emergency_cases enable row level security;
drop policy if exists "staff update emergency_cases" on public.emergency_cases;
create policy "staff update emergency_cases" on public.emergency_cases
  for update to authenticated
  using (public.is_hospital_staff()) with check (public.is_hospital_staff());

-- prescriptions: create / update by staff -------------------------------
drop policy if exists "staff insert prescriptions" on public.prescriptions;
create policy "staff insert prescriptions" on public.prescriptions
  for insert to authenticated with check (public.is_hospital_staff());
drop policy if exists "staff update prescriptions" on public.prescriptions;
create policy "staff update prescriptions" on public.prescriptions
  for update to authenticated
  using (public.is_hospital_staff()) with check (public.is_hospital_staff());

-- medication history: staff may add entries -----------------------------
drop policy if exists "staff insert patient_medications" on public.patient_medications;
create policy "staff insert patient_medications" on public.patient_medications
  for insert to authenticated with check (public.is_hospital_staff());
drop policy if exists "staff update patient_medications" on public.patient_medications;
create policy "staff update patient_medications" on public.patient_medications
  for update to authenticated
  using (public.is_hospital_staff()) with check (public.is_hospital_staff());


-- ------------------------------------------------------------
-- 4. Realtime: live updates on the dashboard
--    (the dashboard also polls every 20 s, so this is an accelerator)
-- ------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array[
    'emergency_cases','referrals','referral_notifications','calls',
    'conversation_turns','appointments','prescriptions'
  ] loop
    begin
      execute format('alter publication supabase_realtime add table public.%I', t);
    exception
      when duplicate_object then null;   -- already published
      when undefined_object then null;   -- publication missing
    end;
  end loop;
end $$;

-- done.
