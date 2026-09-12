-- =============================================================================
-- fix-rls-policies.sql — one-time fix for "new row violates row-level security"
--
-- WHY: your live Supabase project's cases/prescriptions/consents tables were
-- created before sql/schema.sql gained its INSERT policies, so every form
-- submission is silently blocked by RLS (error 42501) and the app falls back
-- to session-only mode. These statements add exactly the missing policies.
--
-- HOW: open your project at https://supabase.com → SQL editor → paste ALL of
-- this file → Run. Idempotent: safe to run repeatedly.
-- =============================================================================

-- cases — any authenticated user may submit a case (intake terminal).
drop policy if exists "authenticated insert cases" on public.cases;
create policy "authenticated insert cases" on public.cases
  for insert to authenticated with check (true);

-- prescriptions — any authenticated user may save a prescription.
drop policy if exists "authenticated insert prescriptions" on public.prescriptions;
create policy "authenticated insert prescriptions" on public.prescriptions
  for insert to authenticated with check (true);

-- consents — any authenticated user may record a consent.
drop policy if exists "staff write consents" on public.consents;
create policy "staff write consents" on public.consents
  for insert to authenticated with check (true);

-- Sanity check: after running, this should return one row per table.
select tablename, policyname
from pg_policies
where tablename in ('cases', 'prescriptions', 'consents')
  and cmd = 'INSERT'
order by tablename;