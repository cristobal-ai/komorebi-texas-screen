-- Map overlays: 345 kV substations (OSM, transmission layer cache) and the load-pocket projects (data/load_pockets.csv).
-- Reference points, not per plant; written by pipeline/phase_4_geo/reference.py (columns checked by a test) when the
-- transmission / load_pocket layer is loaded; read by allowlisted users.
create table public.ref_substations_345kv (
  osm_id      text primary key,
  name        text,
  operator    text,
  voltage_kv  double precision,
  lat         double precision not null,
  lon         double precision not null,
  run_id      text not null,
  loaded_at   timestamptz not null default now()
);

create table public.ref_load_pocket_projects (
  project_id           text primary key,
  name                 text not null,
  developer            text,
  kind                 text,
  status               text,
  load_mw              double precision,
  county               text,
  city                 text,
  lat                  double precision not null,
  lon                  double precision not null,
  location_confidence  text,
  qualifies            boolean not null,   -- counts toward the load-pocket score (not cancelled; DC or >= 75 MW)
  source_url           text,
  source_date          text,
  run_id               text not null,
  loaded_at            timestamptz not null default now()
);

alter table public.ref_substations_345kv enable row level security;
alter table public.ref_load_pocket_projects enable row level security;
create policy "allowlisted users read ref_substations_345kv" on public.ref_substations_345kv
  for select to authenticated using ((select private.is_allowlisted()));
create policy "allowlisted users read ref_load_pocket_projects" on public.ref_load_pocket_projects
  for select to authenticated using ((select private.is_allowlisted()));
