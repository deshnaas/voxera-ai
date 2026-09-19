-- ============================================================
-- Let logged-in hospital staff READ the Voxera call data
-- (calls / conversation_turns / call_summaries).
-- Run once in the Supabase SQL editor.
--
-- Voxera writes with the SERVICE-ROLE key (bypasses RLS) so it is
-- unaffected. This only opens SELECT for the dashboard's authenticated
-- (publishable-key) sessions.
-- ============================================================

-- --- calls -------------------------------------------------------------
alter table public.calls enable row level security;
drop policy if exists "staff read calls" on public.calls;
create policy "staff read calls"
  on public.calls for select
  to authenticated
  using (true);

-- --- conversation_turns ---------------------------------------------
alter table public.conversation_turns enable row level security;
drop policy if exists "staff read conversation_turns" on public.conversation_turns;
create policy "staff read conversation_turns"
  on public.conversation_turns for select
  to authenticated
  using (true);

-- --- call_summaries (created by 2026_voxera_dashboard_integration.sql) --
-- Safe to run even if the table doesn't exist yet: wrap in a DO block.
do $$
begin
  if to_regclass('public.call_summaries') is not null then
    execute 'alter table public.call_summaries enable row level security';
    execute 'drop policy if exists "staff read call_summaries" on public.call_summaries';
    execute 'create policy "staff read call_summaries" on public.call_summaries
             for select to authenticated using (true)';
  end if;
end $$;

-- If you prefer facility-scoped access instead of "any staff", replace the
-- conversation_turns policy with:
--
--   using (exists (
--     select 1 from public.calls c
--     join public.hospital_users hu on hu.facility_id = c.facility_id
--     where c.id = conversation_turns.call_id and hu.user_id = auth.uid()
--   ))
--
-- (calls without a facility_id would then be hidden.)
