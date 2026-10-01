-- Move the allowlist helper out of the API-exposed public schema (Supabase advisor 0029:
-- signed-in users could call public.is_allowlisted via /rest/v1/rpc). Policies are recreated to use it.

create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;

create or replace function private.is_allowlisted(required_role text default null)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.users_allowlist a
    where a.email = lower(coalesce((select auth.jwt() ->> 'email'), ''))
      and (required_role is null or a.role = required_role)
  );
$$;
revoke all on function private.is_allowlisted(text) from public, anon;
grant execute on function private.is_allowlisted(text) to authenticated;

drop policy "allowlisted users see their own row" on public.users_allowlist;
drop policy "admins manage the allowlist" on public.users_allowlist;
drop policy "allowlisted users read plants" on public.plants;

create policy "allowlisted users see their own row" on public.users_allowlist
  for select to authenticated
  using (email = lower(coalesce((select auth.jwt() ->> 'email'), '')) or (select private.is_allowlisted('admin')));
create policy "admins manage the allowlist" on public.users_allowlist
  for all to authenticated
  using ((select private.is_allowlisted('admin')))
  with check ((select private.is_allowlisted('admin')));
create policy "allowlisted users read plants" on public.plants
  for select to authenticated
  using ((select private.is_allowlisted()));

drop function public.is_allowlisted(text);
