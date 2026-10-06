-- Phase 4 layer 9: load-pocket proximity (Section D, 5 pts) from the hand-maintained data/load_pockets.csv
-- (ERCOT's large-load queue is not a dataset until the PUCT transparency rule). load_pocket_confidence is always
-- 'manual'. One row per plant; written by the pipeline (pipeline/phase_4_geo/load.py LOAD_POCKET_COLUMNS, a test
-- checks), read by allowlisted users.
create table public.layers_load_pocket (
  eia_id                           integer primary key references public.plants (eia_id) on delete cascade,
  dist_load_pocket_firm_mi         double precision,   -- nearest operating / under-construction project; null = none within search radius
  nearest_firm_project             text,
  nearest_firm_kind                text,
  nearest_firm_status              text,
  nearest_firm_mw                  double precision,   -- published MW (first phase or operating); null = not published
  dist_load_pocket_announced_mi    double precision,   -- nearest announced project (scores at the announced factor)
  nearest_announced_project        text,
  nearest_announced_kind           text,
  nearest_announced_mw             double precision,
  n_projects_within_search         integer,
  mw_within_search                 double precision,   -- sum of published MW within the search radius
  projects_within_search           text,               -- 'name (status, MW, x mi); ...' nearest first
  load_pocket_location_confidence  text check (load_pocket_location_confidence in ('site', 'city', 'county')),
  load_pocket_search_mi            double precision,
  load_pocket_source               text,
  load_pocket_table_date           date,
  load_pocket_confidence           text check (load_pocket_confidence in ('manual')),
  run_id                           text not null,
  loaded_at                        timestamptz not null default now()
);

alter table public.layers_load_pocket enable row level security;
create policy "allowlisted users read layers_load_pocket" on public.layers_load_pocket
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.layers_load_pocket is
  'Phase 4 load-pocket proximity: distance to the nearest data center or large flexible load (>= 75 MW) in the hand-maintained table. Manual, not ERCOT queue data.';
