-- Phase 6: private Storage bucket for the XLSX each pipeline run writes (web/scripts/export-upload.ts, secret key,
-- bypasses RLS). Allowlisted users may read it (the app's /export/latest signs a URL with the user's session);
-- nobody writes through the API with a user session.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('exports', 'exports', false, 52428800,
        array['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'])
on conflict (id) do nothing;

create policy "allowlisted users read exports" on storage.objects
  for select to authenticated
  using (bucket_id = 'exports' and (select private.is_allowlisted()));
