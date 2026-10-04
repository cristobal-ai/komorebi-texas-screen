-- Phase 4 layer 1: transmission distances (OpenStreetMap / HIFLD). One row per plant; written by the pipeline with the
-- secret key (pipeline/phase_4_geo/load.py), read by allowlisted users. Column list must match
-- pipeline/phase_4_geo/load.py TRANSMISSION_COLUMNS (a test checks).
create table public.layers_transmission (
  eia_id                   integer primary key references public.plants (eia_id) on delete cascade,
  dist_345kv_sub_mi        double precision,   -- edge-to-edge miles to the nearest substation tagged >= 345 kV (scored, Section D)
  nearest_345kv_sub_name   text,
  dist_345kv_line_mi       double precision,   -- nearest line >= 345 kV (fallback when substations are untagged)
  dist_138kv_line_mi       double precision,
  kv_classes_within_near   text,               -- voltage classes (kV, ';'-joined) of lines within near_miles (config)
  max_kv_within_near       double precision,
  poi_kv_seen              boolean,            -- a line at the EIA POI voltage runs within near_miles; null = no EIA POI voltage
  transmission_source      text,               -- osm | hifld lines + osm substations
  transmission_fetched     date,
  transmission_confidence  text check (transmission_confidence in ('low', 'medium')),
  run_id                   text not null,
  loaded_at                timestamptz not null default now()
);

alter table public.layers_transmission enable row level security;
create policy "allowlisted users read layers_transmission" on public.layers_transmission
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.layers_transmission is
  'Phase 4 transmission layer: distance to 345 kV substations/lines from OpenStreetMap (or HIFLD lines). A screen, not an interconnection study.';
