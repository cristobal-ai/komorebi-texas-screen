-- Phase 4 layer 4: FEMA NFHL flood zones clipped with the array. One row per plant; written by the pipeline with the
-- secret key (pipeline/phase_4_geo/load.py), read by allowlisted users. Column list must match
-- pipeline/phase_4_geo/load.py FLOOD_COLUMNS (a test checks).
create table public.layers_flood (
  eia_id             integer primary key references public.plants (eia_id) on delete cascade,
  flood_status       text not null check (flood_status in ('ok', 'not_mapped')),   -- not_mapped = no digital FIRM: unknown, not "no risk"
  nfhl_mapped_share  double precision,   -- share of the array inside a digital NFHL study area
  sfha_share         double precision,   -- share of the array in SFHA zones (A, AE, AH, AO, AR, A99, V, VE)
  sfha_acres         double precision,
  floodway_share     double precision,
  x500_share         double precision,   -- 0.2% annual-chance zone
  zone_d_share       double precision,   -- undetermined hazard
  flood_zones        text,               -- ';'-joined FLD_ZONE values touching the array
  flood_flag         boolean,            -- sfha_share >= layers.flood.flag_sfha_share (brief kill criterion); null when not_mapped
  nfhl_study_ids     text,
  flood_source       text,
  flood_fetched      date,
  flood_confidence   text check (flood_confidence in ('low', 'medium')),
  run_id             text not null,
  loaded_at          timestamptz not null default now()
);

alter table public.layers_flood enable row level security;
create policy "allowlisted users read layers_flood" on public.layers_flood
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.layers_flood is
  'Phase 4 flood layer: share of each array in FEMA NFHL Special Flood Hazard Areas. flood_status = not_mapped means no digital FIRM covers the site (risk unknown).';
