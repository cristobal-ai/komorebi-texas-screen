-- Private bucket holding the pipeline's built state (Phase 4 layers, Phase 3b metrics) so the monthly GitHub Actions job can
-- load results. Written and read only with the secret key (pipeline/state.py); no policy, so no user session can touch it.
insert into storage.buckets (id, name, public, file_size_limit)
values ('pipeline-state', 'pipeline-state', false, 104857600)
on conflict (id) do nothing;
