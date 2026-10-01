-- Advisor 0003 (auth.jwt() re-evaluated per row) and 0006 (two permissive SELECT policies) on users_allowlist:
-- one SELECT policy with the JWT lookup evaluated once; admin write policies split per command.

drop policy "allowlisted users see their own row" on public.users_allowlist;
drop policy "admins manage the allowlist" on public.users_allowlist;

create policy "read own row, admins read all" on public.users_allowlist
  for select to authenticated
  using (email = (select lower(coalesce(auth.jwt() ->> 'email', ''))) or (select private.is_allowlisted('admin')));
create policy "admins insert" on public.users_allowlist
  for insert to authenticated with check ((select private.is_allowlisted('admin')));
create policy "admins update" on public.users_allowlist
  for update to authenticated using ((select private.is_allowlisted('admin'))) with check ((select private.is_allowlisted('admin')));
create policy "admins delete" on public.users_allowlist
  for delete to authenticated using ((select private.is_allowlisted('admin')));
