-- Advisor 0003: the linter wants auth.jwt() itself wrapped as (select auth.jwt()) so it is an initplan.
alter policy "read own row, admins read all" on public.users_allowlist
  using (email = lower(coalesce((select auth.jwt()) ->> 'email', '')) or (select private.is_allowlisted('admin')));
