-- ============================================================
-- VOXERA PATIENT INTELLIGENCE  — database setup   (RUN THIS ONE)
-- ============================================================
-- Paste the whole file into Supabase -> SQL Editor -> Run.
-- Idempotent (safe to re-run) and additive: it never drops a table or your data.
--
-- SELF-CONTAINED: it includes everything from sql/2026_dashboard_v2.sql that the
-- dashboard needs, so you only need to run THIS file. (If you already ran v2 this
-- is still safe.) It supersedes:
--     sql/2026_voxera_dashboard_integration.sql
--     sql/2026_dashboard_read_policies.sql
--     sql/2026_dashboard_v2.sql
--
-- What it does
--   1. patients.patient_id  human-facing ID (VX-000123), auto-assigned + backfilled, unique, indexed
--   2. clinical columns on patients (allergies, blood group, ...) + fast search indexes
--   3. call_summaries, prescriptions (clinician-only), patient_medications (with provenance)
--   4. medical_documents + prescription_items  (OCR candidates, NEVER active until a clinician confirms)
--   5. patient_record_access_logs  (append-only audit; service-role only)
--   6. Row Level Security scoped to the staff member's OWN facility and role
--   7. private Storage bucket for uploaded prescriptions
--
-- Security model
--   * Voxera + the Patient-AI API write with the SERVICE-ROLE key (bypasses RLS).
--   * The browser uses the publishable key and is confined by the policies below.
--   * A patient ID alone never grants access. Cross-facility reads go through the
--     Patient-AI API (policy + reason + audit); RLS gives other facilities NOTHING.
--   * hospital_users.role: admin | doctor | nurse | receptionist | operator.
--     The legacy value 'staff' (all existing demo users) is treated as 'doctor'.
-- ============================================================


-- ------------------------------------------------------------
-- 0. extensions
-- ------------------------------------------------------------
create extension if not exists pg_trgm;


-- ------------------------------------------------------------
-- 1. patients: human-facing patient_id  (VX-000123)
-- ------------------------------------------------------------
create sequence if not exists public.patient_code_seq start 1;

alter table public.patients add column if not exists patient_id text;

-- backfill existing patients in creation order (only rows still NULL)
do $$
declare r record;
begin
  for r in select id from public.patients where patient_id is null order by created_at, id loop
    update public.patients
       set patient_id = 'VX-' || lpad(nextval('public.patient_code_seq')::text, 6, '0')
     where id = r.id;
  end loop;
end $$;

create or replace function public.assign_patient_code()
returns trigger
language plpgsql
as $$
begin
  if new.patient_id is null or btrim(new.patient_id) = '' then
    new.patient_id := 'VX-' || lpad(nextval('public.patient_code_seq')::text, 6, '0');
  else
    new.patient_id := upper(btrim(new.patient_id));
  end if;
  return new;
end $$;

drop trigger if exists patients_assign_code on public.patients;
create trigger patients_assign_code
  before insert on public.patients
  for each row execute function public.assign_patient_code();

alter table public.patients alter column patient_id set not null;
create unique index if not exists patients_patient_id_key on public.patients(patient_id);


-- ------------------------------------------------------------
-- 2. patients: clinical columns + search indexes
-- ------------------------------------------------------------
alter table public.patients add column if not exists blood_group             text;
alter table public.patients add column if not exists allergies               text;
alter table public.patients add column if not exists chronic_conditions      text;
alter table public.patients add column if not exists emergency_contact_name  text;
alter table public.patients add column if not exists emergency_contact_phone text;
alter table public.patients add column if not exists clinical_notes          text;

-- last 10 digits of the phone, for normalised lookup
alter table public.patients add column if not exists phone_digits text
  generated always as (right(regexp_replace(coalesce(phone, ''), '\D', '', 'g'), 10)) stored;

create index if not exists patients_phone_digits_idx on public.patients(phone_digits);
create index if not exists patients_name_trgm_idx    on public.patients using gin (full_name gin_trgm_ops);

-- relationship lookups used by the access policy and RLS
create index if not exists calls_patient_idx           on public.calls(patient_id);
create index if not exists referrals_patient_idx       on public.referrals(patient_id);
create index if not exists appointments_patient_idx    on public.appointments(patient_id);
create index if not exists emergency_cases_patient_idx on public.emergency_cases(patient_id);


-- ------------------------------------------------------------
-- 3. Voxera / clinician tables
-- ------------------------------------------------------------

-- 3a. one structured summary per finished Voxera call
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
create unique index if not exists call_summaries_call_id_key on public.call_summaries(call_id);
create index if not exists call_summaries_patient_id_idx     on public.call_summaries(patient_id);

-- 3b. CLINICIAN prescriptions (dashboard only; one row per medicine).
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
create index if not exists prescriptions_patient_id_idx on public.prescriptions(patient_id, prescribed_at desc);

-- 3c. uploaded documents (original file lives in Storage; text is what OCR read)
create table if not exists public.medical_documents (
    id             uuid primary key default gen_random_uuid(),
    patient_id     uuid not null references public.patients(id) on delete cascade,
    facility_id    uuid references public.facilities(id) on delete set null,
    document_type  text not null default 'prescription',
    storage_path   text,
    filename       text,
    extracted_text text,
    ocr_metadata   jsonb not null default '{}'::jsonb,
    ocr_status     text not null default 'pending',        -- pending | done | low_confidence | failed
    uploaded_by    uuid,
    created_at     timestamptz not null default now()
);
create index if not exists medical_documents_patient_idx on public.medical_documents(patient_id, created_at desc);

-- 3d. OCR-extracted medicine CANDIDATES. Never active by themselves.
create table if not exists public.prescription_items (
    id                  uuid primary key default gen_random_uuid(),
    document_id         uuid references public.medical_documents(id) on delete cascade,
    patient_id          uuid not null references public.patients(id) on delete cascade,
    medicine_name       text,
    generic_name        text,
    strength            text,
    form                text,
    route               text,
    frequency           text,
    duration            text,
    instructions        text,
    confidence          numeric,
    raw_text            text,
    warnings            jsonb not null default '[]'::jsonb,
    source              text not null default 'ocr' check (source = 'ocr'),
    verification_status text not null default 'pending'
                        check (verification_status in ('pending','needs_review','confirmed','rejected')),
    verified_by         uuid,
    verified_at         timestamptz,
    created_at          timestamptz not null default now()
);
create index if not exists prescription_items_patient_idx  on public.prescription_items(patient_id, created_at desc);
create index if not exists prescription_items_document_idx on public.prescription_items(document_id);

-- 3e. the medication record, with mandatory provenance.
--     Raw 'ocr' is NOT an allowed source here: an OCR candidate becomes a medication
--     only as 'ocr_verified', and only with a named verifier.
create table if not exists public.patient_medications (
    id                 uuid primary key default gen_random_uuid(),
    patient_id         uuid not null references public.patients(id) on delete cascade,
    call_id            uuid references public.calls(id) on delete set null,
    facility_id        uuid references public.facilities(id) on delete set null,
    medicine_name      text not null,
    generic_name       text,
    strength           text,
    form               text,
    route              text,
    frequency          text,
    duration           text,
    instructions       text,
    source             text not null default 'patient_reported',
    status             text not null default 'active',      -- active | stopped | completed
    source_document_id uuid references public.medical_documents(id) on delete set null,
    source_item_id     uuid references public.prescription_items(id) on delete set null,
    verified_by        uuid,
    verified_at        timestamptz,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);
-- if an older shape of this table already exists, bring it up to date
alter table public.patient_medications add column if not exists medicine_name text;
alter table public.patient_medications add column if not exists generic_name text;
alter table public.patient_medications add column if not exists strength text;
alter table public.patient_medications add column if not exists form text;
alter table public.patient_medications add column if not exists route text;
alter table public.patient_medications add column if not exists frequency text;
alter table public.patient_medications add column if not exists duration text;
alter table public.patient_medications add column if not exists instructions text;
alter table public.patient_medications add column if not exists status text not null default 'active';
alter table public.patient_medications add column if not exists facility_id uuid references public.facilities(id) on delete set null;
alter table public.patient_medications add column if not exists source_document_id uuid references public.medical_documents(id) on delete set null;
alter table public.patient_medications add column if not exists source_item_id uuid references public.prescription_items(id) on delete set null;
alter table public.patient_medications add column if not exists verified_by uuid;
alter table public.patient_medications add column if not exists verified_at timestamptz;
alter table public.patient_medications add column if not exists updated_at timestamptz not null default now();

alter table public.patient_medications drop constraint if exists patient_medications_source_check;
alter table public.patient_medications add constraint patient_medications_source_check
  check (source in ('clinician','ocr_verified','patient_reported','imported'));
alter table public.patient_medications drop constraint if exists patient_medications_verified_needs_verifier;
alter table public.patient_medications add constraint patient_medications_verified_needs_verifier
  check (source <> 'ocr_verified' or (verified_by is not null and verified_at is not null));
create index if not exists patient_medications_patient_id_idx on public.patient_medications(patient_id, created_at desc);

-- 3f. access audit — append-only; contains who/where/why, never clinical content
create table if not exists public.patient_record_access_logs (
    id                  uuid primary key default gen_random_uuid(),
    patient_id          uuid not null references public.patients(id) on delete cascade,
    accessed_by_user_id uuid,
    facility_id         uuid,
    role                text,
    access_reason       text,
    accessed_at         timestamptz not null default now(),
    source              text not null default 'dashboard',   -- dashboard | call | api
    action              text,                                 -- search | open_record | ask_question | upload | verify | denied
    relationship        text,                                 -- home | external
    granted             boolean,
    sections            jsonb not null default '[]'::jsonb,
    call_id             uuid,
    meta                jsonb not null default '{}'::jsonb
);
create index if not exists access_logs_patient_idx on public.patient_record_access_logs(patient_id, accessed_at desc);
create index if not exists access_logs_user_idx    on public.patient_record_access_logs(accessed_by_user_id, accessed_at desc);

-- audit rows can never be changed or removed, even by mistake
create or replace function public.audit_log_immutable()
returns trigger
language plpgsql
as $$
begin
  raise exception 'patient_record_access_logs is append-only';
end $$;

drop trigger if exists access_logs_no_update on public.patient_record_access_logs;
create trigger access_logs_no_update
  before update or delete on public.patient_record_access_logs
  for each row execute function public.audit_log_immutable();


-- ------------------------------------------------------------
-- 4. access helper functions (SECURITY DEFINER so RLS can call them)
-- ------------------------------------------------------------
create or replace function public.is_hospital_staff()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.hospital_users hu where hu.user_id = auth.uid());
$$;

create or replace function public.is_my_facility(fid uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select fid is not null and exists (
    select 1 from public.hospital_users hu where hu.user_id = auth.uid() and hu.facility_id = fid);
$$;

-- does one of MY facilities have a care relationship with this patient?
--   roles: null = any staff role;  otherwise a list of effective roles ('staff' counts as 'doctor')
create or replace function public.patient_access(pid uuid, roles text[])
returns boolean language sql stable security definer set search_path = public as $$
  select pid is not null and exists (
    select 1 from public.hospital_users hu
    where hu.user_id = auth.uid()
      and (roles is null or (case when lower(hu.role) = 'staff' then 'doctor' else lower(hu.role) end) = any (roles))
      and (
        exists (select 1 from public.referrals r         where r.patient_id = pid and r.receiving_facility_id = hu.facility_id)
     or exists (select 1 from public.appointments a      where a.patient_id = pid and a.facility_id = hu.facility_id)
     or exists (select 1 from public.emergency_cases e   where e.patient_id = pid and e.facility_id = hu.facility_id)
     or exists (select 1 from public.calls c             where c.patient_id = pid and c.facility_id = hu.facility_id)
      ));
$$;

create or replace function public.call_access(cid uuid, roles text[])
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.calls c
    where c.id = cid and public.patient_access(c.patient_id, roles));
$$;

grant execute on function public.is_hospital_staff()               to authenticated;
grant execute on function public.is_my_facility(uuid)              to authenticated;
grant execute on function public.patient_access(uuid, text[])      to authenticated;
grant execute on function public.call_access(uuid, text[])         to authenticated;


-- ------------------------------------------------------------
-- 5. Row Level Security
--    Old broad policies on these tables are removed first so that nothing
--    left over can widen access; the scoped policies below replace them.
-- ------------------------------------------------------------
do $$
declare r record; t text;
begin
  foreach t in array array[
    'patients','calls','conversation_turns','call_summaries','prescriptions',
    'patient_medications','prescription_items','medical_documents','patient_record_access_logs'
  ] loop
    for r in select policyname from pg_policies where schemaname = 'public' and tablename = t loop
      execute format('drop policy %I on public.%I', r.policyname, t);
    end loop;
    execute format('alter table public.%I enable row level security', t);
  end loop;
end $$;

-- patients: identity is visible to staff of a facility that has a relationship with the patient
create policy "staff read patients" on public.patients
  for select to authenticated using (public.patient_access(id, null));
create policy "staff update patients" on public.patients
  for update to authenticated
  using (public.patient_access(id, null)) with check (public.patient_access(id, null));

-- calls (metadata): any staff role at the call's facility, or a related patient's facility
create policy "staff read calls" on public.calls
  for select to authenticated
  using (public.is_my_facility(facility_id) or (facility_id is null and public.patient_access(patient_id, null)));

-- transcripts / summaries / prescriptions: clinical roles only
create policy "clinical read conversation_turns" on public.conversation_turns
  for select to authenticated using (public.call_access(call_id, array['doctor','nurse']));
create policy "clinical read call_summaries" on public.call_summaries
  for select to authenticated using (public.call_access(call_id, array['doctor','nurse']));

create policy "clinical read prescriptions" on public.prescriptions
  for select to authenticated using (public.patient_access(patient_id, array['doctor','nurse']));
create policy "doctor insert prescriptions" on public.prescriptions
  for insert to authenticated with check (public.patient_access(patient_id, array['doctor']));
create policy "doctor update prescriptions" on public.prescriptions
  for update to authenticated
  using (public.patient_access(patient_id, array['doctor']))
  with check (public.patient_access(patient_id, array['doctor']));

-- patient_medications, prescription_items, medical_documents, patient_record_access_logs:
--   RLS is ON and there are deliberately NO policies -> only the Patient-AI API
--   (service role) can touch them, so role + cross-facility rules are enforced in one place.

-- other tables the dashboard already uses (additive, scoped where a facility column exists)
alter table public.referral_events enable row level security;
drop policy if exists "staff read referral_events"   on public.referral_events;
drop policy if exists "staff insert referral_events" on public.referral_events;
create policy "staff read referral_events" on public.referral_events
  for select to authenticated using (public.is_hospital_staff());
create policy "staff insert referral_events" on public.referral_events
  for insert to authenticated with check (public.is_hospital_staff());

alter table public.ai_assessments enable row level security;
drop policy if exists "staff read ai_assessments" on public.ai_assessments;
create policy "staff read ai_assessments" on public.ai_assessments
  for select to authenticated using (public.is_hospital_staff());

alter table public.emergency_cases enable row level security;
drop policy if exists "staff update emergency_cases" on public.emergency_cases;
create policy "staff update emergency_cases" on public.emergency_cases
  for update to authenticated
  using (public.is_my_facility(facility_id)) with check (public.is_my_facility(facility_id));


-- ------------------------------------------------------------
-- 6. Storage: private bucket for uploaded prescriptions
--    (no public access, no policies: the API uploads with the service role and
--     hands the browser a short-lived signed URL only after an access check)
-- ------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('patient-documents', 'patient-documents', false)
on conflict (id) do nothing;


-- ------------------------------------------------------------
-- 7. Realtime (accelerator; the dashboard also polls)
-- ------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['emergency_cases','referrals','referral_notifications','calls',
                           'conversation_turns','appointments','prescriptions'] loop
    begin
      execute format('alter publication supabase_realtime add table public.%I', t);
    exception
      when duplicate_object then null;
      when undefined_object then null;
      when undefined_table  then null;
    end;
  end loop;
end $$;


-- ------------------------------------------------------------
-- 8. OPTIONAL — real roles instead of the legacy 'staff'
--    (uncomment and edit; 'staff' keeps working and is treated as 'doctor')
-- ------------------------------------------------------------
-- update public.hospital_users set role = 'doctor'
--  where user_id = (select id from auth.users where email = 'hospital.a@voxera.com');
-- update public.hospital_users set role = 'nurse'
--  where user_id = (select id from auth.users where email = 'nurse@yourhospital.example');

-- done.
