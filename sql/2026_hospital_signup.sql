-- ============================================================
-- VOXERA HOSPITAL DASHBOARD — hospital self-signup
-- ============================================================
-- Paste into Supabase -> SQL Editor -> Run. Idempotent: safe to run more than once.
--
-- Lets a new hospital create its own account from the dashboard (Sign up -> Onboarding),
-- instead of an operator inserting `facilities` / `hospital_users` rows by hand.
--
-- SECURITY: this is a shared, multi-tenant project — every hospital's emergencies, referrals
-- and patient data live in the same tables, kept apart only by `hospital_users.facility_id` and
-- the RLS policies that key off it. A hospital "signing itself up" must NEVER be able to insert a
-- `hospital_users` row pointing at facility_id it doesn't own, or every other policy in this schema
-- (2026_dashboard_v2.sql, 2026_voxera_patient_intelligence.sql, ...) is worthless.
-- So the browser is never granted INSERT on `facilities` or `hospital_users` directly. It can only
-- call `register_hospital(...)` below, a SECURITY DEFINER function that:
--   * takes the caller's identity from auth.uid() (never a client-supplied id),
--   * refuses if that account is already linked to a hospital,
--   * creates ONE new facility and ONE hospital_users row (role='admin'), both owned by that user.
-- ============================================================

-- ------------------------------------------------------------
-- 0. is_hospital_staff() — from 2026_dashboard_v2.sql; redefined here too so this file
--    also runs standalone (create or replace is idempotent either way).
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
-- 1. facilities: the columns a signup collects, if not already present
-- ------------------------------------------------------------
alter table public.facilities add column if not exists contact_phone   text;
alter table public.facilities add column if not exists contact_email  text;
alter table public.facilities add column if not exists registration_no text;
alter table public.facilities add column if not exists created_by     uuid references auth.users(id) on delete set null;
alter table public.facilities add column if not exists created_at     timestamptz not null default now();
-- a self-registered hospital is visible and usable right away (operational_status), but flagged
-- unverified until an operator checks it — surfaced as a small banner in the dashboard, not a block.
alter table public.facilities add column if not exists verified       boolean not null default false;

-- ------------------------------------------------------------
-- 2. read access (facilities are read across hospitals for referral routing / bed search;
--    hospital_users is read only by the row's own owner). Additive: does not touch other tables.
-- ------------------------------------------------------------
alter table public.facilities enable row level security;
drop policy if exists "staff read facilities" on public.facilities;
create policy "staff read facilities"
  on public.facilities for select
  to authenticated
  using (public.is_hospital_staff());

alter table public.hospital_users enable row level security;
drop policy if exists "self read hospital_users" on public.hospital_users;
create policy "self read hospital_users"
  on public.hospital_users for select
  to authenticated
  using (user_id = auth.uid());

-- No INSERT/UPDATE policy is added for either table: writes only ever happen through
-- register_hospital() below (as the table owner, bypassing RLS) or Voxera's service-role key.

-- ------------------------------------------------------------
-- 3. register_hospital(...) — the only way a browser session can create a facility
-- ------------------------------------------------------------
create or replace function public.register_hospital(
  p_name            text,
  p_type            text,
  p_location        text,
  p_district        text,
  p_phone           text default null,
  p_email           text default null,
  p_registration_no text default null
)
returns table(facility_id uuid)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uid uuid := auth.uid();
  v_fid uuid;
begin
  if v_uid is null then
    raise exception 'not authenticated';
  end if;
  if exists (select 1 from public.hospital_users where user_id = v_uid) then
    raise exception 'this account is already linked to a hospital';
  end if;
  if coalesce(trim(p_name), '') = '' then
    raise exception 'hospital name is required';
  end if;

  insert into public.facilities
    (name, type, location, district, contact_phone, contact_email, registration_no,
     operational_status, verified, created_by)
  values
    (trim(p_name), nullif(trim(coalesce(p_type, '')), ''), nullif(trim(coalesce(p_location, '')), ''),
     nullif(trim(coalesce(p_district, '')), ''), nullif(trim(coalesce(p_phone, '')), ''),
     nullif(trim(coalesce(p_email, '')), ''), nullif(trim(coalesce(p_registration_no, '')), ''),
     true, false, v_uid)
  returning id into v_fid;

  insert into public.hospital_users (user_id, facility_id, role)
  values (v_uid, v_fid, 'admin');

  return query select v_fid;
end;
$$;

grant execute on function public.register_hospital(text, text, text, text, text, text, text) to authenticated;

-- ------------------------------------------------------------
-- 4. (optional) mark a hospital verified once you've checked it, e.g. its registration number
-- ------------------------------------------------------------
-- update public.facilities set verified = true where id = '...';
