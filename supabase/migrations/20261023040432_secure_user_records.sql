-- Coordinated release: verified JWT API and anonymous sessions must be ready.
-- Existing text user_id values and rows are deliberately preserved.
alter table public.mood_records add column if not exists client_request_id uuid;
create unique index if not exists mood_records_user_request_key
  on public.mood_records (user_id, client_request_id);
create index if not exists mood_records_user_created_idx
  on public.mood_records (user_id, created_at desc) where is_deleted = false;
create index if not exists user_profiles_user_created_idx
  on public.user_profiles (user_id, created_at desc);

alter table public.mood_records enable row level security;
alter table public.user_profiles enable row level security;
drop policy if exists "allow all" on public.mood_records;
drop policy if exists "allow all" on public.user_profiles;

-- Restrictive guard also constrains any other permissive policies on these tables.
create policy records_owner_guard on public.mood_records as restrictive
  for all to public
  using ((select auth.uid())::text = user_id)
  with check ((select auth.uid())::text = user_id);
create policy records_owner_access on public.mood_records
  for all to authenticated
  using ((select auth.uid())::text = user_id)
  with check ((select auth.uid())::text = user_id);
create policy profiles_owner_guard on public.user_profiles as restrictive
  for all to public
  using ((select auth.uid())::text = user_id)
  with check ((select auth.uid())::text = user_id);
create policy profiles_owner_access on public.user_profiles
  for all to authenticated
  using ((select auth.uid())::text = user_id)
  with check ((select auth.uid())::text = user_id);

revoke all on public.mood_records, public.user_profiles from anon;
revoke delete, truncate, references, trigger on public.mood_records, public.user_profiles from authenticated;
grant select, insert, update on public.mood_records, public.user_profiles to authenticated;
grant usage, select on sequence public.mood_records_id_seq, public.user_profiles_id_seq to authenticated;
